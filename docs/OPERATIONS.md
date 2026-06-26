# 爬虫操作文档

本文说明各爬虫的用途、更新频率与推荐执行顺序。

## 前置条件

```bash
pip install -r requirements.txt
cp stocks/local_settings.py.example stocks/local_settings.py
# 编辑 MYSQL_PASSWORD

mysql -u root -p stocks < schema.sql
# 已有库增量建表：
mysql -u root -p stocks < migrations/004_fund_nav.sql
```

工作目录：

```bash
cd /Users/rain/Documents/code/stocks
```

---

## 一、数据表与爬虫对照

| 表 | 爬虫 | 数据性质 |
|----|------|----------|
| `fund` | `fund` | 基金基本信息 |
| `fund_ranking` | `fund` | 排行快照（含近1周/1月等滚动涨幅） |
| `fund_nav` | `fund_nav` | **历史日净值**（算任意区间涨跌幅） |
| `fund_holding` | `fund_holding` | 历史持仓（按季度） |
| `fund_filter` | `fund_holding` 写入 | 无持仓基金过滤 |
| `stock` / `sector` | `stock_capital_flow` | 股票与板块 |
| `stock_capital_flow` | `stock_quarterly_quote` | 股票日 K |

---

## 二、哪些数据需要每日爬取？

基金净值通常在 **每个交易日 19:00~23:00** 陆续公布（QDII 可能更晚）。建议工作日晚间跑批。

### 每日必跑（交易日）

| 优先级 | 爬虫 | 写入表 | 说明 |
|--------|------|--------|------|
| ★★★ | `fund` | `fund`、`fund_ranking` | 同步基金列表 + **当日排行快照**（近1周/1月/今年来等） |
| ★★★ | `fund_nav` | `fund_nav` | **最新净值**（默认只抓第 1 页，已同步则跳过） |
| ★★★ | `stock_quarterly_quote` | `stock_capital_flow` | 股票最新日 K（已同步则跳过） |

```bash
scrapy crawl fund
scrapy crawl fund_nav
scrapy crawl stock_quarterly_quote
```

### 每周 / 按需

| 爬虫 | 频率 | 说明 |
|------|------|------|
| `stock_capital_flow` | 每周 1 次 | 股票列表、板块、关联关系变化不频繁 |
| `fund_holding_current` | 季度末后 | 仅更新**当前最新季度**持仓 |

```bash
scrapy crawl stock_capital_flow
scrapy crawl fund_holding_current
```

### 一次性 / 低频回填

| 爬虫 | 时机 | 说明 |
|------|------|------|
| `fund_nav` 全量 | 首次 | 2016 年起历史净值，耗时长 |
| `fund_holding` | 首次 | 全历史持仓 |
| `stock_quarterly_quote` 全量 | 首次 | 2016 年起股票日 K |

---

## 三、各爬虫命令详解

### 1. fund — 基金排行（保留 fund_ranking）

```bash
scrapy crawl fund
```

- 写入：`fund` + `fund_ranking`
- `fund_ranking` 保存**爬取当日**的滚动涨幅（近1周/1月/3月/1年/今年来等），适合横向对比，**不能替代历史净值序列**
- 调试：`scrapy crawl fund -s FUND_RANK_MAX_PAGES=2`

### 2. fund_nav — 基金历史净值（新增）

```bash
# 日增量（推荐，默认跳过已同步基金）
scrapy crawl fund_nav

# 首次全量回填（2016 年起，分页抓取全部历史）
scrapy crawl fund_nav -s FUND_NAV_FULL_BACKFILL=True -s FUND_NAV_SKIP_SYNCED=False

# 调试单只基金
scrapy crawl fund_nav -s FUND_NAV_MAX_FUNDS=5
```

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `FUND_NAV_START_DATE` | 2016-01-01 | 回填起始日 |
| `FUND_NAV_SKIP_SYNCED` | True | 已同步至最近净值日则跳过 |
| `FUND_NAV_FULL_BACKFILL` | False | True=强制全分页回填 |
| `FUND_NAV_PAGE_SIZE` | 49 | 每页条数 |

**区间涨跌幅查询示例：**

