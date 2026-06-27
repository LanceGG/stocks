"""股票日 K 爬虫：新浪不复权 + 前复权（默认东方财富 push2his，可选腾讯）。"""

import json
from datetime import date
from urllib.parse import quote, urlencode

import scrapy

from stocks.utils import (
    approx_latest_trade_date,
    build_eastmoney_kline_url,
    build_enriched_stock_quotes,
    build_qfq_date_chunks,
    build_quotes_qfq_only,
    build_quotes_sina_only,
    build_sina_symbol,
    bulk_save_stock_quotes,
    get_mysql_settings,
    is_quote_earliest_sufficient,
    load_stock_names,
    load_stock_quote_sync_bounds,
    load_stocks_from_db,
    load_unadjusted_quotes_from_db,
    parse_eastmoney_qfq_rows,
    parse_sina_kline_row,
    parse_tencent_fq_rows,
)

SINA_KLINE_API = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)
TENCENT_FQKLINE_API = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


class StockQuarterlyQuoteSpider(scrapy.Spider):
    """抓取日 K 写入 stock_capital_flow；通过 FETCH_SINA / FETCH_QFQ 控制数据源。"""

    name = "stock_quarterly_quote"
    allowed_domains = [
        "money.finance.sina.com.cn",
        "web.ifzq.gtimg.cn",
        "push2his.eastmoney.com",
        "82.push2his.eastmoney.com",
    ]

    custom_settings = {
        "ITEM_PIPELINES": {},
        "DOWNLOADER_MIDDLEWARES": {
            "stocks.middlewares.EastMoneyPush2Middleware": 543,
        },
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
        "CONCURRENT_REQUESTS": 8,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "DOWNLOAD_DELAY": 0.3,
        "REACTOR_THREADPOOL_MAXSIZE": 16,
        "DOWNLOAD_TIMEOUT": 60,
        "RETRY_TIMES": 5,
        "RETRY_HTTP_CODES": [500, 502, 503, 504, 408, 429],
    }

    @classmethod
    def update_settings(cls, settings):
        super().update_settings(settings)
        settings.set(
            "CONCURRENT_REQUESTS",
            settings.getint("STOCK_QUOTE_CONCURRENT_REQUESTS", 4),
            priority="spider",
        )
        settings.set(
            "CONCURRENT_REQUESTS_PER_DOMAIN",
            settings.getint("STOCK_QUOTE_CONCURRENT_REQUESTS_PER_DOMAIN", 2),
            priority="spider",
        )
        settings.set(
            "DOWNLOAD_DELAY",
            settings.getfloat("STOCK_QUOTE_DOWNLOAD_DELAY", 0.3),
            priority="spider",
        )
        settings.set(
            "DOWNLOAD_TIMEOUT",
            settings.getint("STOCK_QUOTE_DOWNLOAD_TIMEOUT", 60),
            priority="spider",
        )
        # 腾讯 qfq 分段慢，单独降并发；东方财富一次拉全量无需
        qfq_src = settings.get("STOCK_QUOTE_QFQ_SOURCE", "eastmoney").lower()
        if (
            qfq_src == "tencent"
            and settings.getbool("STOCK_QUOTE_FETCH_QFQ", True)
            and not settings.getbool("STOCK_QUOTE_FETCH_SINA", True)
        ):
            qfq_conc = settings.getint("STOCK_QUOTE_QFQ_CONCURRENT_REQUESTS", 2)
            settings.set("CONCURRENT_REQUESTS", qfq_conc, priority="spider")
            settings.set("CONCURRENT_REQUESTS_PER_DOMAIN", qfq_conc, priority="spider")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._since_date = "2016-01-01"
        self._mysql_settings = None
        self._latest_trade_date = ""
        self._sync_bounds: dict[str, tuple[str, str, str | None, str | None]] = {}
        self._stock_names: dict[str, str] = {}
        self._qfq_chunks: list[tuple[str, str]] = []
        self._fetch_sina = True
        self._fetch_qfq = True
        self._fetch_hfq = False
        self._qfq_source = "eastmoney"

    async def start(self):
        cfg = get_mysql_settings(self.settings)
        self._mysql_settings = cfg
        self._since_date = self.settings.get("STOCK_QUOTE_START_DATE", "2016-01-01")
        self._fetch_sina = self.settings.getbool("STOCK_QUOTE_FETCH_SINA", True)
        self._fetch_qfq = self.settings.getbool("STOCK_QUOTE_FETCH_QFQ", True)
        self._fetch_hfq = self.settings.getbool("STOCK_QUOTE_FETCH_HFQ", False)
        self._qfq_source = self.settings.get("STOCK_QUOTE_QFQ_SOURCE", "eastmoney").lower()
        if self._fetch_hfq and self._qfq_source != "tencent":
            self.logger.warning("STOCK_QUOTE_FETCH_HFQ 仅支持 tencent 源，已忽略")
            self._fetch_hfq = False

        if not self._fetch_sina and not self._fetch_qfq and not self._fetch_hfq:
            self.logger.error("STOCK_QUOTE_FETCH_SINA/QFQ/HFQ 至少开启一项")
            return

        self._kline_datalen = self.settings.getint("STOCK_QUOTE_KLINE_DATALEN", 4000)
        raw_qfq = self.settings.getint(
            "STOCK_QUOTE_QFQ_BAR_COUNT",
            self.settings.getint("STOCK_QUOTE_HFQ_BAR_COUNT", 2000),
        )
        if raw_qfq > 2000:
            self.logger.warning(
                "STOCK_QUOTE_QFQ_BAR_COUNT=%s 超过腾讯接口上限 2000，已降级为 2000",
                raw_qfq,
            )
        self._qfq_bar_count = min(raw_qfq, 2000)
        self._end_date = date.today().isoformat()
        chunk_days = self.settings.getint("STOCK_QUOTE_QFQ_CHUNK_DAYS", 700)
        self._qfq_chunks = build_qfq_date_chunks(
            self._since_date, self._end_date, chunk_days=chunk_days
        )
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
            meta = {"stock_code": stock_code, "market": market, "symbol": symbol}
            for req in self._start_stock_requests(symbol, meta):
                yield req

        src = []
        if self._fetch_sina:
            src.append("新浪")
        if self._fetch_qfq:
            qfq_label = "东财qfq" if self._qfq_source == "eastmoney" else "腾讯qfq"
            src.append(qfq_label)
        if self._fetch_hfq:
            src.append("腾讯hfq")
        chunk_info = (
            "1 次/股"
            if self._fetch_qfq and self._qfq_source == "eastmoney"
            else f"{len(self._qfq_chunks)} 段/股"
        )
        self.logger.info(
            "抓取 %s 起 [%s]：待爬 %s 只，已跳过 %s 只，qfq %s",
            self._since_date,
            "+".join(src),
            pending,
            skipped,
            chunk_info if self._fetch_qfq else "-",
        )

    def _start_stock_requests(self, symbol: str, meta: dict):
        if self._fetch_sina:
            yield self._build_sina_kline_request(symbol=symbol, meta=meta)
            return
        if self._fetch_qfq:
            for req in self._start_qfq_chain(symbol, meta, day_rows=[]):
                yield req

    def _start_qfq_chain(self, symbol: str, meta: dict, day_rows: list):
        if self._qfq_source == "eastmoney":
            yield self._build_eastmoney_qfq_request(meta, day_rows)
            return
        if not self._qfq_chunks:
            self.logger.error("stock_code=%s 前复权日期分段为空", meta["stock_code"])
            return
        chunk_start, chunk_end = self._qfq_chunks[0]
        yield self._build_tencent_fq_request(
            symbol=symbol,
            meta={
                **meta,
                "day_rows": day_rows,
                "qfq_chunk_idx": 0,
                "qfq_by_date": {},
            },
            fq_type="qfq",
            chunk_start=chunk_start,
            chunk_end=chunk_end,
        )

    def _is_fully_synced(self, stock_code: str) -> bool:
        bounds = self._sync_bounds.get(stock_code)
        if not bounds:
            return False
        earliest, latest, qfq_earliest, qfq_latest = bounds

        if self._fetch_sina:
            if not is_quote_earliest_sufficient(earliest, self._since_date):
                return False
            if latest < self._latest_trade_date:
                return False

        if self._fetch_qfq:
            if not qfq_earliest or not is_quote_earliest_sufficient(
                qfq_earliest, self._since_date
            ):
                return False
            if not qfq_latest or qfq_latest < self._latest_trade_date:
                return False

        return True

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

    def _build_eastmoney_qfq_request(self, meta: dict, day_rows: list):
        url = build_eastmoney_kline_url(
            meta["stock_code"],
            meta.get("market"),
            self._since_date,
            self._end_date,
            fqt=1,
        )
        return scrapy.Request(
            url=url,
            callback=self.parse_eastmoney_qfq,
            errback=self._request_errback,
            meta={**meta, "day_rows": day_rows},
            headers={
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
            dont_filter=True,
        )

    def parse_eastmoney_qfq(self, response):
        stock_code = response.meta["stock_code"]
        day_rows = response.meta.get("day_rows") or []
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.warning("东方财富前复权解析失败: stock_code=%s", stock_code)
            return

        qfq_by_date = parse_eastmoney_qfq_rows(payload)
        if not qfq_by_date:
            self.logger.warning("东方财富前复权空响应: stock_code=%s", stock_code)
            return

        self._save_quotes(stock_code, day_rows, qfq_by_date, hfq_by_date=None)

    def _build_tencent_fq_request(
        self,
        symbol: str,
        meta: dict,
        fq_type: str,
        chunk_start: str | None = None,
        chunk_end: str | None = None,
    ):
        start = chunk_start or self._since_date
        end = chunk_end or self._end_date
        param = f"{symbol},day,{start},{end},{self._qfq_bar_count},{fq_type}"
        callback = (
            self.parse_tencent_qfq if fq_type == "qfq" else self.parse_tencent_hfq
        )
        return scrapy.Request(
            url=f"{TENCENT_FQKLINE_API}?param={quote(param, safe='')}",
            callback=callback,
            errback=self._request_errback,
            meta={**meta, "fq_type": fq_type, "chunk_start": start, "chunk_end": end},
            headers={"Referer": "https://finance.qq.com/"},
            dont_filter=True,
        )

    def _request_errback(self, failure):
        request = failure.request
        stock_code = request.meta.get("stock_code", "?")
        fq_type = request.meta.get("fq_type")
        retries = request.meta.get("qfq_retry", 0)
        max_retries = self.settings.getint("STOCK_QUOTE_QFQ_MAX_RETRIES", 5)

        if fq_type == "qfq" and retries < max_retries:
            self.logger.warning(
                "腾讯前复权请求失败，重试 %s/%s: stock_code=%s",
                retries + 1,
                max_retries,
                stock_code,
            )
            return request.replace(
                meta={**request.meta, "qfq_retry": retries + 1},
                dont_filter=True,
            )

        if fq_type == "qfq":
            self.logger.warning(
                "腾讯前复权段失败，尝试下一段: stock_code=%s url=%s",
                stock_code,
                request.url[:100],
            )
            next_req = self._next_qfq_chunk_request(request.meta, after_failure=True)
            if next_req:
                return next_req
            self._save_quotes(
                stock_code,
                request.meta.get("day_rows") or [],
                request.meta.get("qfq_by_date") or {},
                hfq_by_date=None,
            )
            return

        self.logger.warning(
            "行情请求失败: stock_code=%s url=%s",
            stock_code,
            request.url,
        )

    def _next_qfq_chunk_request(self, meta: dict, after_failure: bool = False):
        """当前 qfq 段结束或失败后，调度下一段；全部完成则返回 None。"""
        chunk_idx = meta.get("qfq_chunk_idx", 0)
        if after_failure:
            next_idx = chunk_idx + 1
        else:
            next_idx = chunk_idx + 1
        if next_idx >= len(self._qfq_chunks):
            return None
        chunk_start, chunk_end = self._qfq_chunks[next_idx]
        return self._build_tencent_fq_request(
            symbol=meta["symbol"],
            meta={
                **meta,
                "qfq_chunk_idx": next_idx,
                "qfq_empty_retry": 0,
                "qfq_retry": 0,
            },
            fq_type="qfq",
            chunk_start=chunk_start,
            chunk_end=chunk_end,
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

        if not self._fetch_qfq:
            self._save_quotes(stock_code, day_rows, qfq_by_date={}, hfq_by_date=None)
            return

        for req in self._start_qfq_chain(symbol, response.meta, day_rows=day_rows):
            yield req

    def parse_tencent_qfq(self, response):
        stock_code = response.meta["stock_code"]
        symbol = response.meta["symbol"]
        day_rows = response.meta.get("day_rows") or []
        chunk_idx = response.meta.get("qfq_chunk_idx", 0)
        qfq_by_date = dict(response.meta.get("qfq_by_date") or {})

        try:
            payload = json.loads(response.text)
            chunk_rows = parse_tencent_fq_rows(payload, symbol, fq_type="qfq")
            if not chunk_rows:
                empty_retries = response.meta.get("qfq_empty_retry", 0)
                max_empty = self.settings.getint("STOCK_QUOTE_QFQ_MAX_RETRIES", 3)
                if empty_retries < max_empty:
                    self.logger.warning(
                        "腾讯前复权空响应，重试 %s/%s: stock_code=%s 段=%s~%s",
                        empty_retries + 1,
                        max_empty,
                        stock_code,
                        response.meta.get("chunk_start"),
                        response.meta.get("chunk_end"),
                    )
                    yield self._build_tencent_fq_request(
                        symbol=symbol,
                        meta={
                            **response.meta,
                            "qfq_empty_retry": empty_retries + 1,
                        },
                        fq_type="qfq",
                        chunk_start=response.meta.get("chunk_start"),
                        chunk_end=response.meta.get("chunk_end"),
                    )
                    return
                self.logger.warning(
                    "腾讯前复权空响应（已用尽重试）: stock_code=%s 段=%s~%s",
                    stock_code,
                    response.meta.get("chunk_start"),
                    response.meta.get("chunk_end"),
                )
            else:
                qfq_by_date.update(chunk_rows)
        except json.JSONDecodeError:
            self.logger.warning("腾讯前复权解析失败: stock_code=%s", stock_code)

        next_idx = chunk_idx + 1
        if next_idx < len(self._qfq_chunks):
            chunk_start, chunk_end = self._qfq_chunks[next_idx]
            yield self._build_tencent_fq_request(
                symbol=symbol,
                meta={
                    **response.meta,
                    "day_rows": day_rows,
                    "qfq_chunk_idx": next_idx,
                    "qfq_by_date": qfq_by_date,
                    "qfq_empty_retry": 0,
                },
                fq_type="qfq",
                chunk_start=chunk_start,
                chunk_end=chunk_end,
            )
            return

        if self._fetch_hfq:
            yield self._build_tencent_fq_request(
                symbol=symbol,
                meta={**response.meta, "day_rows": day_rows, "qfq_by_date": qfq_by_date},
                fq_type="hfq",
            )
            return

        self._save_quotes(stock_code, day_rows, qfq_by_date, hfq_by_date=None)

    def parse_tencent_hfq(self, response):
        stock_code = response.meta["stock_code"]
        day_rows = response.meta.get("day_rows") or []
        qfq_by_date = response.meta.get("qfq_by_date") or {}

        hfq_by_date: dict[str, list] = {}
        try:
            payload = json.loads(response.text)
            hfq_by_date = parse_tencent_fq_rows(payload, response.meta["symbol"], fq_type="hfq")
        except json.JSONDecodeError:
            self.logger.warning("腾讯后复权解析失败: stock_code=%s", stock_code)

        self._save_quotes(stock_code, day_rows, qfq_by_date, hfq_by_date)

    def _save_quotes(
        self,
        stock_code: str,
        day_rows: list,
        qfq_by_date: dict,
        hfq_by_date: dict | None,
    ) -> None:
        stock_name = self._stock_names.get(stock_code)

        if self._fetch_sina and self._fetch_qfq:
            quote_rows = build_enriched_stock_quotes(
                stock_code=stock_code,
                day_rows=day_rows,
                qfq_by_date=qfq_by_date,
                hfq_by_date=hfq_by_date,
                stock_name=stock_name,
                since_date=self._since_date,
            )
        elif self._fetch_qfq:
            merge_rows = day_rows or load_unadjusted_quotes_from_db(
                self._mysql_settings, stock_code, self._since_date
            )
            if merge_rows and qfq_by_date:
                quote_rows = build_enriched_stock_quotes(
                    stock_code=stock_code,
                    day_rows=merge_rows,
                    qfq_by_date=qfq_by_date,
                    hfq_by_date=hfq_by_date,
                    stock_name=stock_name,
                    since_date=self._since_date,
                )
            else:
                quote_rows = build_quotes_qfq_only(
                    stock_code=stock_code,
                    qfq_by_date=qfq_by_date,
                    stock_name=stock_name,
                    since_date=self._since_date,
                )
        else:
            quote_rows = build_quotes_sina_only(
                stock_code=stock_code,
                day_rows=day_rows,
                stock_name=stock_name,
                since_date=self._since_date,
            )

        if not quote_rows:
            return

        count = bulk_save_stock_quotes(self._mysql_settings, quote_rows)
        qfq_count = sum(1 for row in quote_rows if row.get("close_qfq") is not None)
        sina_count = sum(1 for row in quote_rows if row.get("close_price") is not None)
        if self._fetch_qfq and qfq_count == 0:
            self.logger.error(
                "stock_code=%s 前复权未写入（qfq=0），请检查 QFQ_BAR_COUNT<=2000",
                stock_code,
            )
        else:
            self.logger.info(
                "stock_code=%s 写入 %s 条（不复权 %s，前复权 %s）",
                stock_code,
                count,
                sina_count,
                qfq_count,
            )
