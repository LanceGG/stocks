"""公共工具：MySQL 读写、持仓解析、批量写入与无持仓基金过滤。"""

import json
import re
from datetime import date
from typing import Any

import pymysql


def get_mysql_settings(crawler_settings) -> dict:
    """从 Scrapy settings 构建 pymysql 连接参数字典。"""
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
    """加载 fund_holding 表中已有任意持仓数据的 fund_code 集合。

    用于 fund_holding 爬虫启动时按基金维度跳过已爬取过的基金。
    """
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
    """加载指定报告期已有持仓数据的 fund_code 集合。

    用于 fund_holding_current 爬虫按当前季度跳过已同步基金。
    """
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


# 持仓 upsert SQL，Pipeline 与 bulk_save 共用
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
    """批量 upsert 持仓记录到 fund_holding 表。

    Args:
        mysql_settings: pymysql 连接参数
        rows: 持仓字典列表
        batch_size: executemany 每批条数

    Returns:
        写入总条数
    """
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
    """按基金跟踪请求进度，单只基金全部请求完成后批量写入数据库。

    工作流程：
    1. register_request：发起请求前计数 +1
    2. add_holdings：解析到持仓后缓存到内存
    3. finish_request：请求完成计数 -1，归零时 flush 该基金的缓存
    """

    def __init__(self, mysql_settings, logger, on_fund_complete=None):
        self._mysql_settings = mysql_settings
        self._logger = logger
        self._on_fund_complete = on_fund_complete  # 单基金完成回调（如写入 fund_filter）
        self._active_requests: dict[str, int] = {}  # fund_code -> 未完成请求数
        self._pending_holdings: dict[str, list[dict[str, Any]]] = {}  # 待写入缓存

    def register_request(self, fund_code: str) -> None:
        """注册一次新请求，活跃计数 +1。"""
        self._active_requests[fund_code] = self._active_requests.get(fund_code, 0) + 1

    def add_holdings(self, fund_code: str, rows: list[dict[str, Any]]) -> None:
        """将解析到的持仓追加到该基金的待写入缓存。"""
        if rows:
            self._pending_holdings.setdefault(fund_code, []).extend(rows)

    def finish_request(self, fund_code: str) -> None:
        """标记一次请求完成；该基金所有请求完成后触发 flush。"""
        count = self._active_requests.get(fund_code, 0)
        if count <= 1:
            self._active_requests.pop(fund_code, None)
            self._flush_fund(fund_code)
        else:
            self._active_requests[fund_code] = count - 1

    def flush_all(self) -> None:
        """爬虫结束时兜底：刷写所有未完成基金的数据。"""
        for fund_code in list(self._pending_holdings):
            self._flush_fund(fund_code)
        for fund_code in list(self._active_requests):
            self._flush_fund(fund_code)

    def _flush_fund(self, fund_code: str) -> None:
        """将单只基金的缓存批量写入数据库，并触发完成回调。"""
        rows = self._pending_holdings.pop(fund_code, [])
        wrote_holdings = bool(rows)
        if rows:
            count = bulk_save_fund_holdings(self._mysql_settings, rows)
            self._logger.info("基金 %s 批量写入 %s 条持仓记录", fund_code, count)
        if self._on_fund_complete:
            self._on_fund_complete(fund_code, wrote_holdings=wrote_holdings)


def load_funds_with_establish_date(mysql_settings) -> list[tuple[str, date | None]]:
    """从 fund 表加载全部基金代码及成立日期，按代码排序。"""
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
    """根据当前日期推算最新已结束季度的报告截止日。

    例如 2026-06-18 -> 2025-12-31（Q4 已结束，Q1 尚未结束）。
    """
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
    """过滤出尚未爬取持仓的 (fund_code, holding_type) 任务。

    按基金维度跳过：只要 fund_holding 有该基金的任意记录即跳过整只基金。
    每只待爬基金生成 stock + bond 两个发现任务。
    """
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
    """过滤出当前报告期尚未入库的 (fund_code, holding_type) 任务。

    逻辑同 filter_discovery_tasks，但 synced 集合按当前 report_date 筛选。
    """
    tasks: list[tuple[str, str]] = []
    for fund_code, _ in funds:
        if skip_synced and fund_code in synced_fund_codes:
            continue
        for holding_type in ("stock", "bond"):
            tasks.append((fund_code, holding_type))
    return tasks


