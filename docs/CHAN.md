# 缠论股票数据指南

本文档说明如何用本项目爬取、存储和导出 **缠论分析所需的 A 股日线数据**（前复权 OHLC + 成交量 + 因子）。不涉及基金爬虫。

---

## 一、缠论需要什么数据

缠论分型、笔、线段、中枢等分析，通常基于 **日线 K 线**，且价格应使用 **前复权**，避免除权除息造成假突破/假分型。

| 缠论侧字段 | 本项目来源 | 说明 |
|-----------|-----------|------|
| `code` | `stock.stock_code` + `stock.market` | 如 `600519.SH` |
| `datetime` | `stock_capital_flow.trade_date` | 交易日，日线粒度 |
| `open` / `high` / `low` / `close` | `open_qfq` / `high_qfq` / `low_qfq` / `close_qfq` | **前复权** OHLC |
| `volume` | `volume` | 成交量（股） |
| `factor` | `qfq_factor` | 前复权因子（`close_qfq / close_price`） |

> 缠论请用 **前复权字段**，不要用不复权 `open_price` 等，也不要混用前后复权。

---

## 二、涉及爬虫（仅 2 个）

| 顺序 | 爬虫 | 作用 | 写入表 |
|------|------|------|--------|
| 1 | `stock_capital_flow` | A 股列表、板块（列表是日 K 的前置） | `stock`、`sector`、`stock_sector_rel` |
| 2 | `stock_quarterly_quote` | 日 K + 前复权（可选后复权） | `stock_capital_flow` |

**依赖关系：** 必须先有 `stock` 表数据，再跑 `stock_quarterly_quote`。

---

## 三、环境准备

```bash
cd /path/to/stocks
pip install -r requirements.txt

cp stocks/local_settings.py.example stocks/local_settings.py
# 编辑 MYSQL_PASSWORD、MYSQL_DATABASE（默认 stocks）
```

MySQL 需已建好股票相关表。`schema.sql` 中股票部分从 `-- 股票基本信息` 起，包含：

- `stock`
- `sector`、`stock_sector_rel`（列表爬虫附带写入，缠论可忽略）
- `stock_capital_flow`

> `schema.sql` 文件头默认 `USE fund_db`，与爬虫默认库名 `stocks` 不一致。缠论单独建库时，请创建 `stocks` 库并执行股票相关建表语句，或改 `local_settings.py` 中的 `MYSQL_DATABASE` 与建库一致。

---

## 四、爬取命令

### 4.1 首次部署

```bash
# 1. 股票列表
scrapy crawl stock_capital_flow

# 2. 日 K + 前复权（2016 起全量，强制重爬）
scrapy crawl stock_quarterly_quote \
  -s STOCK_QUOTE_SKIP_SYNCED=False \
  -s STOCK_QUOTE_FETCH_HFQ=False
```

### 4.2 日常更新（每个交易日盘后）

```bash
scrapy crawl stock_quarterly_quote \
  -s STOCK_QUOTE_FETCH_HFQ=False
```

### 4.3 调试（少量股票）

```bash
scrapy crawl stock_quarterly_quote \
  -s STOCK_QUOTE_MAX_STOCKS=10 \
  -s STOCK_QUOTE_FETCH_HFQ=False
```

### 4.4 仅更新股票列表（新股、退市变更时）

```bash
scrapy crawl stock_capital_flow
```

---

## 五、关键配置

在命令行用 `-s KEY=VALUE` 覆盖，或写入 `stocks/local_settings.py`。

| 配置项 | 默认值 | 缠论建议 |
|--------|--------|----------|
| `STOCK_QUOTE_START_DATE` | `2016-01-01` | 历史起点，可按需改早 |
| `STOCK_QUOTE_SKIP_SYNCED` | `True` | 日常保持 `True`；全量回填设 `False` |
| `STOCK_QUOTE_FETCH_HFQ` | `True` | **建议 `False`**，缠论不需要后复权，每只少 1 次 HTTP |
| `STOCK_QUOTE_MAX_STOCKS` | `0`（不限） | 调试时设小值 |
| `STOCK_QUOTE_QFQ_BAR_COUNT` | `2000` | 腾讯前复权单次条数 |
| `STOCK_QUOTE_CONCURRENT_REQUESTS` | `32` | 并发，过高可能被限流 |

---

## 六、数据源与写入逻辑

**`stock_quarterly_quote` 数据流：**

1. **新浪** — 不复权日 K（OHLC、成交量、成交额等）
2. **腾讯 `qfqday`** — 前复权 OHLC，合并写入 `open_qfq` 等字段
3. **腾讯 `hfqday`**（仅当 `STOCK_QUOTE_FETCH_HFQ=True`）— 后复权，缠论可跳过

写入表：`stock_capital_flow`，按 `(stock_code, trade_date)` 唯一键 upsert，重复爬取会覆盖更新。

