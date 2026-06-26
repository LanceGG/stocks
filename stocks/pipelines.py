"""Scrapy Pipeline：将 Item 写入 MySQL。"""

import pymysql
from itemadapter import ItemAdapter
from pymysql.err import OperationalError

from stocks.items import (
    FundHoldingItem,
    FundRankingItem,
    SectorItem,
    StockCapitalFlowItem,
    StockItem,
    StockSectorRelItem,
)
from stocks.utils import UPSERT_HOLDING_SQL, UPSERT_STOCK_CAPITAL_FLOW_SQL, get_mysql_settings


class MySQLPipeline:
    """基金排行 Pipeline：同时 upsert fund 与 fund_ranking 表。"""

    UPSERT_FUND_SQL = """
        INSERT INTO fund (
            fund_code, fund_name, pinyin_abbr, establish_date
        ) VALUES (
            %(fund_code)s, %(fund_name)s, %(pinyin_abbr)s, %(establish_date)s
        )
        ON DUPLICATE KEY UPDATE
            fund_name = VALUES(fund_name),
            pinyin_abbr = VALUES(pinyin_abbr),
            establish_date = VALUES(establish_date),
            updated_at = CURRENT_TIMESTAMP
    """

    UPSERT_RANKING_SQL = """
        INSERT INTO fund_ranking (
            fund_code, fund_name, pinyin_abbr, nav_date, unit_nav, accumulated_nav,
            daily_growth_rate, week_1_rate, month_1_rate, month_3_rate, month_6_rate,
            year_1_rate, year_2_rate, year_3_rate, ytd_rate, since_inception_rate,
            establish_date, purchase_status, interval_growth_rate, original_fee,
            fee_rate, discount, discounted_fee, query_start_date, query_end_date,
            sort_column, page_index
        ) VALUES (
            %(fund_code)s, %(fund_name)s, %(pinyin_abbr)s, %(nav_date)s, %(unit_nav)s,
            %(accumulated_nav)s, %(daily_growth_rate)s, %(week_1_rate)s, %(month_1_rate)s,
            %(month_3_rate)s, %(month_6_rate)s, %(year_1_rate)s, %(year_2_rate)s,
            %(year_3_rate)s, %(ytd_rate)s, %(since_inception_rate)s, %(establish_date)s,
            %(purchase_status)s, %(interval_growth_rate)s, %(original_fee)s, %(fee_rate)s,
            %(discount)s, %(discounted_fee)s, %(query_start_date)s, %(query_end_date)s,
            %(sort_column)s, %(page_index)s
        )
        ON DUPLICATE KEY UPDATE
            fund_name = VALUES(fund_name),
            pinyin_abbr = VALUES(pinyin_abbr),
            nav_date = VALUES(nav_date),
            unit_nav = VALUES(unit_nav),
            accumulated_nav = VALUES(accumulated_nav),
            daily_growth_rate = VALUES(daily_growth_rate),
            week_1_rate = VALUES(week_1_rate),
            month_1_rate = VALUES(month_1_rate),
            month_3_rate = VALUES(month_3_rate),
            month_6_rate = VALUES(month_6_rate),
            year_1_rate = VALUES(year_1_rate),
            year_2_rate = VALUES(year_2_rate),
            year_3_rate = VALUES(year_3_rate),
            ytd_rate = VALUES(ytd_rate),
            since_inception_rate = VALUES(since_inception_rate),
            establish_date = VALUES(establish_date),
            purchase_status = VALUES(purchase_status),
            interval_growth_rate = VALUES(interval_growth_rate),
            original_fee = VALUES(original_fee),
            fee_rate = VALUES(fee_rate),
            discount = VALUES(discount),
            discounted_fee = VALUES(discounted_fee),
            query_start_date = VALUES(query_start_date),
            query_end_date = VALUES(query_end_date),
            sort_column = VALUES(sort_column),
            page_index = VALUES(page_index),
            crawled_at = CURRENT_TIMESTAMP
    """

    def __init__(self, mysql_settings):
        self.mysql_settings = mysql_settings
        self.connection = None
        self.cursor = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls(mysql_settings=get_mysql_settings(crawler.settings))

    def open_spider(self, spider):
        cfg = self.mysql_settings
        try:
            self.connection = pymysql.connect(**cfg)
        except OperationalError as exc:
            if exc.args and exc.args[0] == 1045:
                raise OperationalError(
                    exc.args[0],
                    f"MySQL 认证失败（user={cfg['user']}）。"
                    "请设置密码：复制 stocks/local_settings.py.example 为 "
                    "stocks/local_settings.py 并填写 MYSQL_PASSWORD，"
                    "或运行 scrapy crawl fund -s MYSQL_PASSWORD=你的密码",
                ) from exc
            raise
        self.cursor = self.connection.cursor()
        spider.logger.info(
            "MySQL 已连接: %s:%s/%s",
            cfg["host"],
            cfg["port"],
            cfg["database"],
        )

    def close_spider(self, spider):
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()

    def process_item(self, item, spider):
        if not isinstance(item, FundRankingItem):
            return item

        data = ItemAdapter(item).asdict()
        try:
            self.cursor.execute(self.UPSERT_FUND_SQL, data)
            self.cursor.execute(self.UPSERT_RANKING_SQL, data)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return item