def load_filtered_fund_codes(mysql_settings) -> set[str]:
    """加载 fund_filter 表中需跳过的无持仓基金代码。"""
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
    """从基金列表中排除 fund_filter 表中的代码。"""
    if not filtered_codes:
        return funds
    return [(code, establish_date) for code, establish_date in funds if code not in filtered_codes]


def save_fund_to_filter(mysql_settings, fund_code: str) -> bool:
    """将确认无持仓的基金从 fund 表复制写入 fund_filter 表。"""
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
    """跟踪本次爬取中确认无持仓的基金，并在全部请求完成后写入 fund_filter。

    设计要点：
    - 仅在单只基金全部请求完成后判定，避免并发时债券先返回空导致误判
    - record_empty_type 只记录状态，不立即写库
    - 需 stock/bond 两种类型均确认无持仓才写入 fund_filter
    """

    def __init__(self, mysql_settings, logger):
        self._mysql_settings = mysql_settings
        self._logger = logger
        self._has_existing_holdings = lambda fund_code: False  # 历史已有持仓检查
        self.filtered_codes = load_filtered_fund_codes(mysql_settings)
        self._pending_checks: dict[str, set[str]] = {}  # fund -> 待检查的类型集合
        self._empty_types: dict[str, set[str]] = {}  # API 返回无历史年份的类型
        self._types_with_years: set[tuple[str, str]] = set()  # 有年份数据的类型
        self._type_has_holdings: set[tuple[str, str]] = set()  # 解析到持仓的类型

    def set_has_existing_holdings(self, fn) -> None:
        """设置历史持仓检查函数（启动时加载的 synced 集合）。"""
        self._has_existing_holdings = fn

    def register_task(self, fund_code: str, holding_type: str) -> None:
        """注册本次爬取任务（stock 或 bond）。"""
        self._pending_checks.setdefault(fund_code, set()).add(holding_type)

    def record_empty_type(self, fund_code: str, holding_type: str) -> None:
        """API 返回无历史年份，仅记录状态，不立即写入 fund_filter。"""
        self._empty_types.setdefault(fund_code, set()).add(holding_type)

    def record_types_with_years(self, fund_code: str, holding_type: str) -> None:
        """记录该类型有可用历史年份（后续可能解析到空持仓）。"""
        self._types_with_years.add((fund_code, holding_type))

    def record_has_holdings(self, fund_code: str, holding_type: str) -> None:
        """记录该类型确实解析到了持仓数据。"""
        self._type_has_holdings.add((fund_code, holding_type))

    def flush(self) -> None:
        """爬虫结束时兜底检查所有待处理基金。"""
        for fund_code in self._pending_checks:
            self._try_save(fund_code)

    def try_save_fund(self, fund_code: str, *, wrote_holdings: bool = False) -> None:
        """单只基金爬取完成时调用；若写入了持仓则跳过 fund_filter。"""
        if wrote_holdings:
            return
        self._try_save(fund_code)

    def _fund_has_crawled_holdings(self, fund_code: str) -> bool:
        """本次爬取中是否任一类型解析到了持仓。"""
        return any(
            (fund_code, holding_type) in self._type_has_holdings
            for holding_type in ("stock", "bond")
        )

    def _confirmed_empty_types(self, fund_code: str) -> set[str]:
        """汇总已确认无持仓的类型（API 无年份 + 有年份但解析为空）。"""
        empty = set(self._empty_types.get(fund_code, set()))
        pending = self._pending_checks.get(fund_code, set())
        for holding_type in pending:
            key = (fund_code, holding_type)
            if key in self._type_has_holdings:
                continue
            if holding_type in empty:
                continue
            # 有年份但所有请求完成后仍未解析到持仓，视为该类型无持仓
            if key in self._types_with_years:
                empty.add(holding_type)
        return empty

    def _try_save(self, fund_code: str) -> None:
        """满足全部条件时将基金写入 fund_filter。"""
        if fund_code in self.filtered_codes:
            return
        if self._has_existing_holdings(fund_code):
            return
        if self._fund_has_crawled_holdings(fund_code):
            return

        pending = self._pending_checks.get(fund_code, set())
        if not pending:
            return

        # 所有待检查类型均已确认无持仓
        if not pending.issubset(self._confirmed_empty_types(fund_code)):
            return

        if save_fund_to_filter(self._mysql_settings, fund_code):
            self.filtered_codes.add(fund_code)
            self._logger.info("无持仓，已加入 fund_filter: fund_code=%s", fund_code)


