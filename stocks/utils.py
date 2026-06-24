import json
import re
from datetime import date
from typing import Any

import pymysql


def get_mysql_settings(crawler_settings) -> dict:
    password = crawler_settings.get("MYSQL_PASSWORD")
    if password is None:
        password = ""
    return {
        "host": crawler_settings.get("MYSQL_HOST", "127.0.0.1"),
        "port": crawler_settings.getint("MYSQL_PORT", 3306),
        "user": crawler_settings.get("MYSQL_USER", "root"),
        "password": password,
        "database": crawler_settings.get("MYSQL_DATABASE", "stocks"),
        "charset": crawler_settings.get("MYSQL_CHARSET", "utf8mb4"),
    }


def load_synced_fund_codes(mysql_settings) -> set[str]:
    """已有持仓数据的 fund_code 集合。"""
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT fund_code FROM fund_holding")
            return {row[0] for row in cursor.fetchall()}
    finally:
        connection.close()


def load_synced_current_quarter_funds(
    mysql_settings, report_date: str
) -> set[str]:
    """指定报告期已有持仓数据的 fund_code 集合。"""
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT fund_code
                FROM fund_holding
                WHERE report_date = %s
                """,
                (report_date,),
            )
            return {row[0] for row in cursor.fetchall()}
    finally:
        connection.close()


UPSERT_HOLDING_SQL = """
    INSERT INTO fund_holding (
        fund_code, report_date, report_year, report_quarter,
        stock_code, stock_name, rank_num, nav_ratio,
        share_count, market_value, holding_type
    ) VALUES (
        %(fund_code)s, %(report_date)s, %(report_year)s, %(report_quarter)s,
        %(stock_code)s, %(stock_name)s, %(rank_num)s, %(nav_ratio)s,
        %(share_count)s, %(market_value)s, %(holding_type)s
    )
    ON DUPLICATE KEY UPDATE
        stock_name = VALUES(stock_name),
        rank_num = VALUES(rank_num),
        nav_ratio = VALUES(nav_ratio),
        share_count = VALUES(share_count),
        market_value = VALUES(market_value),
        report_year = VALUES(report_year),
        report_quarter = VALUES(report_quarter),
        crawled_at = CURRENT_TIMESTAMP
