from urllib.parse import urlencode

import scrapy

from stocks.utils import (
    FundBatchWriter,
    exclude_filtered_funds,
    expected_latest_report_date,
    filter_current_holding_tasks,
    get_mysql_settings,
    keep_latest_quarter,
    load_filtered_fund_codes,
    load_funds_with_establish_date,
    load_synced_current_quarter_funds,
    parse_bond_holdings,
    parse_f10_apidata,
    parse_stock_holdings,
)

API_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"


class FundHoldingCurrentSpider(scrapy.Spider):
    """只爬取各基金当前最新季度的股票/债券持仓。"""

    name = "fund_holding_current"
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
        self._historical_current_quarter_funds: set[str] = set()
        self._skip_synced = True
        self._batch_writer: FundBatchWriter | None = None

    async def start(self):
        cfg = get_mysql_settings(self.settings)
        self._mysql_settings = cfg
        self._skip_synced = self.settings.getbool("FUND_HOLDING_SKIP_SYNCED", True)
        self._batch_writer = FundBatchWriter(cfg, self.logger)

        expected_report_date = expected_latest_report_date()
        if self._skip_synced:
            self._historical_current_quarter_funds = load_synced_current_quarter_funds(
                cfg, expected_report_date
            )
            self.logger.info(
                "已加载 %s 只报告期 %s 历史已有持仓数据的基金",
                len(self._historical_current_quarter_funds),
                expected_report_date,
            )

        funds = load_funds_with_establish_date(cfg)
        if not funds:
            self.logger.warning("fund 表中没有可爬取的基金代码")
            return

        max_funds = self.settings.getint("FUND_HOLDING_CURRENT_MAX_FUNDS", 0)
        if max_funds <= 0:
            max_funds = self.settings.getint("FUND_HOLDING_MAX_FUNDS", 0)
        if max_funds > 0:
            funds = funds[:max_funds]

        filtered_codes = load_filtered_fund_codes(cfg)
        before_filter = len(funds)
        funds = exclude_filtered_funds(funds, filtered_codes)
        if before_filter > len(funds):
            self.logger.info(
                "已跳过 %s 只 fund_filter 表中的基金",
                before_filter - len(funds),
            )

        pending_tasks = filter_current_holding_tasks(
            funds=funds,
            synced_fund_codes=self._historical_current_quarter_funds,
            skip_synced=self._skip_synced,
        )
        pending_funds = len({fund_code for fund_code, _ in pending_tasks})
        skipped_funds = len(funds) - pending_funds
        self.logger.info(
            "准备爬取 %s 只基金（%s 个任务），已跳过 %s 只（历史报告期 %s 已有数据）",
            pending_funds,
            len(pending_tasks),
            skipped_funds,
            expected_report_date,
        )

        for fund_code, holding_type in pending_tasks:
            yield self._build_request(fund_code, holding_type)

    def closed(self, reason):
        if self._batch_writer:
            self._batch_writer.flush_all()

    def _request_errback(self, failure):
        fund_code = failure.request.meta["fund_code"]
        self.logger.warning("请求失败: fund_code=%s url=%s", fund_code, failure.request.url)
        self._batch_writer.finish_request(fund_code)

    def _build_request(self, fund_code: str, holding_type: str):
        if holding_type == "stock":
            params = {
                "type": "jjcc",
                "code": fund_code,
                "topline": str(self.settings.getint("FUND_HOLDING_TOPLINE", 9999)),
                "year": "",
                "month": "",
                "rt": "0.1",
            }
            referer = f"https://fundf10.eastmoney.com/ccmx_{fund_code}.html"
        else:
            params = {
                "type": "zqcc",
                "code": fund_code,
                "year": "",
                "rt": "0.1",
            }
            referer = f"https://fundf10.eastmoney.com/ccmx1_{fund_code}.html"

        self._batch_writer.register_request(fund_code)
        return scrapy.Request(
            url=f"{API_URL}?{urlencode(params)}",
            callback=self.parse_holdings,
            errback=self._request_errback,
            meta={"fund_code": fund_code, "holding_type": holding_type},
            headers={"Referer": referer},
        )

    def parse_holdings(self, response):
        fund_code = response.meta["fund_code"]
        holding_type = response.meta["holding_type"]
        try:
            payload = parse_f10_apidata(response.text)
            content = payload.get("content") or ""
            if not content or "<tbody>" not in content:
                self.logger.debug(
                    "当前季度持仓为空: fund_code=%s type=%s",
                    fund_code,
                    holding_type,
                )
                return

            if holding_type == "bond":
                holdings = parse_bond_holdings(content, fund_code)
            else:
                holdings = parse_stock_holdings(content, fund_code)

            holdings = keep_latest_quarter(holdings)
            if not holdings:
                self.logger.debug(
                    "未解析到当前季度持仓: fund_code=%s type=%s",
                    fund_code,
                    holding_type,
                )
                return

            sample = holdings[0]
            self.logger.info(
                "fund_code=%s type=%s, 报告期=%s Q%s, 持仓条数=%s",
                fund_code,
                holding_type,
                sample["report_date"],
                sample["report_quarter"],
                len(holdings),
            )
            self._batch_writer.add_holdings(fund_code, holdings)
        finally:
            self._batch_writer.finish_request(fund_code)
