"""Scrapy 项目全局配置。"""

import os

# 项目标识
BOT_NAME = "stocks"

SPIDER_MODULES = ["stocks.spiders"]
NEWSPIDER_MODULE = "stocks.spiders"

# 不遵守 robots.txt（东方财富 API 需直接访问）
ROBOTSTXT_OBEY = False

# 全局默认限速（fund 排行爬虫使用；fund_holding 在 spider 内单独覆盖）
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY = 1

# 默认 HTTP 请求头
DEFAULT_REQUEST_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 默认 Pipeline：基金排行写入 MySQL（持仓爬虫在 spider 内覆盖为空）
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

# 基金排行 API 参数（对应页面 hash:
# tall;c0;r;sqjzf;pn50;ddesc;qsd20250101;qed20260331;qdii;zq;gg;gzbd;gzfs;bbzt;sfbb）
FUND_RANK_OP = "ph"  # 操作类型
FUND_RANK_DT = "kf"  # 基金类型：开放型
FUND_RANK_FT = "all"  # 全部基金
FUND_RANK_RS = ""  # 排序规则
FUND_RANK_GS = "0"
FUND_RANK_SC = "sqjzf"  # 排序字段：区间涨幅
FUND_RANK_ST = "desc"  # 降序
FUND_RANK_SD = "2025-01-01"  # 查询区间开始
FUND_RANK_ED = "2026-03-31"  # 查询区间结束
FUND_RANK_PN = 50  # 每页条数
FUND_RANK_DX = "1"
FUND_RANK_QDII = ""
FUND_RANK_TAB_SUBTYPE = ",,,,,"

# 0 表示抓取全部页；调试时可设为 2
FUND_RANK_MAX_PAGES = 0

# 基金持仓爬虫参数
FUND_HOLDING_TOPLINE = 9999  # 每季度最多返回持仓条数
FUND_HOLDING_MAX_FUNDS = 0  # 0=不限，调试时可设小值
FUND_HOLDING_CURRENT_MAX_FUNDS = 0  # 当前季度爬虫专用上限，0=沿用 MAX_FUNDS
FUND_HOLDING_SKIP_SYNCED = True  # 跳过数据库已有数据的基金
# fund_holding 并发参数（可通过 -s 或 local_settings.py 覆盖）
FUND_HOLDING_CONCURRENT_REQUESTS = 16  # 全局最大并发请求数
FUND_HOLDING_CONCURRENT_REQUESTS_PER_DOMAIN = 8  # 单域名并发
FUND_HOLDING_DOWNLOAD_DELAY = 0  # 请求间隔（秒），0=不限速

# 股票资金流向爬虫（data.eastmoney.com/zjlx/list.html）
STOCK_CAPITAL_FLOW_PAGE_SIZE = 50  # 每页条数
STOCK_CAPITAL_FLOW_MAX_PAGES = 0  # 0=全部页，调试可设 2
SECTOR_CONSTITUENT_MAX_SECTORS = 0  # 0=抓取全部板块成分股
# push2 API 域名优先级（push2 主站易断连，默认走 push2delay）
EASTMONEY_PUSH2_HOSTS = [
    "push2delay.eastmoney.com",
    "82.push2.eastmoney.com",
    "push2.eastmoney.com",
]

FEED_EXPORT_ENCODING = "utf-8"

# 本地覆盖配置（含 MYSQL_PASSWORD 等敏感信息，不提交 git）
try:
    from stocks.local_settings import *  # noqa: F403
except ImportError:
    pass
