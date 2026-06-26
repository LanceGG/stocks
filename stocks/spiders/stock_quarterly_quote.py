"""股票日 K 爬虫：新浪日 K + 腾讯后复权，批量写入 stock_capital_flow。"""

import json
from datetime import date
from urllib.parse import quote, urlencode

import scrapy

from stocks.utils import (
    approx_latest_trade_date,
    build_enriched_stock_quotes,
    build_sina_symbol,
    bulk_save_stock_quotes,
    get_mysql_settings,
    load_stock_names,
    load_stock_quote_sync_bounds,
    load_stocks_from_db,
    parse_sina_kline_row,
    parse_tencent_hfq_rows,
)

SINA_KLINE_API = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)
TENCENT_FQKLINE_API = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


class StockQuarterlyQuoteSpider(scrapy.Spider):
    """抓取指定起始日（默认 2016-01-01）起每个交易日的行情，写入 stock_capital_flow。"""

    name = "stock_quarterly_quote"
    allowed_domains = ["money.finance.sina.com.cn", "web.ifzq.gtimg.cn"]

    custom_settings = {
        "ITEM_PIPELINES": {},
        "DEFAULT_REQUEST_HEADERS": {
            "Referer": "https://finance.sina.com.cn/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "close",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        "COOKIES_ENABLED": False,
        "CONCURRENT_REQUESTS": 32,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 16,
        "DOWNLOAD_DELAY": 0,
        "REACTOR_THREADPOOL_MAXSIZE": 32,
        "DOWNLOAD_TIMEOUT": 30,
        "RETRY_TIMES": 5,
        "RETRY_HTTP_CODES": [500, 502, 503, 504, 408, 429],
    }

    @classmethod
    def update_settings(cls, settings):
        super().update_settings(settings)
        settings.set(
            "CONCURRENT_REQUESTS",
            settings.getint("STOCK_QUOTE_CONCURRENT_REQUESTS", 32),
            priority="spider",
        )
        settings.set(
            "CONCURRENT_REQUESTS_PER_DOMAIN",
            settings.getint("STOCK_QUOTE_CONCURRENT_REQUESTS_PER_DOMAIN", 16),
            priority="spider",
        )
        settings.set(
            "DOWNLOAD_DELAY",
            settings.getfloat("STOCK_QUOTE_DOWNLOAD_DELAY", 0),
            priority="spider",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._since_date = "2016-01-01"
        self._mysql_settings = None
        self._latest_trade_date = ""
        self._sync_bounds: dict[str, tuple[str, str]] = {}
        self._stock_names: dict[str, str] = {}

    async def start(self):
        cfg = get_mysql_settings(self.settings)
        self._mysql_settings = cfg
        self._since_date = self.settings.get("STOCK_QUOTE_START_DATE", "2016-01-01")
        self._kline_datalen = self.settings.getint("STOCK_QUOTE_KLINE_DATALEN", 4000)
        self._hfq_bar_count = self.settings.getint("STOCK_QUOTE_HFQ_BAR_COUNT", 2000)
        self._end_date = date.today().isoformat()
        skip_synced = self.settings.getbool("STOCK_QUOTE_SKIP_SYNCED", True)
        self._latest_trade_date = approx_latest_trade_date()
        self._stock_names = load_stock_names(cfg)

        stocks = load_stocks_from_db(cfg)
        if not stocks:
            self.logger.warning(
                "stock 表为空，请先运行 scrapy crawl stock_capital_flow 同步股票列表"
            )
            return

        max_stocks = self.settings.getint("STOCK_QUOTE_MAX_STOCKS", 0)
        if max_stocks > 0:
            stocks = stocks[:max_stocks]

        if skip_synced:
            self._sync_bounds = load_stock_quote_sync_bounds(cfg, self._since_date)

        pending = skipped = 0
        for stock_code, market in stocks:
            if skip_synced and self._is_fully_synced(stock_code):
                skipped += 1
                continue
            pending += 1
            symbol = build_sina_symbol(stock_code, market)
            yield self._build_sina_kline_request(
                symbol=symbol,
                meta={"stock_code": stock_code, "market": market, "symbol": symbol},
            )

        self.logger.info(
            "抓取 %s 起日 K：待爬 %s 只，已跳过 %s 只（已同步至 %s）",
            self._since_date,
            pending,
            skipped,
            self._latest_trade_date,
        )

    def _is_fully_synced(self, stock_code: str) -> bool:
        bounds = self._sync_bounds.get(stock_code)
        if not bounds:
            return False
        earliest, latest = bounds
        return earliest <= self._since_date and latest >= self._latest_trade_date

    def _build_sina_kline_request(self, symbol: str, meta: dict):
        params = {
            "symbol": symbol,
            "scale": "240",
            "ma": "no",
            "datalen": str(self._kline_datalen),
        }
        return scrapy.Request(
            url=f"{SINA_KLINE_API}?{urlencode(params)}",
            callback=self.parse_sina_kline,
            errback=self._request_errback,
            meta=meta,
            dont_filter=True,
        )

    def _build_tencent_hfq_request(self, symbol: str, meta: dict):
        param = f"{symbol},day,{self._since_date},{self._end_date},{self._hfq_bar_count},hfq"
        return scrapy.Request(
            url=f"{TENCENT_FQKLINE_API}?param={quote(param, safe='')}",
            callback=self.parse_tencent_hfq,
            errback=self._request_errback,
            meta=meta,
            headers={"Referer": "https://finance.qq.com/"},
            dont_filter=True,
        )

    def _request_errback(self, failure):
        stock_code = failure.request.meta.get("stock_code", "?")
        self.logger.warning(
            "行情请求失败: stock_code=%s url=%s",
            stock_code,
            failure.request.url,
        )

    def parse_sina_kline(self, response):
        stock_code = response.meta["stock_code"]
        symbol = response.meta["symbol"]
        try:
            rows = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.warning("新浪 K 线解析失败: stock_code=%s", stock_code)
            return
        if not isinstance(rows, list) or not rows:
            self.logger.debug("无 K 线: stock_code=%s", stock_code)
            return

        day_rows = []
        for row in rows:
            parsed = parse_sina_kline_row(row)
            if parsed:
                day_rows.append(parsed)
        if not day_rows:
            return

        yield self._build_tencent_hfq_request(
            symbol=symbol,
            meta={**response.meta, "day_rows": day_rows},
        )

    def parse_tencent_hfq(self, response):
        stock_code = response.meta["stock_code"]
        symbol = response.meta["symbol"]
        day_rows = response.meta["day_rows"]

        hfq_by_date: dict[str, list] = {}
        try:
            payload = json.loads(response.text)
            hfq_by_date = parse_tencent_hfq_rows(payload, symbol)
        except json.JSONDecodeError:
            self.logger.warning("腾讯后复权解析失败: stock_code=%s", stock_code)

        quote_rows = build_enriched_stock_quotes(
            stock_code=stock_code,
            day_rows=day_rows,
            hfq_by_date=hfq_by_date,
            stock_name=self._stock_names.get(stock_code),
            since_date=self._since_date,
        )
        if not quote_rows:
            return

        count = bulk_save_stock_quotes(self._mysql_settings, quote_rows)
        self.logger.info("stock_code=%s 批量写入 %s 条", stock_code, count)
