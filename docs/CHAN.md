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
| `factor` | `qfq_factor` | 前复权因子（需先有新浪不复权：`close_qfq / close_price`） |

> 缠论请用 **前复权字段**，不要用不复权 `open_price` 等，也不要混用前后复权。

---

## 二、涉及爬虫（仅 2 个）

| 顺序 | 爬虫 | 作用 | 写入表 |
|------|------|------|--------|
| 1 | `stock_capital_flow` | A 股列表（默认仅 `stock` 表） | `stock` |
| 2 | `stock_quarterly_quote` | 日 K + 前复权 | `stock_capital_flow` |

**依赖关系：** 必须先有 `stock` 表数据，再跑 `stock_quarterly_quote`。

---

## 三、数据源说明

### 3.1 新浪（不复权）

| 项目 | 说明 |
|------|------|
| 接口 | `money.finance.sina.com.cn` 日 K |
| 配置 | `STOCK_QUOTE_FETCH_SINA=True` |
| 写入 | `open_price` / `high_price` / `low_price` / `close_price`、`volume`、`pct_change` |
| 请求量 | **1 次/股** |
| 缠论 | 可选；用于算 `qfq_factor`，缠论 OHLC 本身用前复权 |

### 3.2 东方财富 push2his（前复权，**默认**）

| 项目 | 说明 |
|------|------|
| 接口 | `push2his.eastmoney.com`，参数 `fqt=1` |
| 配置 | `STOCK_QUOTE_FETCH_QFQ=True`，`STOCK_QUOTE_QFQ_SOURCE=eastmoney` |
| 写入 | `open_qfq` / `high_qfq` / `low_qfq` / `close_qfq`、`volume` |
| 请求量 | **1 次/股**，2016→今约 2500 根一次返回 |
| 全量耗时 | ~5947 只 × 0.2 秒 ≈ **10～20 分钟**（并发 16） |

### 3.3 腾讯 qfq（前复权，备用）

| 项目 | 说明 |
|------|------|
| 配置 | `STOCK_QUOTE_QFQ_SOURCE=tencent` |
| 请求量 | **6 段/股**，易超时，全量约数十小时 |
| 建议 | 仅在东财不可用时使用 |

新浪与东财/腾讯 **可分开爬**，同表 upsert 合并，互覆盖已有字段。

---

## 四、什么时候全量？什么时候日常增量？

### 4.1 对照表

| 场景 | `STOCK_QUOTE_SKIP_SYNCED` | 何时跑 | 说明 |
|------|---------------------------|--------|------|
| **首次部署** | `False` | 建库后一次 | 全部股票强制重爬 |
| **前复权从未写入** | `False` | 补历史 qfq 时 | 库里有新浪无 `close_qfq` 时必须全量 |
| **换数据源/改起始日** | `False` | 按需 | 如从腾讯改东财、或改 `START_DATE` |
| **每个交易日盘后** | `True`（默认） | 周一至五 20:30 后 | 只爬「未同步」的股票 |
| **每周** | `True` | 可选周一早上 | 刷新 `stock` 列表（新股） |

> **关键：** `SKIP_SYNCED=False` = 全量强制；`SKIP_SYNCED=True` = 日常增量（跳过已齐的股票）。

### 4.2 「增量」是什么意思？

**不是**只补今天 1 根 K 线。

| 情况 | 行为 |
|------|------|
| 该股前复权已齐（2016 起 + 最新交易日 + `close_qfq` 有值） | **跳过** |
| 缺历史、缺最新日、或 `close_qfq` 为空 | **整只重拉**（东财 1 次/股，约 2500 根） |

日常增量下，**绝大多数已同步股票会被跳过**，只处理新股或未齐的旧股，所以盘后通常很快。

### 4.3 跳过条件（`SKIP_SYNCED=True`）

按当前爬取开关分别判断：

**爬前复权时（`FETCH_QFQ=True`）还需：**

