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
