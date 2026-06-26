"""基金筛选爬虫：规模、类型、经理、换手率，并在结束时批量计算指标。"""

import json
from urllib.parse import urlencode

import scrapy

from stocks.utils import (
    FundScreeningBatchWriter,
    compute_fund_holding_stats,
    compute_fund_metrics,
    get_mysql_settings,
    load_fund_codes,
    load_synced_fund_screening_codes,
    parse_fund_gmbd_apidata,
    parse_fund_managers,
    parse_fund_profile,
    parse_fund_scale_rows,
    parse_fund_turnover,
)

GMBD_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
PROFILE_URL = (
    "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNDetailInformation"
)
MANAGER_URL = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNMangerList"
TURNOVER_URL = "https://api.fund.eastmoney.com/f10/JJHSL/"

MOBILE_QUERY = {
    "deviceid": "Wap",
    "plat": "Wap",
    "product": "EFund",
    "version": "2.0.0",
}


class FundScreeningSpider(scrapy.Spider):
    """每只基金并行抓取 profile / gmbd / manager / turnover，写入筛选相关表。"""

    name = "fund_screening"
    allowed_domains = [
        "fundf10.eastmoney.com",
        "fundmobapi.eastmoney.com",
        "api.fund.eastmoney.com",
    ]

    custom_settings = {
        "ITEM_PIPELINES": {},
        "DEFAULT_REQUEST_HEADERS": {
            "Referer": "https://fundf10.eastmoney.com/",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        "CONCURRENT_REQUESTS": 16,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 8,
        "DOWNLOAD_DELAY": 0,
        "COOKIES_ENABLED": False,
        "REACTOR_THREADPOOL_MAXSIZE": 20,
        "DOWNLOAD_TIMEOUT": 30,
        "RETRY_TIMES": 5,
        "RETRY_HTTP_CODES": [500, 502, 503, 504, 408, 429],
    }

    @classmethod
    def update_settings(cls, settings):
        super().update_settings(settings)
        settings.set(
            "CONCURRENT_REQUESTS",
            settings.getint("FUND_SCREENING_CONCURRENT_REQUESTS", 16),
            priority="spider",
        )
        settings.set(
            "CONCURRENT_REQUESTS_PER_DOMAIN",
            settings.getint("FUND_SCREENING_CONCURRENT_REQUESTS_PER_DOMAIN", 8),
            priority="spider",
        )
        settings.set(
            "DOWNLOAD_DELAY",
            settings.getfloat("FUND_SCREENING_DOWNLOAD_DELAY", 0),
            priority="spider",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mysql_settings = None
        self._batch_writer: FundScreeningBatchWriter | None = None
        self._pending_crawled = 0

    async def start(self):
        cfg = get_mysql_settings(self.settings)
        self._mysql_settings = cfg
        self._batch_writer = FundScreeningBatchWriter(cfg, self.logger)
        self._gmbd_page_size = self.settings.getint("FUND_SCREENING_GMBD_PAGE_SIZE", 20)
        self._turnover_page_size = self.settings.getint(
            "FUND_SCREENING_TURNOVER_PAGE_SIZE", 20
        )
        skip_synced = self.settings.getbool("FUND_SCREENING_SKIP_SYNCED", True)

        fund_codes = load_fund_codes(cfg)
        if not fund_codes:
            self.logger.warning("fund 表为空，请先运行 scrapy crawl fund")
            return

        max_funds = self.settings.getint("FUND_SCREENING_MAX_FUNDS", 0)
        if max_funds > 0:
            fund_codes = fund_codes[:max_funds]

        synced_codes: set[str] = set()
        if skip_synced:
            synced_codes = load_synced_fund_screening_codes(cfg)
            self.logger.info("已加载 %s 只本地已有筛选数据的基金", len(synced_codes))

        pending = skipped = 0
        for fund_code in fund_codes:
            if skip_synced and fund_code in synced_codes:
                skipped += 1
                continue
            pending += 1
            self._batch_writer.register_fund(fund_code)
            yield self._build_profile_request(fund_code)
            yield self._build_gmbd_request(fund_code, page_index=1)
            yield self._build_manager_request(fund_code)
            yield self._build_turnover_request(fund_code, page_index=1)

        self._pending_crawled = pending
        self.logger.info(
            "筛选爬虫启动：待爬 %s 只，已跳过 %s 只（本地已有数据）",
            pending,
            skipped,
        )

    def _build_profile_request(self, fund_code: str):
        params = {"FCODE": fund_code, **MOBILE_QUERY}
        return scrapy.Request(
            url=f"{PROFILE_URL}?{urlencode(params)}",
            callback=self.parse_profile,
            errback=self._request_errback,
            meta={"fund_code": fund_code, "kind": "profile"},
            dont_filter=True,
        )

    def _build_gmbd_request(self, fund_code: str, page_index: int):
        params = {
            "type": "gmbd",
            "code": fund_code,
            "page": page_index,
            "per": self._gmbd_page_size,
        }
        return scrapy.Request(
            url=f"{GMBD_URL}?{urlencode(params)}",
            callback=self.parse_gmbd,
            errback=self._request_errback,
            meta={"fund_code": fund_code, "page_index": page_index, "kind": "gmbd"},
            headers={"Referer": f"https://fundf10.eastmoney.com/jjjz_{fund_code}.html"},
            dont_filter=True,
        )

    def _build_manager_request(self, fund_code: str):
        params = {"FCODE": fund_code, **MOBILE_QUERY}
        return scrapy.Request(
            url=f"{MANAGER_URL}?{urlencode(params)}",
            callback=self.parse_manager,
            errback=self._request_errback,
            meta={"fund_code": fund_code, "kind": "manager"},
            dont_filter=True,
        )

    def _build_turnover_request(self, fund_code: str, page_index: int, turnover_rows=None):
        params = {
            "fundcode": fund_code,
            "pageindex": page_index,
            "pagesize": self._turnover_page_size,
        }
        return scrapy.Request(
            url=f"{TURNOVER_URL}?{urlencode(params)}",
            callback=self.parse_turnover,
            errback=self._request_errback,
            meta={
                "fund_code": fund_code,
                "page_index": page_index,
                "kind": "turnover",
                "turnover_rows": turnover_rows or [],
            },
            headers={"Referer": f"https://fundf10.eastmoney.com/jjjz_{fund_code}.html"},
            dont_filter=True,
        )

    def _request_errback(self, failure):
        fund_code = failure.request.meta.get("fund_code", "?")
        kind = failure.request.meta.get("kind", "?")
        self.logger.warning("请求失败: fund_code=%s kind=%s", fund_code, kind)
        if self._batch_writer and fund_code != "?":
            self._batch_writer.finish_request(fund_code)

    def parse_profile(self, response):
        fund_code = response.meta["fund_code"]
        try:
            data = json.loads(response.text)
            profile = parse_fund_profile(data, fund_code)
            self._batch_writer.add_profile(fund_code, profile)
        except (json.JSONDecodeError, TypeError) as exc:
            self.logger.warning("profile 解析失败: fund_code=%s %s", fund_code, exc)
        self._batch_writer.finish_request(fund_code)

    def parse_gmbd(self, response):
        fund_code = response.meta["fund_code"]
        page_index = response.meta["page_index"]
        payload = parse_fund_gmbd_apidata(response.text)
        rows = parse_fund_scale_rows(payload.get("content") or "", fund_code)
        if rows:
            self._batch_writer.add_scale_rows(fund_code, rows)

        total_pages = int(payload.get("pages") or 1)
        if page_index < total_pages:
            yield self._build_gmbd_request(fund_code, page_index + 1)
            return

        self._batch_writer.finish_request(fund_code)

    def parse_manager(self, response):
        fund_code = response.meta["fund_code"]
        try:
            data = json.loads(response.text)
            managers, rels = parse_fund_managers(data, fund_code)
            self._batch_writer.add_managers(fund_code, managers, rels)
        except (json.JSONDecodeError, TypeError) as exc:
            self.logger.warning("manager 解析失败: fund_code=%s %s", fund_code, exc)
        self._batch_writer.finish_request(fund_code)

    def parse_turnover(self, response):
        fund_code = response.meta["fund_code"]
        page_index = response.meta["page_index"]
        accumulated = list(response.meta.get("turnover_rows") or [])
        try:
            data = json.loads(response.text)
            rows = parse_fund_turnover(data, fund_code)
            accumulated.extend(rows)

            total = int(data.get("TotalCount") or 0)
            if page_index * self._turnover_page_size < total:
                yield self._build_turnover_request(
                    fund_code, page_index + 1, turnover_rows=accumulated
                )
                return

            if accumulated:
                self._batch_writer.add_operations(fund_code, accumulated)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self.logger.warning("turnover 解析失败: fund_code=%s %s", fund_code, exc)
        self._batch_writer.finish_request(fund_code)

    def closed(self, reason):
        if self._batch_writer:
            self._batch_writer.flush_all()
        if not self._mysql_settings:
            return

        if not self.settings.getbool("FUND_SCREENING_RECOMPUTE_METRICS", True):
            self.logger.info("已关闭指标重算（FUND_SCREENING_RECOMPUTE_METRICS=False）")
            return
        if self._pending_crawled <= 0:
            self.logger.info("无新爬取基金，跳过 fund_metrics / fund_holding_stats 重算")
            return

        self.logger.info("开始计算 fund_metrics …")
        metrics_count = compute_fund_metrics(self._mysql_settings)
        self.logger.info("fund_metrics 写入 %s 条", metrics_count)

        self.logger.info("开始计算 fund_holding_stats …")
        stats_count = compute_fund_holding_stats(self._mysql_settings)
        self.logger.info("fund_holding_stats 写入 %s 条", stats_count)
