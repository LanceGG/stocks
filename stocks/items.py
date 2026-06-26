"""Scrapy Item 定义：基金排行与持仓数据结构。"""

import scrapy


class FundRankingItem(scrapy.Item):
    """东方财富基金排行 API 单条记录。"""

    fund_code = scrapy.Field()  # 基金代码
    fund_name = scrapy.Field()  # 基金简称
    pinyin_abbr = scrapy.Field()  # 拼音缩写
    nav_date = scrapy.Field()  # 净值日期
    unit_nav = scrapy.Field()  # 单位净值
    accumulated_nav = scrapy.Field()  # 累计净值
    daily_growth_rate = scrapy.Field()  # 日增长率(%)
    week_1_rate = scrapy.Field()  # 近1周(%)
    month_1_rate = scrapy.Field()  # 近1月(%)
    month_3_rate = scrapy.Field()  # 近3月(%)
    month_6_rate = scrapy.Field()  # 近6月(%)
    year_1_rate = scrapy.Field()  # 近1年(%)
    year_2_rate = scrapy.Field()  # 近2年(%)
    year_3_rate = scrapy.Field()  # 近3年(%)
    ytd_rate = scrapy.Field()  # 今年来(%)
    since_inception_rate = scrapy.Field()  # 成立来(%)
    establish_date = scrapy.Field()  # 成立日期
    purchase_status = scrapy.Field()  # 申购状态代码
    interval_growth_rate = scrapy.Field()  # 自定义区间涨幅(%)
    original_fee = scrapy.Field()  # 原手续费
    fee_rate = scrapy.Field()  # 手续费
    discount = scrapy.Field()  # 折扣
    discounted_fee = scrapy.Field()  # 折扣后手续费
    query_start_date = scrapy.Field()  # 查询区间开始日期
    query_end_date = scrapy.Field()  # 查询区间结束日期
    sort_column = scrapy.Field()  # 排序字段
    page_index = scrapy.Field()  # 来源页码


class FundHoldingItem(scrapy.Item):
    """基金持仓明细单条记录（股票/债券）。"""

    fund_code = scrapy.Field()  # 基金代码
    report_date = scrapy.Field()  # 报告截止日期
    report_year = scrapy.Field()  # 报告年份
    report_quarter = scrapy.Field()  # 报告季度(1-4)
    stock_code = scrapy.Field()  # 标的代码
    stock_name = scrapy.Field()  # 标的名称
    rank_num = scrapy.Field()  # 持仓序号
    nav_ratio = scrapy.Field()  # 占净值比例(%)
    share_count = scrapy.Field()  # 持股数(万股)
    market_value = scrapy.Field()  # 持仓市值(万元)
    holding_type = scrapy.Field()  # 持仓类型: stock/bond


class StockItem(scrapy.Item):
    """股票基本信息。"""

    stock_code = scrapy.Field()  # 股票代码
    stock_name = scrapy.Field()  # 股票简称
    market = scrapy.Field()  # 市场: SH/SZ/BJ
    industry_name = scrapy.Field()  # 所属行业(F10)
    region_board = scrapy.Field()  # 地域板块


class SectorItem(scrapy.Item):
    """行业/概念板块信息。"""

    sector_code = scrapy.Field()  # 板块代码 BKxxxx
    sector_name = scrapy.Field()  # 板块名称
    sector_type = scrapy.Field()  # hy行业 / gn概念
    latest_price = scrapy.Field()  # 板块指数
    change_pct = scrapy.Field()  # 涨跌幅(%)


class StockSectorRelItem(scrapy.Item):
    """股票与板块关联。"""

    stock_code = scrapy.Field()  # 股票代码
    sector_code = scrapy.Field()  # 板块代码
    sector_type = scrapy.Field()  # hy / gn
    source = scrapy.Field()  # constituent / name_match


class StockCapitalFlowItem(scrapy.Item):
    """股票日行情快照（按交易日）。"""

    stock_code = scrapy.Field()  # 股票代码
    trade_date = scrapy.Field()  # 交易日期
    open_price = scrapy.Field()  # 开盘价
    close_price = scrapy.Field()  # 收盘价
    high_price = scrapy.Field()  # 最高价
    low_price = scrapy.Field()  # 最低价
    pct_change = scrapy.Field()  # 涨跌幅(%)
    volume = scrapy.Field()  # 成交量(股)
    amount = scrapy.Field()  # 成交额(元)
    turnover_rate = scrapy.Field()  # 换手率(%)
    market_cap = scrapy.Field()  # 总市值(元)
    adj_factor = scrapy.Field()  # 后复权因子
    close_adj = scrapy.Field()  # 后复权收盘价
    main_net_inflow = scrapy.Field()  # 主力净流入(元)
    trade_status = scrapy.Field()  # 0正常 1停牌 2ST
