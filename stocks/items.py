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