- `close_qfq IS NOT NULL` 且覆盖 `STOCK_QUOTE_START_DATE` 至最近交易日

**爬新浪时（`FETCH_SINA=True`）还需：**

- 已有不复权日 K 且日期范围齐

---

## 五、推荐命令

### 5.1 首次部署（全量）

```bash
cd /path/to/stocks

# 0. 建表（若未建）
mysql -u root -p stocks < schema.sql   # 或只执行股票相关建表语句

# 1. 股票列表
scrapy crawl stock_capital_flow

# 2a. 方案 A（推荐）：只爬东财前复权，最快
scrapy crawl stock_quarterly_quote \
  -s STOCK_QUOTE_FETCH_SINA=False \
  -s STOCK_QUOTE_FETCH_QFQ=True \
  -s STOCK_QUOTE_QFQ_SOURCE=eastmoney \
  -s STOCK_QUOTE_SKIP_SYNCED=False

# 2b. 方案 B：先新浪不复权，再东财前复权（可算 qfq_factor）
scrapy crawl stock_quarterly_quote \
  -s STOCK_QUOTE_FETCH_SINA=True \
  -s STOCK_QUOTE_FETCH_QFQ=False \
  -s STOCK_QUOTE_SKIP_SYNCED=False

scrapy crawl stock_quarterly_quote \
  -s STOCK_QUOTE_FETCH_SINA=False \
  -s STOCK_QUOTE_FETCH_QFQ=True \
  -s STOCK_QUOTE_QFQ_SOURCE=eastmoney \
  -s STOCK_QUOTE_SKIP_SYNCED=False
```

### 5.2 日常增量（每个交易日盘后）

```bash
# 补前复权 + 成交量（缠论够用）
scrapy crawl stock_quarterly_quote \
  -s STOCK_QUOTE_FETCH_SINA=False \
  -s STOCK_QUOTE_FETCH_QFQ=True \
  -s STOCK_QUOTE_QFQ_SOURCE=eastmoney
# 默认 SKIP_SYNCED=True，无需额外参数
```

若也需要 `qfq_factor`，每周或每日再加一步新浪（仅未齐时会爬）：

```bash
scrapy crawl stock_quarterly_quote \
  -s STOCK_QUOTE_FETCH_SINA=True \
  -s STOCK_QUOTE_FETCH_QFQ=False
```

### 5.3 每周刷新股票列表（可选）

```bash
scrapy crawl stock_capital_flow
```

### 5.4 调试（少量股票）

```bash
scrapy crawl stock_quarterly_quote \
  -s STOCK_QUOTE_MAX_STOCKS=10 \
  -s STOCK_QUOTE_FETCH_SINA=False \
  -s STOCK_QUOTE_FETCH_QFQ=True \
  -s STOCK_QUOTE_QFQ_SOURCE=eastmoney \
  -s STOCK_QUOTE_SKIP_SYNCED=False
```

---

## 六、关键配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `STOCK_CAPITAL_FLOW_LIST_ONLY` | `True` | 列表爬虫只写 `stock` 表 |
| `STOCK_QUOTE_START_DATE` | `2016-01-01` | 历史起点 |
| `STOCK_QUOTE_SKIP_SYNCED` | `True` | 日常 `True`；**全量必须 `False`** |
| `STOCK_QUOTE_FETCH_SINA` | `True` | 新浪不复权 |
| `STOCK_QUOTE_FETCH_QFQ` | `True` | 前复权 |
| `STOCK_QUOTE_QFQ_SOURCE` | `eastmoney` | `eastmoney`（快）或 `tencent`（慢） |
| `STOCK_QUOTE_FETCH_HFQ` | `False` | 后复权，缠论不需要 |
| `STOCK_QUOTE_CONCURRENT_REQUESTS` | `16` | 东财可 16；腾讯建议 4 |
| `STOCK_QUOTE_MAX_STOCKS` | `0` | 0=不限，调试可设 10 |

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

---

## 八、进度检查

