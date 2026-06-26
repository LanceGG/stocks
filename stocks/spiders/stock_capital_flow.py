"""东方财富资金流向爬虫：股票、板块及关联关系。"""

import json
import math
from urllib.parse import urlencode

import scrapy

from stocks.items import (
    SectorItem,
    StockCapitalFlowItem,
    StockItem,
    StockSectorRelItem,
)
from stocks.utils import (
    infer_stock_market,
    infer_trade_status,
    load_sector_name_map,
    parse_trade_date_from_ts,
    split_board_names,
)

# push2 通用列表 API（优先 push2delay，push2 主站易 ConnectionLost）
API_HOSTS = (
    "push2delay.eastmoney.com",
    "82.push2.eastmoney.com",
    "push2.eastmoney.com",
)
API_PATH = "/api/qt/clist/get"
API_UT = "bd1d9ddb04089700cf9c27f6f7426281"

# 沪深京 A 股（含科创板、创业板）
STOCK_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
# 行业板块 / 概念板块
INDUSTRY_FS = "m:90+t:2"
CONCEPT_FS = "m:90+t:3"

STOCK_FIELDS = (
    "f12,f14,f2,f3,f5,f6,f8,f15,f16,f17,f20,f62,f124,f100,f102,f103"
)
SECTOR_FIELDS = "f12,f14,f2,f3,f124"
CONSTITUENT_FIELDS = "f12,f14"


