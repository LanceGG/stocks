# Scrapy settings for stocks project

import os

BOT_NAME = "stocks"

SPIDER_MODULES = ["stocks.spiders"]
NEWSPIDER_MODULE = "stocks.spiders"

ROBOTSTXT_OBEY = False

CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY = 1

DEFAULT_REQUEST_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

ITEM_PIPELINES = {
    "stocks.pipelines.MySQLPipeline": 300,
}

# MySQL 连接配置（优先级：local_settings.py > 环境变量 > 默认值）
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "12345678")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "stocks")
MYSQL_CHARSET = os.getenv("MYSQL_CHARSET", "utf8mb4")

# 对应页面 hash:
# tall;c0;r;sqjzf;pn50;ddesc;qsd20250101;qed20260331;qdii;zq;gg;gzbd;gzfs;bbzt;sfbb
FUND_RANK_OP = "ph"
FUND_RANK_DT = "kf"
FUND_RANK_FT = "all"
FUND_RANK_RS = ""
FUND_RANK_GS = "0"
FUND_RANK_SC = "sqjzf"
FUND_RANK_ST = "desc"
FUND_RANK_SD = "2025-01-01"
FUND_RANK_ED = "2026-03-31"
FUND_RANK_PN = 50
FUND_RANK_DX = "1"
FUND_RANK_QDII = ""
FUND_RANK_TAB_SUBTYPE = ",,,,,"

# 0 表示抓取全部页；调试时可设为 2
FUND_RANK_MAX_PAGES = 0

# 基金持仓爬虫
FUND_HOLDING_TOPLINE = 9999
FUND_HOLDING_MAX_FUNDS = 0
FUND_HOLDING_CURRENT_MAX_FUNDS = 0
FUND_HOLDING_SKIP_SYNCED = True

FEED_EXPORT_ENCODING = "utf-8"

try:
    from stocks.local_settings import *  # noqa: F403
except ImportError:
    pass