class FundHoldingPipeline:
    """基金持仓 Pipeline：逐条 upsert（fund_holding 爬虫已改用 FundBatchWriter 批量写入）。"""

    UPSERT_HOLDING_SQL = UPSERT_HOLDING_SQL

    def __init__(self, mysql_settings):
        self.mysql_settings = mysql_settings
        self.connection = None
        self.cursor = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls(mysql_settings=get_mysql_settings(crawler.settings))

    def open_spider(self, spider):
        cfg = self.mysql_settings
        try:
            self.connection = pymysql.connect(**cfg)
        except OperationalError as exc:
            if exc.args and exc.args[0] == 1045:
                raise OperationalError(
                    exc.args[0],
                    f"MySQL 认证失败（user={cfg['user']}）。请设置 MYSQL_PASSWORD 后重试。",
                ) from exc
            raise
        self.cursor = self.connection.cursor()
        spider.logger.info(
            "MySQL 已连接: %s:%s/%s",
            cfg["host"],
            cfg["port"],
            cfg["database"],
        )

    def close_spider(self, spider):
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()

    def process_item(self, item, spider):
        if not isinstance(item, FundHoldingItem):
            return item

        data = ItemAdapter(item).asdict()
        try:
            self.cursor.execute(self.UPSERT_HOLDING_SQL, data)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return item


class StockDataPipeline:
    """股票/板块 Pipeline：upsert stock、sector、stock_sector_rel、stock_capital_flow。"""

    UPSERT_STOCK_SQL = """
        INSERT INTO stock (
            stock_code, stock_name, market, industry_name, region_board
        ) VALUES (
            %(stock_code)s, %(stock_name)s, %(market)s, %(industry_name)s, %(region_board)s
        )
        ON DUPLICATE KEY UPDATE
            stock_name = VALUES(stock_name),
            market = VALUES(market),
            industry_name = VALUES(industry_name),
            region_board = VALUES(region_board),
            updated_at = CURRENT_TIMESTAMP
    """

    UPSERT_SECTOR_SQL = """
        INSERT INTO sector (
            sector_code, sector_name, sector_type, latest_price, change_pct
        ) VALUES (
            %(sector_code)s, %(sector_name)s, %(sector_type)s, %(latest_price)s, %(change_pct)s
        )
        ON DUPLICATE KEY UPDATE
            sector_name = VALUES(sector_name),
            latest_price = VALUES(latest_price),
            change_pct = VALUES(change_pct),
            updated_at = CURRENT_TIMESTAMP
    """

    UPSERT_STOCK_SECTOR_REL_SQL = """
        INSERT INTO stock_sector_rel (
            stock_code, sector_code, sector_type, source
        ) VALUES (
            %(stock_code)s, %(sector_code)s, %(sector_type)s, %(source)s
        )
        ON DUPLICATE KEY UPDATE
            source = VALUES(source),
            updated_at = CURRENT_TIMESTAMP
    """

    UPSERT_STOCK_CAPITAL_FLOW_SQL = UPSERT_STOCK_CAPITAL_FLOW_SQL

    def __init__(self, mysql_settings):
        self.mysql_settings = mysql_settings
        self.connection = None
        self.cursor = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls(mysql_settings=get_mysql_settings(crawler.settings))

    def open_spider(self, spider):
        cfg = self.mysql_settings
        try:
            self.connection = pymysql.connect(**cfg)
        except OperationalError as exc:
            if exc.args and exc.args[0] == 1045:
                raise OperationalError(
                    exc.args[0],
                    f"MySQL 认证失败（user={cfg['user']}）。请设置 MYSQL_PASSWORD 后重试。",
                ) from exc
            raise
        self.cursor = self.connection.cursor()
        spider.logger.info(
            "MySQL 已连接: %s:%s/%s",
            cfg["host"],
            cfg["port"],
            cfg["database"],
        )

    def close_spider(self, spider):
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()

    def process_item(self, item, spider):
        data = ItemAdapter(item).asdict()
        try:
            if isinstance(item, StockItem):
                self.cursor.execute(self.UPSERT_STOCK_SQL, data)
            elif isinstance(item, SectorItem):
                self.cursor.execute(self.UPSERT_SECTOR_SQL, data)
            elif isinstance(item, StockSectorRelItem):
                self.cursor.execute(self.UPSERT_STOCK_SECTOR_REL_SQL, data)
            elif isinstance(item, StockCapitalFlowItem):
                self.cursor.execute(self.UPSERT_STOCK_CAPITAL_FLOW_SQL, data)
            else:
                return item
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return item