```sql
-- 某基金 2020-01-01 ~ 2024-12-31 累计涨跌幅（用累计净值）
SELECT
  a.fund_code,
  (b.accumulated_nav / a.accumulated_nav - 1) * 100 AS return_pct
FROM fund_nav a
JOIN fund_nav b ON a.fund_code = b.fund_code
WHERE a.fund_code = '000001'
  AND a.nav_date = (
    SELECT MIN(nav_date) FROM fund_nav
    WHERE fund_code = '000001' AND nav_date >= '2020-01-01'
  )
  AND b.nav_date = (
    SELECT MAX(nav_date) FROM fund_nav
    WHERE fund_code = '000001' AND nav_date <= '2024-12-31'
  );
```

### 3. fund_holding — 全量历史持仓

```bash
scrapy crawl fund_holding
scrapy crawl fund_holding -s FUND_HOLDING_SKIP_SYNCED=False  # 强制重爬
```

- 季报披露后（1/4/7/10 月）可再跑 `fund_holding_current`

### 4. stock_quarterly_quote — 股票日 K

```bash
scrapy crawl stock_quarterly_quote
```

### 5. stock_capital_flow — 股票/板块

```bash
scrapy crawl stock_capital_flow
```

---

## 四、推荐每日调度（crontab 示例）

```cron
# 每个交易日 20:30（周一至周五）
30 20 * * 1-5 cd /path/to/stocks && scrapy crawl fund >> logs/fund.log 2>&1
35 20 * * 1-5 cd /path/to/stocks && scrapy crawl fund_nav >> logs/fund_nav.log 2>&1
40 20 * * 1-5 cd /path/to/stocks && scrapy crawl stock_quarterly_quote >> logs/stock.log 2>&1

# 每周日 02:00 同步股票板块
0 2 * * 0 cd /path/to/stocks && scrapy crawl stock_capital_flow >> logs/stock_cf.log 2>&1
```

---

## 五、首次部署顺序

按依赖关系依次执行：

```bash
# 1. 基金列表 + 排行快照
scrapy crawl fund

# 2. 基金历史净值（全量，耗时最长之一）
scrapy crawl fund_nav -s FUND_NAV_FULL_BACKFILL=True -s FUND_NAV_SKIP_SYNCED=False

# 3. 基金持仓（全量）
scrapy crawl fund_holding

# 4. 股票/板块
scrapy crawl stock_capital_flow

# 5. 股票日 K（全量）
scrapy crawl stock_quarterly_quote -s STOCK_QUOTE_SKIP_SYNCED=False
```

---

## 六、fund_ranking vs fund_nav 如何配合

| 场景 | 用哪张表 |
|------|----------|
| 今日排行、近1月收益 Top 50 | `fund_ranking`（最新一次 `fund` 爬取） |
| 2023 全年涨跌幅 | `fund_nav` 按区间计算 |
| 成立以来的净值曲线 | `fund_nav` |
| 对比「近1月」在不同日期的变化 | 需每日存 `fund_ranking` 快照历史（当前表会 upsert 覆盖同日） |

> 若未来需要「滚动涨幅的历史变化」，可另建 `fund_ranking_history` 表，每日 `fund` 爬取时 append 而非 upsert。当前设计保留 `fund_ranking` 为**最新快照**。

---

## 七、常见问题

**Q: fund_nav 日增量会抓多少数据？**  
A: 默认只抓第 1 页（约 49 条），实际每日新增 1 条净值，足够覆盖。

**Q: 新基金如何入库？**  
A: 先跑 `fund` 写入 `fund` 表，再跑 `fund_nav` 自动全量回填该基金的净值历史。

**Q: 如何验证同步进度？**

```sql
SELECT COUNT(DISTINCT fund_code) AS funds, COUNT(*) AS rows,
       MIN(nav_date), MAX(nav_date)
FROM fund_nav;

SELECT f.fund_code, f.fund_name, n.latest
FROM fund f
LEFT JOIN (
  SELECT fund_code, MAX(nav_date) AS latest FROM fund_nav GROUP BY fund_code
) n ON f.fund_code = n.fund_code
WHERE n.latest IS NULL OR n.latest < CURDATE() - INTERVAL 3 DAY
LIMIT 20;
```