def parse_f10_apidata(text: str) -> dict[str, Any]:
    """解析东方财富 F10 接口返回的 JS 变量 apidata。

    返回 content（HTML 片段）、arryear（可用年份列表）、curyear（当前年）。
    """
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
    """从 HTML 内容中解析股票持仓，按季度分段提取。"""
    holdings: list[dict[str, Any]] = []
    # 按 "2024年1季度..." 标题拆分各季度区块
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
            # 提取占净值比例、持股数、市值三列
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
    """从 HTML 内容中解析债券持仓，结构与股票类似但字段更少。"""
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
                    share_count=None,  # 债券无持股数
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
    """构建单条持仓字典，统一字段名供数据库写入。"""
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
    """从多条季度持仓中只保留 report_date 最新的一季。"""
    if not holdings:
        return []

    latest_date = max(item["report_date"] for item in holdings)
    return [item for item in holdings if item["report_date"] == latest_date]


def _to_float(value):
    """安全地将字符串转为 float，无效值返回 None。"""
    if value in (None, "", "--"):
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip().replace(",", "").replace("%", "")
    try:
        return float(value)
    except ValueError:
        return None


def infer_stock_market(stock_code: str) -> str | None:
    """根据股票代码推断市场：SH/SZ/BJ。"""
    if not stock_code:
        return None
    code = stock_code.strip()
    if code.startswith(("6", "5", "9")):
        return "SH"
    if code.startswith(("0", "1", "2", "3")):
        return "SZ"
    if code.startswith(("4", "8")):
        return "BJ"
    return None


def parse_trade_date_from_ts(ts_value) -> str | None:
    """将 push2 接口 f124 时间戳转为 YYYY-MM-DD（东八区）。"""
    if ts_value in (None, "", "-", "--", 0, "0"):
        return None
    try:
        ts = int(ts_value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    from datetime import datetime, timedelta, timezone

    cst = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(ts, tz=cst).strftime("%Y-%m-%d")


def split_board_names(raw_value) -> list[str]:
    """解析逗号/顿号分隔的板块名称列表。"""
    if raw_value in (None, "", "-", "--"):
        return []
    text = str(raw_value).strip()
    if not text:
        return []
    parts = re.split(r"[,，、;；]", text)
    return [part.strip() for part in parts if part.strip()]


def load_sector_name_map(mysql_settings) -> dict[tuple[str, str], str]:
    """从 sector 表加载 (sector_type, sector_name) -> sector_code 映射。"""
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT sector_type, sector_name, sector_code FROM sector"
            )
            return {
                (row[0], row[1]): row[2]
                for row in cursor.fetchall()
            }
    finally:
        connection.close()


def build_stock_secid(stock_code: str, market: str | None = None) -> str:
    """构建 push2his 接口 secid（如 1.600519、0.000001）。"""
    market = market or infer_stock_market(stock_code)
    prefix = {"SH": "1", "SZ": "0", "BJ": "0"}.get(market or "", "0")
    return f"{prefix}.{stock_code}"


def compute_quarter_first_trading_days(
    kline_lines: list[str], from_year: int
) -> list[str]:
    """从日 K 线中提取各季度首个交易日（按上证指数交易日历）。"""
    dates: list[str] = []
    seen: set[tuple[int, int]] = set()
    for line in kline_lines:
        date_str = line.split(",")[0]
        year = int(date_str[:4])
        if year < from_year:
            continue
        month = int(date_str[5:7])
        quarter = (month - 1) // 3 + 1
        key = (year, quarter)
        if key in seen:
            continue
        seen.add(key)
        dates.append(date_str)
    return dates


def filter_quarter_dates_through_today(
    dates: list[str], today: date | None = None
) -> list[str]:
    """过滤掉尚未到来的季度锚点日。"""
    today = today or date.today()
    current_quarter = (today.month - 1) // 3 + 1
    result: list[str] = []
    for date_str in dates:
        year = int(date_str[:4])
        month = int(date_str[5:7])
        quarter = (month - 1) // 3 + 1
        if (year, quarter) > (today.year, current_quarter):
            continue
        result.append(date_str)
    return result