class StockCapitalFlowSpider(scrapy.Spider):
    """抓取股票资金流向、板块信息及股票-板块关联。"""

    name = "stock_capital_flow"
    allowed_domains = [
        "push2delay.eastmoney.com",
        "82.push2.eastmoney.com",
        "push2.eastmoney.com",
        "data.eastmoney.com",
    ]

    custom_settings = {
        "ITEM_PIPELINES": {
            "stocks.pipelines.StockDataPipeline": 300,
        },
        "DOWNLOADER_MIDDLEWARES": {
            "stocks.middlewares.EastMoneyPush2Middleware": 543,
        },
        "DEFAULT_REQUEST_HEADERS": {
            "Referer": "https://data.eastmoney.com/zjlx/list.html",
            "Origin": "https://data.eastmoney.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "close",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        "COOKIES_ENABLED": True,
        "CONCURRENT_REQUESTS": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 2,
        "RANDOMIZE_DOWNLOAD_DELAY": True,
        "DOWNLOAD_TIMEOUT": 30,
        "RETRY_TIMES": 5,
        "RETRY_HTTP_CODES": [500, 502, 503, 504, 408, 429],
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sector_name_map: dict[tuple[str, str], str] = {}
        self._constituent_sectors: list[tuple[str, str, str]] = []
        self._followup_started = False
        self._stock_started = False

    def _get_api_hosts(self) -> list[str]:
        return list(self.settings.getlist("EASTMONEY_PUSH2_HOSTS") or API_HOSTS)

    async def start(self):
        """先访问列表页获取 Cookie，再抓板块列表。"""
        yield scrapy.Request(
            url="https://data.eastmoney.com/zjlx/list.html",
            callback=self.parse_bootstrap,
            dont_filter=True,
        )

    def parse_bootstrap(self, response):
        """引导完成后串行抓取：先行业板块，再概念板块。"""
        page_size = self.settings.getint("STOCK_CAPITAL_FLOW_PAGE_SIZE", 50)
        yield self._build_sector_request("hy", INDUSTRY_FS, page_index=1, page_size=page_size)

    def _build_sector_request(
        self, sector_type: str, fs: str, page_index: int, page_size: int
    ):
        return self._build_request(
            callback=self.parse_sector,
            fs=fs,
            fields=SECTOR_FIELDS,
            page_index=page_index,
            page_size=page_size,
            meta={
                "page_index": page_index,
                "sector_type": sector_type,
                "fs": fs,
                "fields": SECTOR_FIELDS,
            },
            fid="f3",
        )

    def _api_url(self, params: dict, host_idx: int = 0) -> str:
        hosts = self._get_api_hosts()
        host = hosts[host_idx % len(hosts)]
        return f"https://{host}{API_PATH}?{urlencode(params)}"

    def _build_request(
        self,
        callback,
        fs: str,
        fields: str,
        page_index: int,
        page_size: int,
        meta: dict,
        fid: str = "f3",
    ):
        """构建 push2 clist 请求（默认走 push2delay 域名）。"""
        params = {
            "pn": page_index,
            "pz": page_size,
            "po": 1,
            "np": 1,
            "ut": API_UT,
            "fltt": 2,
            "invt": 2,
            "fid": fid,
            "fs": fs,
            "fields": fields,
        }
        host_idx = meta.get("push2_host_idx", 0)
        return scrapy.Request(
            url=self._api_url(params, host_idx),
            callback=callback,
            meta={**meta, "push2_host_idx": host_idx},
            dont_filter=True,
        )

    def parse_sector(self, response):
        """解析行业/概念板块列表，并翻页。"""
        page_index = response.meta["page_index"]
        sector_type = response.meta["sector_type"]
        fs = response.meta["fs"]
        fields = response.meta["fields"]
        page_size = self.settings.getint("STOCK_CAPITAL_FLOW_PAGE_SIZE", 50)

        payload = self._load_json(response)
        if payload is None:
            self.logger.error("板块 API 解析失败: type=%s page=%s", sector_type, page_index)
            return

        data = payload.get("data") or {}
        rows = data.get("diff") or []
        for row in rows:
            sector_code = row.get("f12")
            sector_name = row.get("f14")
            if not sector_code or not sector_name:
                continue

            yield SectorItem(
                sector_code=sector_code,
                sector_name=sector_name,
                sector_type=sector_type,
                latest_price=self._to_decimal(row.get("f2")),
                change_pct=self._to_decimal(row.get("f3")),
            )
            self._sector_name_map[(sector_type, sector_name)] = sector_code
            self._constituent_sectors.append((sector_code, sector_name, sector_type))

        total = int(data.get("total") or 0)
        total_pages = max(1, math.ceil(total / page_size)) if total else 1
        max_pages = self.settings.getint("STOCK_CAPITAL_FLOW_MAX_PAGES", 0)
        target_pages = min(total_pages, max_pages) if max_pages > 0 else total_pages

        self.logger.info(
            "板块 %s page %s/%s, records=%s, total=%s",
            sector_type,
            page_index,
            target_pages,
            len(rows),
            total,
        )

        if page_index < target_pages:
            next_page = page_index + 1
            yield self._build_sector_request(
                sector_type, fs, page_index=next_page, page_size=page_size
            )
        elif sector_type == "hy":
            self.logger.info("行业板块完成，开始概念板块")
            yield self._build_sector_request(
                "gn", CONCEPT_FS, page_index=1, page_size=page_size
            )
        else:
            yield from self._maybe_start_followup(page_size)

    def _maybe_start_followup(self, page_size: int):
        """概念板块完成后，启动股票列表与板块成分股抓取。"""
        if self._followup_started:
            return

        self._followup_started = True
        self.logger.info(
            "板块列表完成，开始股票与成分股抓取，sectors=%s",
            len(self._constituent_sectors),
        )
        if not self._stock_started:
            self._stock_started = True
            yield self._build_request(
                callback=self.parse_stock,
                fs=STOCK_FS,
                fields=STOCK_FIELDS,
                page_index=1,
                page_size=page_size,
                meta={
                    "page_index": 1,
                    "fs": STOCK_FS,
                    "fields": STOCK_FIELDS,
                },
            )
        yield from self._schedule_constituent_requests(page_size)

    def _schedule_constituent_requests(self, page_size: int):
        """按配置上限调度板块成分股请求。"""
        max_sectors = self.settings.getint("SECTOR_CONSTITUENT_MAX_SECTORS", 0)
        sectors = self._constituent_sectors
        if max_sectors > 0:
            sectors = sectors[:max_sectors]

        for sector_code, sector_name, sector_type in sectors:
            yield self._build_request(
                callback=self.parse_constituent,
                fs=f"b:{sector_code}",
                fields=CONSTITUENT_FIELDS,
                page_index=1,
                page_size=page_size,
                meta={
                    "page_index": 1,
                    "sector_code": sector_code,
                    "sector_name": sector_name,
                    "sector_type": sector_type,
                    "fs": f"b:{sector_code}",
                    "fields": CONSTITUENT_FIELDS,
                },
            )

    def parse_constituent(self, response):
        """解析板块成分股，建立 stock_sector_rel。"""
        page_index = response.meta["page_index"]
        sector_code = response.meta["sector_code"]
        sector_type = response.meta["sector_type"]
        fs = response.meta["fs"]
        fields = response.meta["fields"]
        page_size = self.settings.getint("STOCK_CAPITAL_FLOW_PAGE_SIZE", 50)

        payload = self._load_json(response)
        if payload is None:
            self.logger.warning(
                "成分股 API 失败: sector=%s page=%s", sector_code, page_index
            )
            return

        data = payload.get("data") or {}
        rows = data.get("diff") or []
        for row in rows:
            stock_code = row.get("f12")
            stock_name = row.get("f14")
            if not stock_code or not stock_name:
                continue

            yield StockItem(
                stock_code=stock_code,
                stock_name=stock_name,
                market=infer_stock_market(stock_code),
                industry_name=None,
                region_board=None,
            )
            yield StockSectorRelItem(
                stock_code=stock_code,
                sector_code=sector_code,
                sector_type=sector_type,
                source="constituent",
            )

        total = int(data.get("total") or 0)
        total_pages = max(1, math.ceil(total / page_size)) if total else 1

        if page_index < total_pages:
            next_page = page_index + 1
            yield self._build_request(
                callback=self.parse_constituent,
                fs=fs,
                fields=fields,
                page_index=next_page,
                page_size=page_size,
                meta={**response.meta, "page_index": next_page},
            )

    def parse_stock(self, response):
        """解析股票资金流向列表，并尝试按名称匹配概念板块。"""
        page_index = response.meta["page_index"]
        fs = response.meta["fs"]
        fields = response.meta["fields"]
        page_size = self.settings.getint("STOCK_CAPITAL_FLOW_PAGE_SIZE", 50)

        if not self._sector_name_map:
            from stocks.utils import get_mysql_settings

            self._sector_name_map = load_sector_name_map(
                get_mysql_settings(self.settings)
            )

        payload = self._load_json(response)
        if payload is None:
            self.logger.error("股票 API 解析失败: page=%s", page_index)
            return

        data = payload.get("data") or {}
        rows = data.get("diff") or []
        for row in rows:
            stock_code = row.get("f12")
            stock_name = row.get("f14")
            if not stock_code or not stock_name:
                continue

            trade_date = parse_trade_date_from_ts(row.get("f124"))

            industry_name = self._empty_to_none(row.get("f100"))
            region_board = self._empty_to_none(row.get("f102"))

            yield StockItem(
                stock_code=stock_code,
                stock_name=stock_name,
                market=infer_stock_market(stock_code),
                industry_name=industry_name,
                region_board=region_board,
            )

            if trade_date:
                open_price = self._to_decimal(row.get("f15"))
                close_price = self._to_decimal(row.get("f2"))
                high_price = self._to_decimal(row.get("f16"))
                low_price = self._to_decimal(row.get("f17"))
                volume_raw = row.get("f5")
                volume = int(float(volume_raw)) if volume_raw not in (None, "", "-") else None
                quote_probe = {
                    "open_price": open_price,
                    "close_price": close_price,
                    "high_price": high_price,
                    "low_price": low_price,
                    "volume": volume,
                }
                yield StockCapitalFlowItem(
                    stock_code=stock_code,
                    trade_date=trade_date,
                    open_price=open_price,
                    close_price=close_price,
                    high_price=high_price,
                    low_price=low_price,
                    pct_change=self._to_decimal(row.get("f3")),
                    volume=volume,
                    amount=self._to_decimal(row.get("f6")),
                    turnover_rate=self._to_decimal(row.get("f8")),
                    market_cap=self._to_decimal(row.get("f20")),
                    adj_factor=None,
                    close_adj=None,
                    main_net_inflow=self._to_decimal(row.get("f62")),
                    trade_status=infer_trade_status(stock_name, quote_probe),
                )

            # 行业板块名称匹配（f100）
            if industry_name:
                sector_code = self._sector_name_map.get(("hy", industry_name))
                if sector_code:
                    yield StockSectorRelItem(
                        stock_code=stock_code,
                        sector_code=sector_code,
                        sector_type="hy",
                        source="name_match",
                    )

            # 概念板块名称匹配（f103 逗号分隔）
            for concept_name in split_board_names(row.get("f103")):
                sector_code = self._sector_name_map.get(("gn", concept_name))
                if sector_code:
                    yield StockSectorRelItem(
                        stock_code=stock_code,
                        sector_code=sector_code,
                        sector_type="gn",
                        source="name_match",
                    )

        total = int(data.get("total") or 0)
        total_pages = max(1, math.ceil(total / page_size)) if total else 1
        max_pages = self.settings.getint("STOCK_CAPITAL_FLOW_MAX_PAGES", 0)
        target_pages = min(total_pages, max_pages) if max_pages > 0 else total_pages

        self.logger.info(
            "股票 page %s/%s, records=%s, total=%s",
            page_index,
            target_pages,
            len(rows),
            total,
        )

        if page_index < target_pages:
            next_page = page_index + 1
            yield self._build_request(
                callback=self.parse_stock,
                fs=fs,
                fields=fields,
                page_index=next_page,
                page_size=page_size,
                meta={**response.meta, "page_index": next_page},
            )

    @staticmethod
    def _load_json(response):
        """解析 push2 JSON 响应。"""
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            return None
        if payload.get("rc") != 0:
            return None
        return payload

    @staticmethod
    def _empty_to_none(value):
        value = (value or "").strip()
        if not value or value in ("-", "--"):
            return None
        return value

    @staticmethod
    def _to_decimal(value):
        if value in (None, "", "-", "--"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
