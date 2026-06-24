from urllib.parse import urlencode

import scrapy

from stocks.utils import (
    FundBatchWriter,
    FundFilterTracker,
    exclude_filtered_funds,
    filter_discovery_tasks,
    get_mysql_settings,
    load_funds_with_establish_date,
    load_synced_fund_codes,
    parse_bond_holdings,
    parse_f10_apidata,
    parse_stock_holdings,
)

API_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"


class FundHoldingSpider(scrapy.Spider):
    """全量历史持仓爬虫：遍历 fund 表，抓取股票/债券各季度持仓。"""

    name = "fund_holding"
    allowed_domains = ["fundf10.eastmoney.com"]

    custom_settings = {
        "DEFAULT_REQUEST_HEADERS": {
            "Referer": "https://fundf10.eastmoney.com/",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        "ITEM_PIPELINES": {},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mysql_settings = None
        self._historical_synced_funds: set[str] = set()
        self._skip_synced = True
        self._filter_tracker: FundFilterTracker | None = None
        self._batch_writer: FundBatchWriter | None = None

    async def start(self):
        cfg = get_mysql_settings(self.settings)
        self._mysql_settings = cfg
        self._skip_synced = self.settings.getbool("FUND_HOLDING_SKIP_SYNCED", True)
        self._filter_tracker = FundFilterTracker(cfg, self.logger)
        self._batch_writer = FundBatchWriter(
            cfg,
            self.logger,
            on_fund_complete=self._on_fund_crawl_complete,
        )

        if self._skip_synced:
            self._historical_synced_funds = load_synced_fund_codes(cfg)
            self.logger.info(
                "已加载 %s 只历史已有持仓数据的基金", len(self._historical_synced_funds)
            )

        self._filter_tracker.set_has_existing_holdings(self._fund_has_historical_holdings)

        funds = load_funds_with_establish_date(cfg)
        if not funds:
            self.logger.warning("fund 表中没有可爬取的基金代码")
            return

        max_funds = self.settings.getint("FUND_HOLDING_MAX_FUNDS", 0)
        if max_funds > 0:
            funds = funds[:max_funds]

        before_filter = len(funds)
        funds = exclude_filtered_funds(funds, self._filter_tracker.filtered_codes)
        if before_filter > len(funds):
            self.logger.info(
                "已跳过 %s 只 fund_filter 表中的基金",
                before_filter - len(funds),
            )

        discovery_tasks = filter_discovery_tasks(
            funds=funds,
            synced_fund_codes=self._historical_synced_funds,
            skip_synced=self._skip_synced,
        )
        for fund_code, holding_type in discovery_tasks:
            self._filter_tracker.register_task(fund_code, holding_type)

        pending_funds = len({fund_code for fund_code, _ in discovery_tasks})
        skipped_funds = len(funds) - pending_funds
        self.logger.info(
            "准备爬取 %s 只基金（%s 个任务），已跳过 %s 只（历史已有持仓数据）",
            pending_funds,
            len(discovery_tasks),
            skipped_funds,
        )

        for fund_code, holding_type in discovery_tasks:
            yield self._build_request(
                fund_code=fund_code,
                holding_type=holding_type,
                year="",
                month="",
                callback=self.parse_years,
            )

    def closed(self, reason):
        if self._batch_writer:
            self._batch_writer.flush_all()
        if self._filter_tracker:
            self._filter_tracker.flush()

    def _on_fund_crawl_complete(self, fund_code: str, *, wrote_holdings: bool = False) -> None:
        if self._filter_tracker:
            self._filter_tracker.try_save_fund(fund_code, wrote_holdings=wrote_holdings)

    def _fund_has_historical_holdings(self, fund_code: str) -> bool:
        return fund_code in self._historical_synced_funds

    def _request_errback(self, failure):
        fund_code = failure.request.meta["fund_code"]
        self.logger.warning("请求失败: fund_code=%s url=%s", fund_code, failure.request.url)
        self._batch_writer.finish_request(fund_code)

    def _build_request(
        self,
        fund_code: str,
        holding_type: str,
        year: str = "",
        month: str = "",
        callback=None,
    ):
        if holding_type == "stock":
            params = {
                "type": "jjcc",
                "code": fund_code,
                "topline": str(self.settings.getint("FUND_HOLDING_TOPLINE", 9999)),
                "year": year,
                "month": month,
                "rt": "0.1",
            }
            referer = f"https://fundf10.eastmoney.com/ccmx_{fund_code}.html"
        else:
            params = {
                "type": "zqcc",
                "code": fund_code,
                "year": year,
                "rt": "0.1",
            }
            referer = f"https://fundf10.eastmoney.com/ccmx1_{fund_code}.html"

        self._batch_writer.register_request(fund_code)
        return scrapy.Request(
            url=f"{API_URL}?{urlencode(params)}",
            callback=callback or self.parse_holdings,
            errback=self._request_errback,
            meta={
                "fund_code": fund_code,
                "holding_type": holding_type,
                "year": year,
            },
            headers={"Referer": referer},
        )

    def parse_years(self, response):
        fund_code = response.meta["fund_code"]
        holding_type = response.meta["holding_type"]
        try:
            payload = parse_f10_apidata(response.text)
            years = payload.get("arryear") or []
            if not years:
                self.logger.info(
                    "无%s持仓历史: fund_code=%˚s",
                    "债券" if holding_type == "bond" else "股票",
                    fund_code,
                )
                self._filter_tracker.record_empty_type(fund_code, holding_type)
                return

            self._filter_tracker.record_types_with_years(fund_code, holding_type)
            self.logger.info(
                "fund_code=%s type=%s, 待同步年份=%s",
                fund_code,
                holding_type,
                years,
            )
            for year in years:
                if holding_type == "stock":
                    yield self._build_request(
                        fund_code=fund_code,
                        holding_type="stock",
                        year=str(year),
                        month="1",
                        callback=self.parse_holdings,
                    )
                else:
                    yield self._build_request(
                        fund_code=fund_code,
                        holding_type="bond",
                        year=str(year),
                        callback=self.parse_holdings,
                    )
        finally:
            self._batch_writer.finish_request(fund_code)

    def parse_holdings(self, response):
        fund_code = response.meta["fund_code"]
        holding_type = response.meta["holding_type"]
        year = response.meta["year"]
        try:
            payload = parse_f10_apidata(response.text)
            content = payload.get("content") or ""
            if not content or "<tbody>" not in content:
                self.logger.debug(
                    "持仓内容为空: fund_code=%s type=%s year=%s",
                    fund_code,
                    holding_type,
                    year,
                )
                return

            if holding_type == "bond":
                holdings = parse_bond_holdings(content, fund_code)
            else:
                holdings = parse_stock_holdings(content, fund_code)

            if not holdings:
                self.logger.debug(
                    "未解析到持仓记录: fund_code=%s type=%s year=%s",
                    fund_code,
                    holding_type,
                    year,
                )
                return

            self._filter_tracker.record_has_holdings(fund_code, holding_type)
            self.logger.info(
                "fund_code=%s type=%s year=%s, 持仓条数=%s",
                fund_code,
                holding_type,
                year,
                len(holdings),
            )
            self._batch_writer.add_holdings(fund_code, holdings)
        finally:
            self._batch_writer.finish_request(fund_code)