def load_stocks_from_db(mysql_settings) -> list[tuple[str, str | None]]:
    """从 stock 表加载股票代码及市场。"""
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stock_code, market FROM stock ORDER BY stock_code"
            )
            return [(row[0], row[1]) for row in cursor.fetchall()]
    finally:
        connection.close()


def load_synced_quote_pairs(
    mysql_settings, trade_dates: list[str]
) -> set[tuple[str, str]]:
    """加载已入库的 (stock_code, trade_date) 集合。"""
    if not trade_dates:
        return set()
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            placeholders = ",".join(["%s"] * len(trade_dates))
            cursor.execute(
                f"""
                SELECT stock_code, trade_date
                FROM stock_capital_flow
                WHERE trade_date IN ({placeholders})
                """,
                trade_dates,
            )
            return {
                (row[0], row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]))
                for row in cursor.fetchall()
            }
    finally:
        connection.close()


def load_synced_quote_pairs_since(
    mysql_settings, since_date: str
) -> set[tuple[str, str]]:
    """加载 trade_date >= since_date 的已入库 (stock_code, trade_date)。"""
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT stock_code, trade_date
                FROM stock_capital_flow
                WHERE trade_date >= %s
                """,
                (since_date,),
            )
            return {
                (row[0], row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]))
                for row in cursor.fetchall()
            }
    finally:
        connection.close()


UPSERT_STOCK_CAPITAL_FLOW_SQL = """
    INSERT INTO stock_capital_flow (
        stock_code, trade_date, open_price, close_price, high_price, low_price,
        pct_change, volume, amount, turnover_rate, market_cap,
        adj_factor, close_adj, main_net_inflow, trade_status
    ) VALUES (
        %(stock_code)s, %(trade_date)s, %(open_price)s, %(close_price)s,
        %(high_price)s, %(low_price)s, %(pct_change)s, %(volume)s, %(amount)s,
        %(turnover_rate)s, %(market_cap)s, %(adj_factor)s, %(close_adj)s,
        %(main_net_inflow)s, %(trade_status)s
    )
    ON DUPLICATE KEY UPDATE
        open_price = VALUES(open_price),
        close_price = VALUES(close_price),
        high_price = VALUES(high_price),
        low_price = VALUES(low_price),
        pct_change = VALUES(pct_change),
        volume = VALUES(volume),
        amount = VALUES(amount),
        turnover_rate = VALUES(turnover_rate),
        market_cap = VALUES(market_cap),
        adj_factor = VALUES(adj_factor),
        close_adj = VALUES(close_adj),
        main_net_inflow = VALUES(main_net_inflow),
        trade_status = VALUES(trade_status),
        crawled_at = CURRENT_TIMESTAMP
"""


def bulk_save_stock_quotes(
    mysql_settings, rows: list[dict[str, Any]], batch_size: int = 500
) -> int:
    """批量 upsert 股票日行情到 stock_capital_flow。"""
    if not rows:
        return 0
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            for offset in range(0, len(rows), batch_size):
                cursor.executemany(
                    UPSERT_STOCK_CAPITAL_FLOW_SQL, rows[offset : offset + batch_size]
                )
            connection.commit()
            return len(rows)
    finally:
        connection.close()


def approx_latest_trade_date(today: date | None = None) -> str:
    """估算最近一个交易日（跳过周末）。"""
    from datetime import timedelta

    d = today or date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def load_stock_latest_trade_dates(
    mysql_settings, since_date: str
) -> dict[str, str]:
    """各股票在 since_date 之后已入库的最新 trade_date。"""
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT stock_code, MAX(trade_date)
                FROM stock_capital_flow
                WHERE trade_date >= %s
                GROUP BY stock_code
                """,
                (since_date,),
            )
            return {
                row[0]: row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1])
                for row in cursor.fetchall()
            }
    finally:
        connection.close()