```sql
-- 有前复权的股票数（全量后应接近 stock 表总数）
SELECT COUNT(DISTINCT stock_code) AS stocks_with_qfq
FROM stock_capital_flow
WHERE close_qfq IS NOT NULL;

-- 尚未有前复权的股票数
SELECT COUNT(*) AS missing_qfq
FROM stock s
WHERE NOT EXISTS (
  SELECT 1 FROM stock_capital_flow f
  WHERE f.stock_code = s.stock_code AND f.close_qfq IS NOT NULL
);

-- 单只样例
SELECT stock_code,
       COUNT(*) AS total,
       SUM(close_qfq IS NOT NULL) AS qfq_rows,
       MIN(CASE WHEN close_qfq IS NOT NULL THEN trade_date END) AS qfq_from,
       MAX(CASE WHEN close_qfq IS NOT NULL THEN trade_date END) AS qfq_to
FROM stock_capital_flow
WHERE stock_code = '000001'
GROUP BY stock_code;
```

---

## 九、定时任务示例

```cron
# 每个交易日 20:40 — 前复权日增量（缠论）
40 20 * * 1-5 cd /path/to/stocks && scrapy crawl stock_quarterly_quote \
  -s STOCK_QUOTE_FETCH_SINA=False \
  -s STOCK_QUOTE_FETCH_QFQ=True \
  -s STOCK_QUOTE_QFQ_SOURCE=eastmoney \
  >> logs/stock_qfq.log 2>&1

# 每周一 08:00 — 刷新股票列表
0 8 * * 1 cd /path/to/stocks && scrapy crawl stock_capital_flow >> logs/stock_list.log 2>&1
```

---

## 十、常见问题

**Q: 为什么必须用前复权？**  
A: 除权除息会在不复权 K 线上产生价格跳空，导致分型、笔划分失真。前复权适合缠论回溯。

**Q: 东财和腾讯选哪个？**  
A: 默认 **东财**（`QFQ_SOURCE=eastmoney`），1 次/股、全量约十几分钟。腾讯需 6 段/股，慢且易超时。

**Q: 日常增量会只补今天一根 K 吗？**  
A: 不会。已齐的整只跳过；未齐的整只重拉（东财仍 1 次 HTTP），靠 upsert 覆盖。

**Q: 全量和增量的唯一开关？**  
A: `-s STOCK_QUOTE_SKIP_SYNCED=False` 全量；默认 `True` 为日常增量。

**Q: 只有前复权、没有 `qfq_factor`？**  
A: 只跑了东财、没跑新浪。补跑：`FETCH_SINA=True, FETCH_QFQ=False`。

**Q: `close_qfq` 一直是 NULL？**  
A: 用东财全量重跑：`FETCH_QFQ=True, QFQ_SOURCE=eastmoney, SKIP_SYNCED=False`。若用腾讯，确认 `QFQ_BAR_COUNT<=2000`。

---

## 十一、快速命令备忘

```bash
# ── 首次全量 ──
scrapy crawl stock_capital_flow
scrapy crawl stock_quarterly_quote \
  -s STOCK_QUOTE_FETCH_SINA=False \
  -s STOCK_QUOTE_FETCH_QFQ=True \
  -s STOCK_QUOTE_QFQ_SOURCE=eastmoney \
  -s STOCK_QUOTE_SKIP_SYNCED=False

# ── 日常增量（盘后）──
scrapy crawl stock_quarterly_quote \
  -s STOCK_QUOTE_FETCH_SINA=False \
  -s STOCK_QUOTE_FETCH_QFQ=True \
  -s STOCK_QUOTE_QFQ_SOURCE=eastmoney

# ── 调试 ──
scrapy crawl stock_quarterly_quote \
  -s STOCK_QUOTE_MAX_STOCKS=10 \
  -s STOCK_QUOTE_SKIP_SYNCED=False \
  -s STOCK_QUOTE_QFQ_SOURCE=eastmoney
```