"""


def bulk_save_fund_holdings(mysql_settings, rows: list[dict[str, Any]], batch_size: int = 500) -> int:
    """批量写入持仓数据。"""
    if not rows:
        return 0

    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            for offset in range(0, len(rows), batch_size):
                cursor.executemany(UPSERT_HOLDING_SQL, rows[offset : offset + batch_size])
            connection.commit()
            return len(rows)
    finally:
        connection.close()


class FundBatchWriter:
    """按基金跟踪请求进度，单只基金爬取完成后批量写入。"""

    def __init__(self, mysql_settings, logger, on_fund_complete=None):
        self._mysql_settings = mysql_settings
        self._logger = logger
        self._on_fund_complete = on_fund_complete
        self._active_requests: dict[str, int] = {}
        self._pending_holdings: dict[str, list[dict[str, Any]]] = {}

    def register_request(self, fund_code: str) -> None:
        self._active_requests[fund_code] = self._active_requests.get(fund_code, 0) + 1

    def add_holdings(self, fund_code: str, rows: list[dict[str, Any]]) -> None:
        if rows:
            self._pending_holdings.setdefault(fund_code, []).extend(rows)

    def finish_request(self, fund_code: str) -> None:
        count = self._active_requests.get(fund_code, 0)
        if count <= 1:
            self._active_requests.pop(fund_code, None)
            self._flush_fund(fund_code)
        else:
            self._active_requests[fund_code] = count - 1

    def flush_all(self) -> None:
        for fund_code in list(self._pending_holdings):
            self._flush_fund(fund_code)
        for fund_code in list(self._active_requests):
            self._flush_fund(fund_code)

    def _flush_fund(self, fund_code: str) -> None:
        rows = self._pending_holdings.pop(fund_code, [])
        wrote_holdings = bool(rows)
        if rows:
            count = bulk_save_fund_holdings(self._mysql_settings, rows)
            self._logger.info("基金 %s 批量写入 %s 条持仓记录", fund_code, count)
        if self._on_fund_complete:
            self._on_fund_complete(fund_code, wrote_holdings=wrote_holdings)


def load_funds_with_establish_date(mysql_settings) -> list[tuple[str, date | None]]:
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT fund_code, establish_date FROM fund ORDER BY fund_code"
            )
            return [(row[0], row[1]) for row in cursor.fetchall()]
    finally:
        connection.close()


def expected_latest_report_date(today: date | None = None) -> str:
    """根据当前日期推算最新已结束季度的报告截止日。"""
    today = today or date.today()
    quarter_ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    for month, day in reversed(quarter_ends):
        quarter_end = date(today.year, month, day)
        if today >= quarter_end:
            return quarter_end.isoformat()
    return date(today.year - 1, 12, 31).isoformat()


def filter_discovery_tasks(
    funds: list[tuple[str, date | None]],
    synced_fund_codes: set[str],
    skip_synced: bool,
) -> list[tuple[str, str]]:
    """过滤出尚未爬取持仓的 (fund_code, holding_type) 任务（按基金维度跳过）。"""
    tasks: list[tuple[str, str]] = []
    for fund_code, _ in funds:
        if skip_synced and fund_code in synced_fund_codes:
            continue
        for holding_type in ("stock", "bond"):
            tasks.append((fund_code, holding_type))
    return tasks


def filter_current_holding_tasks(
    funds: list[tuple[str, date | None]],
    synced_fund_codes: set[str],
    skip_synced: bool,
) -> list[tuple[str, str]]:
    """过滤出当前报告期尚未入库的 (fund_code, holding_type) 任务（按基金维度跳过）。"""
    tasks: list[tuple[str, str]] = []
    for fund_code, _ in funds:
        if skip_synced and fund_code in synced_fund_codes:
            continue
        for holding_type in ("stock", "bond"):
            tasks.append((fund_code, holding_type))
    return tasks


def load_filtered_fund_codes(mysql_settings) -> set[str]:
    """加载 fund_filter 表中需跳过的基金代码。"""
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT fund_code FROM fund_filter")
            return {row[0] for row in cursor.fetchall()}
    finally:
        connection.close()


def exclude_filtered_funds(
    funds: list[tuple[str, date | None]],
    filtered_codes: set[str],
) -> list[tuple[str, date | None]]:
    """排除 fund_filter 表中的基金。"""
    if not filtered_codes:
        return funds
    return [(code, establish_date) for code, establish_date in funds if code not in filtered_codes]


def save_fund_to_filter(mysql_settings, fund_code: str) -> bool:
    """将无持仓基金从 fund 表复制写入 fund_filter。"""
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO fund_filter (
                    fund_code, fund_name, pinyin_abbr, establish_date
                )
                SELECT fund_code, fund_name, pinyin_abbr, establish_date
                FROM fund
                WHERE fund_code = %s
                ON DUPLICATE KEY UPDATE
                    fund_name = VALUES(fund_name),
                    pinyin_abbr = VALUES(pinyin_abbr),
                    establish_date = VALUES(establish_date),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (fund_code,),
            )
            saved = cursor.rowcount > 0
            connection.commit()
            return saved
    finally:
        connection.close()


class FundFilterTracker:
    """跟踪本次爬取中确认无持仓的基金，并写入 fund_filter。

    仅在单只基金全部请求完成后判定是否无持仓，避免并发爬取时提前误判。
    """

    def __init__(self, mysql_settings, logger):
        self._mysql_settings = mysql_settings
        self._logger = logger
        self._has_existing_holdings = lambda fund_code: False
        self.filtered_codes = load_filtered_fund_codes(mysql_settings)
        self._pending_checks: dict[str, set[str]] = {}
        self._empty_types: dict[str, set[str]] = {}
        self._types_with_years: set[tuple[str, str]] = set()
        self._type_has_holdings: set[tuple[str, str]] = set()

    def set_has_existing_holdings(self, fn) -> None:
        self._has_existing_holdings = fn

    def register_task(self, fund_code: str, holding_type: str) -> None:
        self._pending_checks.setdefault(fund_code, set()).add(holding_type)

    def record_empty_type(self, fund_code: str, holding_type: str) -> None:
        """接口返回无历史年份，仅记录状态，不立即写入 fund_filter。"""
        self._empty_types.setdefault(fund_code, set()).add(holding_type)

    def record_types_with_years(self, fund_code: str, holding_type: str) -> None:
        self._types_with_years.add((fund_code, holding_type))

    def record_has_holdings(self, fund_code: str, holding_type: str) -> None:
        self._type_has_holdings.add((fund_code, holding_type))

    def flush(self) -> None:
        for fund_code in self._pending_checks:
            self._try_save(fund_code)

    def try_save_fund(self, fund_code: str, *, wrote_holdings: bool = False) -> None:
        if wrote_holdings:
            return
        self._try_save(fund_code)

    def _fund_has_crawled_holdings(self, fund_code: str) -> bool:
        return any(
            (fund_code, holding_type) in self._type_has_holdings
            for holding_type in ("stock", "bond")
        )

    def _confirmed_empty_types(self, fund_code: str) -> set[str]:
        """仅在基金全部请求完成后调用，此时可确认有年份但未解析到持仓的类型。"""
        empty = set(self._empty_types.get(fund_code, set()))
        pending = self._pending_checks.get(fund_code, set())
        for holding_type in pending:
            key = (fund_code, holding_type)
            if key in self._type_has_holdings:
                continue
            if holding_type in empty:
                continue
            if key in self._types_with_years:
                empty.add(holding_type)
        return empty

    def _try_save(self, fund_code: str) -> None:
        if fund_code in self.filtered_codes:
            return
        if self._has_existing_holdings(fund_code):
            return
        if self._fund_has_crawled_holdings(fund_code):
            return

        pending = self._pending_checks.get(fund_code, set())
        if not pending:
            return

        if not pending.issubset(self._confirmed_empty_types(fund_code)):
            return

        if save_fund_to_filter(self._mysql_settings, fund_code):
            self.filtered_codes.add(fund_code)
            self._logger.info("无持仓，已加入 fund_filter: fund_code=%s", fund_code)


def parse_f10_apidata(text: str) -> dict[str, Any]:
    text = text.strip()
    match = re.search(r'content:"(.*)",arryear:(\[[^\]]+\])', text, re.S)
    if not match:
        return {"content": "", "arryear": [], "curyear": None}

    content = match.group(1).replace("\\'", "'")
    curyear_match = re.search(r",curyear:(\d+)", text)
    return {
        "content": content,
        "arryear": json.loads(match.group(2)),
        "curyear": int(curyear_match.group(1)) if curyear_match else None,
    }


def parse_stock_holdings(content: str, fund_code: str) -> list[dict[str, Any]]:
    holdings: list[dict[str, Any]] = []
    sections = re.split(r"(\d{4}年\d季度[^<]*)", content)

    for index in range(1, len(sections), 2):
        header = sections[index]
        body = sections[index + 1] if index + 1 < len(sections) else ""
        header_match = re.match(r"(\d{4})年(\d)季度", header)
        date_match = re.search(r"截止至[^>]*>([\d-]+)", body)
        if not header_match or not date_match:
            continue

        report_year = int(header_match.group(1))
        report_quarter = int(header_match.group(2))
        report_date = date_match.group(1)
        rows = re.findall(
            r"<tr><td>(\d+)</td><td><a href='[^']*'>(\d+)</a></td>"
            r"<td class='tol'><a[^>]*>([^<]+)</a></td>(.*?)</tr>",
            body,
            re.S,
        )

        for rank, stock_code, stock_name, rest in rows:
            nums = [
                value.replace(",", "").replace("%", "").strip()
                for value in re.findall(r"<td class='tor'>([^<]+)</td>", rest)
                if value.strip()
            ]
            nav_ratio = share_count = market_value = None
            if len(nums) >= 3:
                nav_ratio, share_count, market_value = nums[-3], nums[-2], nums[-1]

            holdings.append(
                _build_holding_item(
                    fund_code=fund_code,
                    report_date=report_date,
                    report_year=report_year,
                    report_quarter=report_quarter,
                    instrument_code=stock_code,
                    instrument_name=stock_name.strip(),
                    rank_num=int(rank),
                    nav_ratio=nav_ratio,
                    share_count=share_count,
                    market_value=market_value,
                    holding_type="stock",
                )
            )

    return holdings


def parse_bond_holdings(content: str, fund_code: str) -> list[dict[str, Any]]:
    holdings: list[dict[str, Any]] = []
    sections = re.split(r"(\d{4}年\d季度[^<]*)", content)

    for index in range(1, len(sections), 2):
        header = sections[index]
        body = sections[index + 1] if index + 1 < len(sections) else ""
        header_match = re.match(r"(\d{4})年(\d)季度", header)
        date_match = re.search(r"截止至[^>]*>([\d-]+)", body)
        if not header_match or not date_match:
            continue

        report_year = int(header_match.group(1))
        report_quarter = int(header_match.group(2))
        report_date = date_match.group(1)
        rows = re.findall(
            r"<tr><td>(\d+)</td><td>([^<]+)</td>"
            r"<td class='tol'>([^<]+)</td><td class='tor'>([^<]+)</td>"
            r"<td class='tor'>([^<]+)</td></tr>",
            body,
            re.S,
        )

        for rank, bond_code, bond_name, nav_ratio, market_value in rows:
            holdings.append(
                _build_holding_item(
                    fund_code=fund_code,
                    report_date=report_date,
                    report_year=report_year,
                    report_quarter=report_quarter,
                    instrument_code=bond_code.strip(),
                    instrument_name=bond_name.strip(),
                    rank_num=int(rank),
                    nav_ratio=nav_ratio,
                    share_count=None,
                    market_value=market_value,
                    holding_type="bond",
                )
            )

    return holdings


def _build_holding_item(
    fund_code: str,
    report_date: str,
    report_year: int,
    report_quarter: int,
    instrument_code: str,
    instrument_name: str,
    rank_num: int,
    nav_ratio,
    share_count,
    market_value,
    holding_type: str,
) -> dict[str, Any]:
    return {
        "fund_code": fund_code,
        "report_date": report_date,
        "report_year": report_year,
        "report_quarter": report_quarter,
        "stock_code": instrument_code,
        "stock_name": instrument_name,
        "rank_num": rank_num,
        "nav_ratio": _to_float(str(nav_ratio).replace(",", "").replace("%", "")),
        "share_count": _to_float(str(share_count).replace(",", "")) if share_count else None,
        "market_value": _to_float(str(market_value).replace(",", "")),
        "holding_type": holding_type,
    }


def keep_latest_quarter(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not holdings:
        return []

    latest_date = max(item["report_date"] for item in holdings)
    return [item for item in holdings if item["report_date"] == latest_date]


def _to_float(value):
    if value in (None, "", "--"):
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip().replace(",", "").replace("%", "")
    try:
        return float(value)
    except ValueError:
        return None