### 跳过逻辑（`STOCK_QUOTE_SKIP_SYNCED=True`）

满足以下**全部**条件才跳过该股票：

- 已有 `stock_capital_flow` 数据
- 最早交易日 ≤ `STOCK_QUOTE_START_DATE`
- 最新交易日 ≥ 最近一个交易日
- `close_qfq IS NOT NULL`（前复权已齐）

否则整只重拉约 4000 根 K 线（无「只补最新几条」的 incremental 模式）。

---

## 七、数据导出

### 7.1 单只股票

```sql
SELECT
  CONCAT(s.stock_code, '.', s.market) AS code,
  f.trade_date                        AS datetime,
  f.open_qfq                          AS open,
  f.high_qfq                          AS high,
  f.low_qfq                           AS low,
  f.close_qfq                         AS close,
  f.volume,
  f.qfq_factor                        AS factor
FROM stock_capital_flow f
JOIN stock s ON s.stock_code = f.stock_code
WHERE f.stock_code = '600519'
  AND f.close_qfq IS NOT NULL
ORDER BY f.trade_date;
```

### 7.2 批量导出

```sql
SELECT
  CONCAT(s.stock_code, '.', s.market) AS code,
  f.trade_date                        AS datetime,
  f.open_qfq AS open, f.high_qfq AS high,
  f.low_qfq AS low, f.close_qfq AS close,
  f.volume, f.qfq_factor AS factor
FROM stock_capital_flow f
JOIN stock s ON s.stock_code = f.stock_code
WHERE f.close_qfq IS NOT NULL
ORDER BY f.stock_code, f.trade_date;
```

### 7.3 JSON 形态示例

```json
{
  "code": "600519.SH",
  "datetime": "2026-06-26",
  "open": 1650.00,
  "high": 1680.50,
  "low": 1642.10,
  "close": 1675.00,
  "volume": 45000,
  "factor": 1.234567
}
```

---

## 八、进度检查

```sql
-- 股票总数
SELECT COUNT(*) FROM stock;

-- 日 K 行数 / 有前复权的行数
SELECT COUNT(*) AS total_rows,
       SUM(close_qfq IS NOT NULL) AS qfq_rows
FROM stock_capital_flow;

-- 日期范围
SELECT MIN(trade_date), MAX(trade_date) FROM stock_capital_flow;

-- 尚未有前复权的股票数
SELECT COUNT(*) AS missing_qfq
FROM stock s
WHERE NOT EXISTS (
  SELECT 1 FROM stock_capital_flow f
  WHERE f.stock_code = s.stock_code AND f.close_qfq IS NOT NULL
);
```

---

## 九、定时任务示例

每个交易日 **20:30 后**（A 股收盘且日 K 已更新）：

```cron
40 20 * * 1-5 cd /path/to/stocks && scrapy crawl stock_quarterly_quote -s STOCK_QUOTE_FETCH_HFQ=False >> logs/stock_quote.log 2>&1
```

每周一次刷新股票列表（可选）：

```cron
0 8 * * 1 cd /path/to/stocks && scrapy crawl stock_capital_flow >> logs/stock_list.log 2>&1
```

---

## 十、常见问题

**Q: 为什么必须用前复权？**  
A: 除权除息会在不复权 K 线上产生价格跳空，导致分型、笔的划分与真实走势不一致。前复权将历史价格按最新股本对齐，适合缠论回溯分析。

**Q: `STOCK_QUOTE_FETCH_HFQ=False` 有什么影响？**  
A: 只不写后复权字段（`open_hfq` 等），前复权与成交量不受影响，缠论无影响，爬取更快。

**Q: 日常增量会只补今天一根 K 吗？**  
A: 不会。未「完全同步」的股票会整段重拉；已同步的跳过。靠 upsert 覆盖，不会重复插行。

**Q: 表里没有 `close_qfq` 列怎么办？**  
A: 说明库表是旧版，需用 `schema.sql` 中 `stock_capital_flow` 最新定义重建或手动加列，然后 `-s STOCK_QUOTE_SKIP_SYNCED=False` 重爬。

**Q: 和 `docs/OPERATIONS.md` 的关系？**  
A: `OPERATIONS.md` 覆盖全部 8 个爬虫；本文档只保留缠论相关的 2 个股票爬虫与导出方式。

---

## 十一、快速命令备忘

```bash
# 首次
scrapy crawl stock_capital_flow
scrapy crawl stock_quarterly_quote -s STOCK_QUOTE_SKIP_SYNCED=False -s STOCK_QUOTE_FETCH_HFQ=False

# 日常
scrapy crawl stock_quarterly_quote -s STOCK_QUOTE_FETCH_HFQ=False

# 调试
scrapy crawl stock_quarterly_quote -s STOCK_QUOTE_MAX_STOCKS=10 -s STOCK_QUOTE_FETCH_HFQ=False
```
