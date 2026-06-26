"""基金历史净值爬虫：东方财富 F10 lsjz 接口，写入 fund_nav。"""

from urllib.parse import urlencode

import scrapy

from stocks.utils import (
    approx_latest_trade_date,
    bulk_save_fund_nav,
    filter_fund_nav_rows,
    get_mysql_settings,
    load_fund_nav_sync_bounds,
    load_funds_with_establish_date,
    parse_fund_nav_apidata,
    parse_fund_nav_rows,
)

API_URL = "https://fundf10.eastmoney.com/F10DataApi.aspx"


class FundNavSpider(scrapy.Spider):
    """抓取 fund 表基金的历史日净值，写入 fund_nav。

    - 全量回填：无历史数据或 FUND_NAV_FULL_BACKFILL=True 时分页抓取
    - 日增量：已有历史时只抓第 1 页新净值
    """

    name = "fund_nav"
    allowed_domains = ["fundf10.eastmoney.com"]

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
            settings.getint("FUND_NAV_CONCURRENT_REQUESTS", 16),
            priority="spider",
        )
        settings.set(
            "CONCURRENT_REQUESTS_PER_DOMAIN",
            settings.getint("FUND_NAV_CONCURRENT_REQUESTS_PER_DOMAIN", 8),
            priority="spider",
        )
        settings.set(
            "DOWNLOAD_DELAY",
            settings.getfloat("FUND_NAV_DOWNLOAD_DELAY", 0),
            priority="spider",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mysql_settings = None
        self._since_date = "2016-01-01"
        self._sync_bounds: dict[str, tuple[str, str]] = {}
        self._expected_latest = ""

    async def start(self):
        cfg = get_mysql_settings(self.settings)
        self._mysql_settings = cfg
        self._since_date = self.settings.get("FUND_NAV_START_DATE", "2016-01-01")
        self._page_size = self.settings.getint("FUND_NAV_PAGE_SIZE", 49)
        self._skip_synced = self.settings.getbool("FUND_NAV_SKIP_SYNCED", True)
        self._full_backfill = self.settings.getbool("FUND_NAV_FULL_BACKFILL", False)
        self._expected_latest = approx_latest_trade_date()

        funds = load_funds_with_establish_date(cfg)
        if not funds:
            self.logger.warning("fund 表为空，请先运行 scrapy crawl fund")
            return

        max_funds = self.settings.getint("FUND_NAV_MAX_FUNDS", 0)
        if max_funds > 0:
            funds = funds[:max_funds]

        if self._skip_synced or not self._full_backfill:
            self._sync_bounds = load_fund_nav_sync_bounds(cfg, self._since_date)

        pending = skipped = 0
        for fund_code, _ in funds:
            mode = self._crawl_mode(fund_code)
            if mode == "skip":
                skipped += 1
                continue
            pending += 1
            yield self._build_request(
                fund_code=fund_code,
                page_index=1,
                full_backfill=(mode == "full"),
            )

        self.logger.info(
            "净值抓取 %s 起：待爬 %s 只，已跳过 %s 只（期望最新 %s）",
            self._since_date,
            pending,
            skipped,
            self._expected_latest,
        )

    def _crawl_mode(self, fund_code: str) -> str:
        """返回 skip / incremental / full。"""
        if self._full_backfill:
            return "full"

        bounds = self._sync_bounds.get(fund_code)
        if not bounds:
            return "full"

        earliest, latest = bounds
        if self._skip_synced and latest >= self._expected_latest and earliest <= self._since_date:
            return "skip"
        if earliest > self._since_date:
            return "full"
        return "incremental"

    def _build_request(self, fund_code: str, page_index: int, full_backfill: bool):
        params = {
            "type": "lsjz",
            "code": fund_code,
            "page": page_index,
            "per": self._page_size,
        }
        bounds = self._sync_bounds.get(fund_code)
        latest_known = bounds[1] if bounds else None
        return scrapy.Request(
            url=f"{API_URL}?{urlencode(params)}",
            callback=self.parse_nav,
            errback=self._request_errback,
            meta={
                "fund_code": fund_code,
                "page_index": page_index,
                "full_backfill": full_backfill,
                "latest_known": latest_known,
            },
            headers={"Referer": f"https://fundf10.eastmoney.com/jjjz_{fund_code}.html"},
            dont_filter=True,
        )

    def _request_errback(self, failure):
        fund_code = failure.request.meta.get("fund_code", "?")
        self.logger.warning(
            "净值请求失败: fund_code=%s page=%s",
            fund_code,
            failure.request.meta.get("page_index"),
        )

    def parse_nav(self, response):
        fund_code = response.meta["fund_code"]
        page_index = response.meta["page_index"]
        full_backfill = response.meta["full_backfill"]
        latest_known = response.meta["latest_known"]

        payload = parse_fund_nav_apidata(response.text)
        rows = parse_fund_nav_rows(payload.get("content") or "", fund_code)
        if not rows:
            self.logger.debug("无净值数据: fund_code=%s page=%s", fund_code, page_index)
            return

        save_rows = filter_fund_nav_rows(rows, self._since_date, latest_known)
        if save_rows:
            count = bulk_save_fund_nav(self._mysql_settings, save_rows)
            self.logger.info(
                "fund_code=%s page=%s 写入 %s 条", fund_code, page_index, count
            )

        total_pages = int(payload.get("pages") or 1)
        if not full_backfill or page_index >= total_pages:
            return

        min_date = min(row["nav_date"] for row in rows)
        if min_date <= self._since_date:
            return

        yield self._build_request(
            fund_code=fund_code,
            page_index=page_index + 1,
            full_backfill=True,
        )