def load_stock_quote_sync_bounds(
    mysql_settings, since_date: str
) -> dict[str, tuple[str, str]]:
    """各股票在 since_date 之后的 (最早 trade_date, 最新 trade_date)。"""
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT stock_code, MIN(trade_date), MAX(trade_date)
                FROM stock_capital_flow
                WHERE trade_date >= %s
                GROUP BY stock_code
                """,
                (since_date,),
            )
            return {
                row[0]: (
                    row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]),
                    row[2].isoformat() if hasattr(row[2], "isoformat") else str(row[2]),
                )
                for row in cursor.fetchall()
            }
    finally:
        connection.close()


def parse_kline_ohlc(line: str) -> dict[str, Any] | None:
    """解析 push2his 日 K 单条：date,open,close,high,low,..."""
    parts = line.split(",")
    if len(parts) < 5:
        return None
    return {
        "trade_date": parts[0],
        "open_price": _to_float(parts[1]),
        "close_price": _to_float(parts[2]),
        "high_price": _to_float(parts[3]),
        "low_price": _to_float(parts[4]),
    }


def build_sina_symbol(stock_code: str, market: str | None = None) -> str:
    """构建新浪行情 symbol：sh600519 / sz000001 / bj830799。"""
    market = market or infer_stock_market(stock_code)
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(market or "", "sz")
    return f"{prefix}{stock_code}"


def parse_sina_kline_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """解析新浪日 K 单条 JSON。"""
    day = row.get("day")
    if not day:
        return None
    volume = _to_int(row.get("volume"))
    return {
        "trade_date": day,
        "open_price": _to_float(row.get("open")),
        "close_price": _to_float(row.get("close")),
        "high_price": _to_float(row.get("high")),
        "low_price": _to_float(row.get("low")),
        "volume": volume,
    }


def parse_tencent_hfq_rows(payload: dict[str, Any], symbol: str) -> dict[str, list[Any]]:
    """解析腾讯后复权日 K，返回 trade_date -> [open, close, high, low, volume]。"""
    stock_data = (payload.get("data") or {}).get(symbol) or {}
    hfq_rows = stock_data.get("hfqday") or []
    result: dict[str, list[Any]] = {}
    for row in hfq_rows:
        if not row or len(row) < 6:
            continue
        result[str(row[0])] = row
    return result


def infer_trade_status(stock_name: str | None, quote: dict[str, Any]) -> int:
    """推断交易状态：0正常 1停牌 2ST。"""
    if stock_name:
        upper = stock_name.upper()
        if "ST" in upper or stock_name.startswith("*"):
            return 2
    volume = quote.get("volume") or 0
    o = quote.get("open_price")
    h = quote.get("high_price")
    l = quote.get("low_price")
    c = quote.get("close_price")
    if volume == 0 and o is not None and o == h == l == c:
        return 1
    return 0


def build_enriched_stock_quotes(
    stock_code: str,
    day_rows: list[dict[str, Any]],
    hfq_by_date: dict[str, list[Any]],
    stock_name: str | None,
    since_date: str,
) -> list[dict[str, Any]]:
    """合并新浪日 K 与腾讯后复权，计算涨跌幅/复权因子等。"""
    sorted_rows = sorted(day_rows, key=lambda item: item["trade_date"])
    prev_close: float | None = None
    quotes: list[dict[str, Any]] = []

    for row in sorted_rows:
        trade_date = row["trade_date"]
        if trade_date < since_date:
            continue

        close_price = row.get("close_price")
        pct_change = None
        if prev_close and prev_close > 0 and close_price is not None:
            pct_change = round((close_price - prev_close) / prev_close * 100, 2)

        hfq = hfq_by_date.get(trade_date)
        close_adj = _to_float(hfq[2]) if hfq else None
        adj_factor = None
        if close_adj is not None and close_price and close_price > 0:
            adj_factor = round(close_adj / close_price, 6)

        quote = {
            "stock_code": stock_code,
            "trade_date": trade_date,
            "open_price": row.get("open_price"),
            "close_price": close_price,
            "high_price": row.get("high_price"),
            "low_price": row.get("low_price"),
            "pct_change": pct_change,
            "volume": row.get("volume"),
            "amount": None,
            "turnover_rate": None,
            "market_cap": None,
            "adj_factor": adj_factor,
            "close_adj": close_adj,
            "main_net_inflow": None,
            "trade_status": infer_trade_status(stock_name, row),
        }
        quotes.append(quote)
        if close_price is not None:
            prev_close = close_price

    return quotes


def load_stock_names(mysql_settings) -> dict[str, str]:
    """加载 stock_code -> stock_name，用于 ST 判定。"""
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT stock_code, stock_name FROM stock")
            return {row[0]: row[1] for row in cursor.fetchall()}
    finally:
        connection.close()


def _to_int(value):
    """安全地将字符串转为 int。"""
    if value in (None, "", "--"):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def compute_quarter_first_trading_days_from_rows(
    rows: list[dict[str, Any]], from_year: int
) -> list[str]:
    """从新浪 K 线 JSON 列表提取各季度首个交易日。"""
    lines = [
        f"{row['day']},{row.get('open')},{row.get('close')},{row.get('high')},{row.get('low')}"
        for row in rows
        if row.get("day")
    ]
    return compute_quarter_first_trading_days(lines, from_year)


UPSERT_FUND_NAV_SQL = """
    INSERT INTO fund_nav (
        fund_code, nav_date, unit_nav, accumulated_nav, daily_growth_rate
    ) VALUES (
        %(fund_code)s, %(nav_date)s, %(unit_nav)s, %(accumulated_nav)s,
        %(daily_growth_rate)s
    )
    ON DUPLICATE KEY UPDATE
        unit_nav = VALUES(unit_nav),
        accumulated_nav = VALUES(accumulated_nav),
        daily_growth_rate = VALUES(daily_growth_rate),
        updated_at = CURRENT_TIMESTAMP
