# 东方财富基金爬虫

基于 Scrapy 抓取 [东方财富基金排行页](https://fund.eastmoney.com/data/fundranking.html) 对应 API 数据，并写入本地 MySQL。

## 1. 安装依赖

```bash
pip install -r requirements.txt
```

## 2. 初始化数据库

```bash
mysql -u root -p stocks < schema.sql
```

## 3. 配置 MySQL

```bash
cp stocks/local_settings.py.example stocks/local_settings.py
# 编辑 stocks/local_settings.py，填写 MYSQL_PASSWORD
```

## 4. 运行爬虫

抓取基金排行（同时写入 `fund` 和 `fund_ranking`）：

```bash
cd /Users/rain/Documents/code/stocks
scrapy crawl fund
```

抓取基金持仓（遍历 `fund` 表，含股票与债券历史各季度持仓）：

```bash
scrapy crawl fund_holding
```

默认会跳过 `fund_holding` 表中已有任意持仓数据的基金，如需全量重爬：

```bash
scrapy crawl fund_holding -s FUND_HOLDING_SKIP_SYNCED=False
```

只抓取当前最新季度持仓（适合日常增量更新）：

```bash
scrapy crawl fund_holding_current
```

同样默认跳过 `fund_holding` 表中已有当前季度数据的基金，全量重爬：

```bash
scrapy crawl fund_holding_current -s FUND_HOLDING_SKIP_SYNCED=False
```

调试：

```bash
scrapy crawl fund -s FUND_RANK_MAX_PAGES=2
scrapy crawl fund_holding -s FUND_HOLDING_MAX_FUNDS=1
scrapy crawl fund_holding_current -s FUND_HOLDING_CURRENT_MAX_FUNDS=1
```

## 5. 数据表

| 表 | 说明 |
|---|---|
| `fund` | 基金基本信息 |
| `fund_ranking` | 排行快照 |
| `fund_holding` | 持仓明细（股票/债券） |
| `fund_filter` | 无持仓基金过滤表（`fund_holding` 写入，`fund_holding_current` 读取跳过） |
