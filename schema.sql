CREATE DATABASE IF NOT EXISTS fund_db
    DEFAULT CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE fund_db;

-- 基金基本信息（静态字段，与排行快照分离）
CREATE TABLE IF NOT EXISTS fund (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    fund_code VARCHAR(10) NOT NULL COMMENT '基金代码',
    fund_name VARCHAR(200) NOT NULL COMMENT '基金简称',
    pinyin_abbr VARCHAR(80) DEFAULT NULL COMMENT '拼音缩写',
    establish_date DATE DEFAULT NULL COMMENT '成立日期',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次入库时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY idx_fund (fund_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='基金基本信息';

-- 基金过滤表（静态字段，与排行快照分离）
CREATE TABLE IF NOT EXISTS fund_filter (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    fund_code VARCHAR(10) NOT NULL COMMENT '基金代码',
    fund_name VARCHAR(200) NOT NULL COMMENT '基金简称',
    pinyin_abbr VARCHAR(80) DEFAULT NULL COMMENT '拼音缩写',
    establish_date DATE DEFAULT NULL COMMENT '成立日期',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次入库时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY idx_fund (fund_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='基金过滤表';

-- 基金排行快照（时变字段 + 查询上下文）
CREATE TABLE `fund_ranking` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `fund_code` varchar(10) NOT NULL COMMENT '基金代码',
  `fund_name` varchar(200) NOT NULL COMMENT '基金简称',
  `pinyin_abbr` varchar(80) DEFAULT NULL COMMENT '拼音缩写',
  `nav_date` date DEFAULT NULL COMMENT '净值日期',
  `unit_nav` decimal(12,4) DEFAULT NULL COMMENT '单位净值',
  `accumulated_nav` decimal(12,4) DEFAULT NULL COMMENT '累计净值',
  `daily_growth_rate` decimal(10,2) DEFAULT NULL COMMENT '日增长率(%)',
  `week_1_rate` decimal(10,2) DEFAULT NULL COMMENT '近1周(%)',
  `month_1_rate` decimal(10,2) DEFAULT NULL COMMENT '近1月(%)',
  `month_3_rate` decimal(10,2) DEFAULT NULL COMMENT '近3月(%)',
  `month_6_rate` decimal(10,2) DEFAULT NULL COMMENT '近6月(%)',
  `year_1_rate` decimal(10,2) DEFAULT NULL COMMENT '近1年(%)',
  `year_2_rate` decimal(10,2) DEFAULT NULL COMMENT '近2年(%)',
  `year_3_rate` decimal(10,2) DEFAULT NULL COMMENT '近3年(%)',
  `ytd_rate` decimal(10,2) DEFAULT NULL COMMENT '今年来(%)',
  `since_inception_rate` decimal(10,2) DEFAULT NULL COMMENT '成立来(%)',
  `establish_date` date DEFAULT NULL COMMENT '成立日期',
  `purchase_status` tinyint DEFAULT NULL COMMENT '申购状态代码',
  `interval_growth_rate` decimal(12,4) DEFAULT NULL COMMENT '自定义区间涨幅(%)',
  `original_fee` varchar(32) DEFAULT NULL COMMENT '原手续费',
  `fee_rate` varchar(32) DEFAULT NULL COMMENT '手续费',
  `discount` varchar(32) DEFAULT NULL COMMENT '折扣',
  `discounted_fee` varchar(32) DEFAULT NULL COMMENT '折扣后手续费',
  `query_start_date` date NOT NULL COMMENT '查询区间开始日期',
  `query_end_date` date NOT NULL COMMENT '查询区间结束日期',
  `sort_column` varchar(32) DEFAULT NULL COMMENT '排序字段',
  `page_index` int DEFAULT NULL COMMENT '来源页码',
  `crawled_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '抓取时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_fund` (`fund_code`,`nav_date`)
) ENGINE=InnoDB AUTO_INCREMENT=19748 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='东方财富基金排行数据';

-- 基金历史净值（日频，用于计算任意区间涨跌幅）
CREATE TABLE IF NOT EXISTS fund_nav (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    fund_code VARCHAR(10) NOT NULL COMMENT '基金代码',
    nav_date DATE NOT NULL COMMENT '净值日期',
    unit_nav DECIMAL(12, 4) DEFAULT NULL COMMENT '单位净值',
    accumulated_nav DECIMAL(12, 4) DEFAULT NULL COMMENT '累计净值',
    daily_growth_rate DECIMAL(10, 4) DEFAULT NULL COMMENT '日增长率(%)',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次入库时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_fund_nav (fund_code, nav_date),
    KEY idx_nav_date (nav_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='基金历史净值（日频）';

-- 基金持仓明细（股票持仓，含历史各季度）
CREATE TABLE IF NOT EXISTS fund_holding (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    fund_code VARCHAR(10) NOT NULL COMMENT '基金代码',
    report_date DATE NOT NULL COMMENT '报告截止日期',
    report_year SMALLINT NOT NULL COMMENT '报告年份',
    report_quarter TINYINT NOT NULL COMMENT '报告季度(1-4)',
    stock_code VARCHAR(10) NOT NULL COMMENT '标的代码(股票/债券)',
    stock_name VARCHAR(100) NOT NULL COMMENT '标的名称(股票/债券)',
    rank_num INT DEFAULT NULL COMMENT '持仓序号',
    nav_ratio DECIMAL(10, 2) DEFAULT NULL COMMENT '占净值比例(%)',
    share_count DECIMAL(16, 2) DEFAULT NULL COMMENT '持股数(万股，债券为空)',
    market_value DECIMAL(16, 2) DEFAULT NULL COMMENT '持仓市值(万元)',
    holding_type VARCHAR(16) NOT NULL DEFAULT 'stock' COMMENT '持仓类型: stock/bond',
    crawled_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '抓取时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_holding (fund_code, report_date, stock_code),
    KEY idx_fund_code (fund_code),
    KEY idx_stock_code (stock_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='基金持仓明细';

-- ========== 股票资金流向与板块（data.eastmoney.com/zjlx/list.html） ==========

-- 股票基本信息
CREATE TABLE IF NOT EXISTS stock (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
    stock_name VARCHAR(100) NOT NULL COMMENT '股票简称',
    market VARCHAR(4) DEFAULT NULL COMMENT '市场: SH/SZ/BJ',
    industry_name VARCHAR(100) DEFAULT NULL COMMENT '所属行业(F10细分类)',
    region_board VARCHAR(50) DEFAULT NULL COMMENT '地域板块',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次入库时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_stock_code (stock_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票基本信息';

-- 板块信息（行业/概念）
CREATE TABLE IF NOT EXISTS sector (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    sector_code VARCHAR(16) NOT NULL COMMENT '板块代码(BKxxxx)',
    sector_name VARCHAR(100) NOT NULL COMMENT '板块名称',
    sector_type VARCHAR(8) NOT NULL COMMENT '板块类型: hy行业/gn概念',
    latest_price DECIMAL(16, 2) DEFAULT NULL COMMENT '板块指数/最新价',
    change_pct DECIMAL(10, 2) DEFAULT NULL COMMENT '涨跌幅(%)',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次入库时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_sector (sector_code, sector_type),
    KEY idx_sector_name (sector_name, sector_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='行业/概念板块信息';

-- 股票与板块关联
CREATE TABLE IF NOT EXISTS stock_sector_rel (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
    sector_code VARCHAR(16) NOT NULL COMMENT '板块代码',
    sector_type VARCHAR(8) NOT NULL COMMENT '板块类型: hy/gn',
    source VARCHAR(16) NOT NULL DEFAULT 'constituent' COMMENT '来源: constituent/name_match',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次入库时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_rel (stock_code, sector_code, sector_type),
    KEY idx_stock_code (stock_code),
    KEY idx_sector_code (sector_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票-板块关联';

-- 股票日行情快照（按交易日，支持重复爬取更新）
CREATE TABLE IF NOT EXISTS stock_capital_flow (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
    trade_date DATE NOT NULL COMMENT '交易日期',
    open_price DECIMAL(12, 2) DEFAULT NULL COMMENT '开盘价',
    close_price DECIMAL(12, 2) DEFAULT NULL COMMENT '收盘价',
    high_price DECIMAL(12, 2) DEFAULT NULL COMMENT '最高价',
    low_price DECIMAL(12, 2) DEFAULT NULL COMMENT '最低价',
    pct_change DECIMAL(10, 2) DEFAULT NULL COMMENT '涨跌幅(%)',
    volume BIGINT DEFAULT NULL COMMENT '成交量(股)',
    amount DECIMAL(20, 2) DEFAULT NULL COMMENT '成交额(元)',
    turnover_rate DECIMAL(10, 2) DEFAULT NULL COMMENT '换手率(%)',
    market_cap DECIMAL(20, 2) DEFAULT NULL COMMENT '总市值(元)',
    adj_factor DECIMAL(12, 6) DEFAULT NULL COMMENT '后复权因子',
    close_adj DECIMAL(12, 2) DEFAULT NULL COMMENT '后复权收盘价',
    main_net_inflow DECIMAL(20, 2) DEFAULT NULL COMMENT '主力净流入(元)',
    trade_status TINYINT NOT NULL DEFAULT 0 COMMENT '0正常 1停牌 2ST',
    crawled_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '抓取时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_flow (stock_code, trade_date),
    KEY idx_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票日行情（默认 2016 年起每个交易日）';