"""


def bulk_save_fund_nav(
    mysql_settings, rows: list[dict[str, Any]], batch_size: int = 500
) -> int:
    """批量 upsert 基金净值到 fund_nav 表。"""
    if not rows:
        return 0
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            for offset in range(0, len(rows), batch_size):
                cursor.executemany(
                    UPSERT_FUND_NAV_SQL, rows[offset : offset + batch_size]
                )
            connection.commit()
            return len(rows)
    finally:
        connection.close()


def parse_fund_nav_apidata(text: str) -> dict[str, Any]:
    """解析 F10DataApi lsjz 响应：content HTML + pages/curpage。"""
    text = text.strip()
    content_match = re.search(r'content:"(.*)",(?:pages|curpage|record)', text, re.S)
    if not content_match:
        return {"content": "", "pages": 1, "curpage": 1}

    content = content_match.group(1).replace("\\'", "'")
    pages_match = re.search(r"pages:(\d+)", text)
    curpage_match = re.search(r"curpage:(\d+)", text)
    return {
        "content": content,
        "pages": int(pages_match.group(1)) if pages_match else 1,
        "curpage": int(curpage_match.group(1)) if curpage_match else 1,
    }


def parse_fund_nav_rows(content: str, fund_code: str) -> list[dict[str, Any]]:
    """从 lsjz HTML 表格解析净值记录。"""
    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r"<tr><td>([\d-]+)</td>"
        r"<td[^>]*>([^<]*)</td>"
        r"<td[^>]*>([^<]*)</td>"
        r"<td[^>]*>([^<]*)</td>",
        re.S,
    )
    for nav_date, unit_nav, accumulated_nav, daily_growth in pattern.findall(content):
        unit_nav = unit_nav.strip()
        accumulated_nav = accumulated_nav.strip()
        if not unit_nav or unit_nav == "--":
            continue
        rows.append(
            {
                "fund_code": fund_code,
                "nav_date": nav_date,
                "unit_nav": _to_float(unit_nav),
                "accumulated_nav": _to_float(accumulated_nav),
                "daily_growth_rate": _parse_pct(daily_growth),
            }
        )
    return rows


def _parse_pct(value) -> float | None:
    if value in (None, "", "--"):
        return None
    text = str(value).strip().replace("%", "")
    return _to_float(text)


def load_fund_nav_sync_bounds(
    mysql_settings, since_date: str
) -> dict[str, tuple[str, str]]:
    """各基金在 since_date 之后的 (最早 nav_date, 最新 nav_date)。"""
    connection = pymysql.connect(**mysql_settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT fund_code, MIN(nav_date), MAX(nav_date)
                FROM fund_nav
                WHERE nav_date >= %s
                GROUP BY fund_code
                """,
                (since_date,),
            )
            return {
                row[0]: (
                    row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]),
                    row[2].isoformat() if hasattr(row[2], "isoformat") else str(row[2]),
                )
                for row in cursor.fetchall()
            }
    finally:
        connection.close()


def filter_fund_nav_rows(
    rows: list[dict[str, Any]],
    since_date: str,
    latest_known: str | None = None,
) -> list[dict[str, Any]]:
    """按起始日与已入库最新日期过滤净值记录。"""
    result: list[dict[str, Any]] = []
    for row in rows:
        nav_date = row["nav_date"]
        if nav_date < since_date:
            continue
        if latest_known and nav_date <= latest_known:
            continue
        result.append(row)
    return result
