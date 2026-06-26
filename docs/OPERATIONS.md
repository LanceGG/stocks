# 爬虫操作文档

本文说明项目中 **全部 8 个爬虫** 的用途、命令、配置项、同步策略与推荐调度。

---

## 目录

1. [环境与初始化](#一环境与初始化)
2. [配置说明](#二配置说明)
3. [爬虫速查表](#三爬虫速查表)
4. [数据表与爬虫对照](#四数据表与爬虫对照)
5. [同步策略对比](#五同步策略对比)
6. [更新频率建议](#六更新频率建议)
7. [各爬虫详解](#七各爬虫详解)
8. [首次部署顺序](#八首次部署顺序)
9. [定时调度（crontab）](#九定时调度crontab)
10. [进度验证 SQL](#十进度验证-sql)
11. [常见问题](#十一常见问题)

---

## 一、环境与初始化

### 1.1 安装依赖

```bash
cd /Users/rain/Documents/code/stocks
pip install -r requirements.txt
```

### 1.2 配置 MySQL

```bash
cp stocks/local_settings.py.example stocks/local_settings.py
# 编辑 stocks/local_settings.py，填写 MYSQL_PASSWORD、MYSQL_DATABASE 等
```

配置优先级：`local_settings.py` > 环境变量 > `settings.py` 默认值。

默认连接（`settings.py`）：

| 项 | 默认值 |
|----|--------|
| `MYSQL_HOST` | 127.0.0.1 |
| `MYSQL_PORT` | 3306 |
| `MYSQL_USER` | root |
| `MYSQL_DATABASE` | stocks |

也可通过环境变量覆盖：`MYSQL_HOST`、`MYSQL_PASSWORD` 等。

### 1.3 初始化数据库

```bash
# 新建库 + 基础表（fund / fund_ranking / fund_nav / fund_holding / stock 等）
mysql -u root -p stocks < schema.sql

# 增量：基金筛选相关表（fund_scale、fund_manager、fund_metrics、v_fund_black_horse 等）
mysql -u root -p stocks < migrations/005_fund_screening.sql
```

> `schema.sql` 顶部示例库名为 `fund_db`，若你使用 `stocks` 库，请确保 `local_settings.py` 中 `MYSQL_DATABASE` 与建库名一致。

### 1.4 查看可用爬虫

```bash
scrapy list
```

输出应包含：

```
fund
fund_holding
fund_holding_current
fund_nav
fund_screening
stock_capital_flow
stock_quarterly_quote
```

---

## 二、配置说明

所有 `-s KEY=VALUE` 参数可在命令行临时覆盖；持久化请写入 `stocks/local_settings.py`。

### 全局

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `MYSQL_*` | 见上 | 数据库连接 |
| `ROBOTSTXT_OBEY` | False | 不遵守 robots.txt（API 直连） |

### 按爬虫分组

完整默认值见 `stocks/settings.py`；下文各爬虫章节列出与其相关的配置项。

**覆盖示例：**

```bash
scrapy crawl fund_nav -s FUND_NAV_MAX_FUNDS=10 -s FUND_NAV_SKIP_SYNCED=False
```

---

## 三、爬虫速查表

| 爬虫 | 常用命令 | 主要写入表 |
|------|----------|------------|
| `fund` | `scrapy crawl fund` | `fund`、`fund_ranking` |
| `fund_nav` | `scrapy crawl fund_nav` | `fund_nav` |
| `fund_holding` | `scrapy crawl fund_holding` | `fund_holding`、`fund_filter` |
| `fund_holding_current` | `scrapy crawl fund_holding_current` | `fund_holding` |
| `fund_screening` | `scrapy crawl fund_screening` | `fund_scale`、`fund_manager*`、`fund_operation`、`fund_metrics`、`fund_holding_stats` |
| `stock_capital_flow` | `scrapy crawl stock_capital_flow` | `stock`、`sector`、`stock_sector_rel`、`stock_capital_flow` |
| `stock_quarterly_quote` | `scrapy crawl stock_quarterly_quote` | `stock_capital_flow`（日 K） |

### 调试常用后缀

```bash
# 限制数量
scrapy crawl fund -s FUND_RANK_MAX_PAGES=2
scrapy crawl fund_nav -s FUND_NAV_MAX_FUNDS=5
scrapy crawl fund_holding -s FUND_HOLDING_MAX_FUNDS=3
scrapy crawl fund_holding_current -s FUND_HOLDING_CURRENT_MAX_FUNDS=3
scrapy crawl fund_screening -s FUND_SCREENING_MAX_FUNDS=5
scrapy crawl stock_capital_flow -s STOCK_CAPITAL_FLOW_MAX_PAGES=2 -s SECTOR_CONSTITUENT_MAX_SECTORS=5
scrapy crawl stock_quarterly_quote -s STOCK_QUOTE_MAX_STOCKS=10

# 强制重爬（关闭跳过）
scrapy crawl fund_nav -s FUND_NAV_FULL_BACKFILL=True -s FUND_NAV_SKIP_SYNCED=False
scrapy crawl fund_holding -s FUND_HOLDING_SKIP_SYNCED=False
scrapy crawl fund_holding_current -s FUND_HOLDING_SKIP_SYNCED=False
scrapy crawl fund_screening -s FUND_SCREENING_SKIP_SYNCED=False
scrapy crawl stock_quarterly_quote -s STOCK_QUOTE_SKIP_SYNCED=False
```

### 后台长时间运行

```bash
mkdir -p logs
scrapy crawl fund_screening >> logs/fund_screening.log 2>&1 &
tail -f logs/fund_screening.log
```

---

## 四、数据表与爬虫对照

| 表 | 爬虫 | 数据性质 |
|----|------|----------|
| `fund` | `fund` | 基金基本信息（静态） |
| `fund_ranking` | `fund` | 排行快照（近1周/1月/3月/1年/今年来等，**当日快照**） |
| `fund_nav` | `fund_nav` | 历史日净值（算任意区间涨跌幅） |
| `fund_holding` | `fund_holding`、`fund_holding_current` | 持仓明细（按季度） |
| `fund_filter` | `fund_holding` 写入 | 无持仓基金黑名单（`fund_holding_current` 读取跳过） |
| `fund.fund_category` | `fund_screening` | 基金类型（如混合型-偏股） |
| `fund_scale` | `fund_screening` | 规模变动（亿元） |
| `fund_manager` / `fund_manager_rel` | `fund_screening` | 基金经理及任职关系 |
| `fund_operation` | `fund_screening` | 换手率（半年报/年报） |
| `fund_metrics` | `fund_screening` 结束时计算 | 1y/2y 夏普、最大回撤、同类回撤百分位 |
| `fund_holding_stats` | `fund_screening` 结束时计算 | 前十大集中度、持仓数量 |
| `v_fund_black_horse` | —（视图） | 四步筛选后的黑马候选 |
| `stock` | `stock_capital_flow` | 股票基本信息 |
| `sector` | `stock_capital_flow` | 行业/概念板块 |
| `stock_sector_rel` | `stock_capital_flow` | 股票-板块关联 |
| `stock_capital_flow` | `stock_capital_flow`、`stock_quarterly_quote` | 日行情（资金流向快照 + 日 K OHLC） |

---

## 五、同步策略对比

各爬虫对「已同步数据」的处理方式不同，运行前建议确认预期行为。

### 5.1 fund_nav — 三档模式（skip / incremental / full）

对**每只基金**单独判断（见 `fund_nav._crawl_mode`）：

| 模式 | 条件 | 行为 |
|------|------|------|
| **skip** | 已有数据，且 `最早 ≤ FUND_NAV_START_DATE` 且 `最新 ≥ 最近交易日` | 不请求 |
| **incremental**（日增） | 已有数据，但最新净值落后于最近交易日 | **只抓第 1 页**（约 49 条），过滤掉已入库日期 |
| **full**（全量） | 库里无该基金；或最早日期晚于起始日 | **分页**一直翻到 `FUND_NAV_START_DATE` |

强制全量：`FUND_NAV_FULL_BACKFILL=True`（所有基金走 full）。

启动日志示例：

```
净值抓取 2016-01-01 起：待爬 1200 只，已跳过 18600 只（期望最新 2026-06-26）
```

### 5.2 stock_quarterly_quote — 两档（skip / 整只重爬）

**没有** `fund_nav` 式的「只补最新几条」日增模式。

| 情况 | 行为 |
|------|------|
| 无 `stock_capital_flow` 数据 | 爬（新浪约 4000 根日 K + 腾讯后复权） |
| 有数据但最新 < 最近交易日 | 爬（同样拉整段 K 线） |
| 有数据但最早 > `STOCK_QUOTE_START_DATE` | 爬（补历史缺口） |
| 最早 ≤ 起始日 **且** 最新 ≥ 最近交易日 | **跳过** |

未同步时总是拉约 4000 根 K 线；重复日期靠 `ON DUPLICATE KEY UPDATE` 覆盖，不按 `latest_known` 过滤。

### 5.3 fund_holding — 按基金跳过

| 条件 | 行为 |
|------|------|
| `fund_holding` 中已有该基金的**任意**持仓记录 | 跳过整只基金 |
| 无记录 | 爬全部历史季度（股票 + 债券） |
| 在 `fund_filter` 表中 | 跳过（判定为无持仓基金） |

### 5.4 fund_holding_current — 按「当前报告期」跳过

| 条件 | 行为 |
|------|------|
| 已有**当前最新季度**（如 2025-12-31）的持仓 | 跳过 |
| 否则 | 只抓最新一季（股票 + 债券） |

当前报告期由 `expected_latest_report_date()` 推算（已结束的最新季度末）。

### 5.5 fund_screening — 按本地数据跳过

| 条件 | 行为 |
|------|------|
| `fund_category` 已填 **且** `fund_scale`、`fund_manager_rel` 均有记录 | **跳过**整只基金 |
| 否则 | 爬取 4 类 API |

换手率 `fund_operation` 部分基金无披露，**不作为**跳过条件（无披露基金会每次重爬以尝试补全）。

全部跳过时不再重算 `fund_metrics` / `fund_holding_stats`（除非 `FUND_SCREENING_RECOMPUTE_METRICS=True` 且有待爬基金）。

强制全量：`scrapy crawl fund_screening -s FUND_SCREENING_SKIP_SYNCED=False`

### 5.6 stock_capital_flow / fund — 全量覆盖

每次运行抓取完整列表并 upsert，无「跳过已同步」逻辑。

---

## 六、更新频率建议

基金净值通常在 **每个交易日 19:00~23:00** 陆续公布（QDII 可能更晚）。建议工作日晚间跑批。

### 每日必跑（交易日）

| 优先级 | 爬虫 | 说明 |
|--------|------|------|
| ★★★ | `fund` | 同步基金列表 + 当日排行快照 |
| ★★★ | `fund_nav` | 补最新净值（默认日增，已最新则跳过） |
| ★★★ | `stock_quarterly_quote` | 补股票日 K（未同步则整只重爬） |

```bash
scrapy crawl fund
scrapy crawl fund_nav
scrapy crawl stock_quarterly_quote
```

### 每周 / 按需

| 爬虫 | 频率 | 说明 |
|------|------|------|
| `stock_capital_flow` | 每周 1 次 | 股票列表、板块、关联变化不频繁 |
| `fund_screening` | 每周 1 次 | 规模、经理、换手率；结束时重算指标 |
| `fund_holding_current` | 季报披露后 | 1/4/7/10 月更新最新季度持仓 |

```bash
scrapy crawl stock_capital_flow
scrapy crawl fund_screening
scrapy crawl fund_holding_current
```

### 一次性 / 低频回填

| 爬虫 | 时机 |
|------|------|
| `fund_nav` 全量 | 首次部署 |
| `fund_holding` | 首次部署 |
| `stock_quarterly_quote` 全量 | 首次部署（`-s STOCK_QUOTE_SKIP_SYNCED=False`） |
| `fund_screening` 全量 | 首次部署或每周 |

---

## 七、各爬虫详解

### 7.1 fund — 基金排行

**数据源：** 东方财富基金排行 API（`fund.eastmoney.com/data/rankhandler.aspx`）

**写入：** `fund`（基本信息 upsert）+ `fund_ranking`（排行快照 upsert，同 `(fund_code, nav_date)` 覆盖）

```bash
# 日常：抓取全部页
scrapy crawl fund

# 调试：只抓 2 页
scrapy crawl fund -s FUND_RANK_MAX_PAGES=2
```

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `FUND_RANK_SD` / `FUND_RANK_ED` | 2025-01-01 ~ 2026-03-31 | 自定义区间涨幅查询范围 |
| `FUND_RANK_SC` | sqjzf | 排序字段（区间涨幅） |
| `FUND_RANK_PN` | 50 | 每页条数 |
| `FUND_RANK_MAX_PAGES` | 0 | 0=全部页 |

**说明：**

- `fund_ranking` 保存**爬取当日**的滚动涨幅，适合横向排行，**不能替代** `fund_nav` 的历史净值序列。
- 若需「滚动涨幅的历史变化」，需另建历史快照表；当前设计为**最新快照**。

---

### 7.2 fund_nav — 基金历史净值

**数据源：** 东方财富 F10 `F10DataApi.aspx?type=lsjz`

**写入：** `fund_nav`

```bash
# 日常日增（推荐）
scrapy crawl fund_nav

# 首次全量回填（2016 年起，分页抓取）
scrapy crawl fund_nav -s FUND_NAV_FULL_BACKFILL=True -s FUND_NAV_SKIP_SYNCED=False

# 调试
scrapy crawl fund_nav -s FUND_NAV_MAX_FUNDS=5
```

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `FUND_NAV_START_DATE` | 2016-01-01 | 回填起始净值日 |
| `FUND_NAV_PAGE_SIZE` | 49 | 每页条数（接口上限约 49） |
| `FUND_NAV_SKIP_SYNCED` | True | 已同步至最近交易日则 skip |
| `FUND_NAV_FULL_BACKFILL` | False | True=全部基金强制全量分页 |
| `FUND_NAV_MAX_FUNDS` | 0 | 0=不限 |
| `FUND_NAV_CONCURRENT_REQUESTS` | 16 | 全局并发 |
| `FUND_NAV_CONCURRENT_REQUESTS_PER_DOMAIN` | 8 | 单域名并发 |
| `FUND_NAV_DOWNLOAD_DELAY` | 0 | 请求间隔（秒） |

**区间涨跌幅 SQL 示例：**

```sql
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

---

### 7.3 fund_holding — 全量历史持仓

**数据源：** 东方财富 F10 `FundArchivesDatas.aspx?type=jjcc`（股票）/ `type=bond`（债券）

**写入：** `fund_holding`；无持仓基金写入 `fund_filter`

```bash
# 首次 / 补漏
scrapy crawl fund_holding

# 强制重爬全部基金
scrapy crawl fund_holding -s FUND_HOLDING_SKIP_SYNCED=False

# 调试
scrapy crawl fund_holding -s FUND_HOLDING_MAX_FUNDS=5
```

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `FUND_HOLDING_SKIP_SYNCED` | True | 跳过已有任意持仓记录的基金 |
| `FUND_HOLDING_MAX_FUNDS` | 0 | 0=不限 |
| `FUND_HOLDING_TOPLINE` | 9999 | 每季度最多返回持仓条数 |
| `FUND_HOLDING_CONCURRENT_REQUESTS` | 16 | 全局并发 |
| `FUND_HOLDING_CONCURRENT_REQUESTS_PER_DOMAIN` | 8 | 单域名并发 |
| `FUND_HOLDING_DOWNLOAD_DELAY` | 0 | 请求间隔 |

**流程：** 先请求年份列表 → 按年/季度分页 → 单只基金全部请求完成后批量写入。

---

### 7.4 fund_holding_current — 当前季度持仓

**数据源：** 同 `fund_holding`

**写入：** `fund_holding`（仅最新一季）；**不写入** `fund_filter`

```bash
# 季报披露后（1/4/7/10 月）
scrapy crawl fund_holding_current

# 强制重爬
scrapy crawl fund_holding_current -s FUND_HOLDING_SKIP_SYNCED=False

# 调试
scrapy crawl fund_holding_current -s FUND_HOLDING_CURRENT_MAX_FUNDS=5
```

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `FUND_HOLDING_CURRENT_MAX_FUNDS` | 0 | 0=沿用 `FUND_HOLDING_MAX_FUNDS` |
| 其余 | — | 与 `fund_holding` 共用 `FUND_HOLDING_*` |

**与 `fund_holding` 区别：**

| | fund_holding | fund_holding_current |
|---|--------------|----------------------|
| 范围 | 全部历史季度 | 仅最新一季 |
| 跳过条件 | 任意历史持仓 | 当前报告期已有 |
| fund_filter | 会写入 | 只读跳过 |

---

### 7.5 fund_screening — 基金筛选（黑马）

**数据源（每只基金 4 个 API）：**

| API | 内容 |
|-----|------|
| `FundMNDetailInformation` | 基金类型、最新规模 |
| `FundArchivesDatas.aspx?type=gmbd` | 历史规模变动 |
| `FundMNMangerList` | 基金经理及任职 |
| `api.fund.eastmoney.com/f10/JJHSL/` | 换手率 |

**写入：**

- 爬取：`fund.fund_category`、`fund_scale`、`fund_manager`、`fund_manager_rel`、`fund_operation`
- 结束时计算：`fund_metrics`（1y/2y 夏普、最大回撤、同类回撤百分位）、`fund_holding_stats`（前十大集中度）

```bash
# 日常（跳过本地已有数据）
scrapy crawl fund_screening

# 强制全量重爬
scrapy crawl fund_screening -s FUND_SCREENING_SKIP_SYNCED=False

# 调试小样本
scrapy crawl fund_screening -s FUND_SCREENING_MAX_FUNDS=5
```

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `FUND_SCREENING_SKIP_SYNCED` | True | 跳过本地已有筛选数据的基金 |
| `FUND_SCREENING_RECOMPUTE_METRICS` | True | 有实际爬取时重算指标 |
| `FUND_SCREENING_MAX_FUNDS` | 0 | 0=不限 |
| `FUND_SCREENING_GMBD_PAGE_SIZE` | 20 | 规模分页 |
| `FUND_SCREENING_TURNOVER_PAGE_SIZE` | 20 | 换手率分页 |
| `FUND_SCREENING_CONCURRENT_REQUESTS` | 16 | 全局并发 |
| `FUND_SCREENING_CONCURRENT_REQUESTS_PER_DOMAIN` | 8 | 单域名并发 |
| `FUND_SCREENING_DOWNLOAD_DELAY` | 0 | 请求间隔 |

**推荐依赖顺序：**

```bash
scrapy crawl fund_nav      # 先有净值，才能算夏普/回撤
scrapy crawl fund_holding  # 先有持仓，才能算集中度
scrapy crawl fund_screening
```

**黑马视图：**

```sql
SELECT * FROM v_fund_black_horse LIMIT 20;
```

视图条件（四步筛选，需各表数据齐全）：

1. 规模 5~30 亿（`fund_scale`）
2. 现任经理任职 2~5 年（`fund_manager_rel`）
3. 1 年夏普 > 1，最大回撤处于同类前 30%（`fund_metrics`）
4. 前十大集中度 30~50%，换手率 100~300%（`fund_holding_stats` + `fund_operation`）

> **注意：** 仅在有**新爬取**基金时重算 `fund_metrics` 与 `fund_holding_stats`；全部跳过则不算。强制重算指标：`-s FUND_SCREENING_RECOMPUTE_METRICS=True -s FUND_SCREENING_SKIP_SYNCED=False`。

---

### 7.6 stock_capital_flow — 股票列表 / 板块 / 资金流向

**数据源：** 东方财富 push2 列表 API（默认 `push2delay.eastmoney.com`）

**写入：** `stock`、`sector`、`stock_sector_rel`、`stock_capital_flow`（当日行情 + 主力净流入等）

```bash
# 全量
scrapy crawl stock_capital_flow

# 调试
scrapy crawl stock_capital_flow -s STOCK_CAPITAL_FLOW_MAX_PAGES=2
scrapy crawl stock_capital_flow -s SECTOR_CONSTITUENT_MAX_SECTORS=10
```

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `STOCK_CAPITAL_FLOW_PAGE_SIZE` | 50 | 每页条数 |
| `STOCK_CAPITAL_FLOW_MAX_PAGES` | 0 | 0=全部页 |
| `SECTOR_CONSTITUENT_MAX_SECTORS` | 0 | 0=全部板块成分股 |
| `EASTMONEY_PUSH2_HOSTS` | push2delay 优先 | push2 主站易断连时可调整顺序 |

**抓取顺序：** 访问列表页取 Cookie → 行业板块 → 概念板块 → 全 A 股列表 → 各板块成分股。

**前置：** 本爬虫会填充 `stock` 表；`stock_quarterly_quote` 依赖 `stock` 表非空。

---

### 7.7 stock_quarterly_quote — 股票日 K

**数据源：** 新浪日 K（`money.finance.sina.com.cn`）+ 腾讯后复权（`web.ifzq.gtimg.cn`）

**写入：** `stock_capital_flow`（OHLC、涨跌幅、后复权因子等；与 `stock_capital_flow` 爬虫共用同一张表 upsert）

```bash
# 日常
scrapy crawl stock_quarterly_quote

# 首次全量 / 强制全部重爬
scrapy crawl stock_quarterly_quote -s STOCK_QUOTE_SKIP_SYNCED=False

# 调试
scrapy crawl stock_quarterly_quote -s STOCK_QUOTE_MAX_STOCKS=10
```

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `STOCK_QUOTE_START_DATE` | 2016-01-01 | 抓取起始日 |
| `STOCK_QUOTE_SKIP_SYNCED` | True | 已同步至最近交易日则跳过 |
| `STOCK_QUOTE_MAX_STOCKS` | 0 | 0=不限 |
| `STOCK_QUOTE_KLINE_DATALEN` | 4000 | 新浪单次 K 线条数 |
| `STOCK_QUOTE_HFQ_BAR_COUNT` | 2000 | 腾讯后复权条数 |
| `STOCK_QUOTE_BULK_BATCH_SIZE` | 500 | 批量写入条数 |
| `STOCK_QUOTE_CONCURRENT_REQUESTS` | 32 | 全局并发 |
| `STOCK_QUOTE_CONCURRENT_REQUESTS_PER_DOMAIN` | 16 | 单域名并发 |

**前置：**

```bash
scrapy crawl stock_capital_flow   # 先同步 stock 表
scrapy crawl stock_quarterly_quote
```

启动日志示例：

```
抓取 2016-01-01 起日 K：待爬 3200 只，已跳过 2100 只（已同步至 2026-06-26）
```

---

## 八、首次部署顺序

按表依赖关系依次执行：

```bash
cd /Users/rain/Documents/code/stocks

# 0. 建库建表
mysql -u root -p stocks < schema.sql
mysql -u root -p stocks < migrations/005_fund_screening.sql

# 1. 基金列表 + 排行快照
scrapy crawl fund

# 2. 基金历史净值（全量，耗时最长之一）
scrapy crawl fund_nav -s FUND_NAV_FULL_BACKFILL=True -s FUND_NAV_SKIP_SYNCED=False

# 3. 基金持仓（全量）
scrapy crawl fund_holding

# 4. 股票列表 + 板块
scrapy crawl stock_capital_flow

# 5. 股票日 K（全量）
scrapy crawl stock_quarterly_quote -s STOCK_QUOTE_SKIP_SYNCED=False

# 6. 基金筛选（规模/经理/换手率 + 指标计算）
scrapy crawl fund_screening
```

---

## 九、定时调度（crontab）

将 `/path/to/stocks` 替换为实际路径；建议 `mkdir -p logs`。

```cron
# 每个交易日 20:30 起（周一至周五）
30 20 * * 1-5 cd /path/to/stocks && scrapy crawl fund >> logs/fund.log 2>&1
35 20 * * 1-5 cd /path/to/stocks && scrapy crawl fund_nav >> logs/fund_nav.log 2>&1
40 20 * * 1-5 cd /path/to/stocks && scrapy crawl stock_quarterly_quote >> logs/stock_quote.log 2>&1

# 每周日 02:00 股票板块
0 2 * * 0 cd /path/to/stocks && scrapy crawl stock_capital_flow >> logs/stock_cf.log 2>&1

# 每周日 03:00 基金筛选
0 3 * * 0 cd /path/to/stocks && scrapy crawl fund_screening >> logs/fund_screening.log 2>&1

# 季报披露期（示例：4/8/10/11 月 5 日更新持仓）
0 4 5 4,8,10,11 * cd /path/to/stocks && scrapy crawl fund_holding_current >> logs/fund_holding.log 2>&1
```

---

## 十、进度验证 SQL

### fund_nav

```sql
SELECT COUNT(DISTINCT fund_code) AS funds,
       COUNT(*) AS rows,
       MIN(nav_date), MAX(nav_date)
FROM fund_nav;

-- 落后超过 3 个交易日的基金
SELECT f.fund_code, f.fund_name, n.latest
FROM fund f
LEFT JOIN (
  SELECT fund_code, MAX(nav_date) AS latest FROM fund_nav GROUP BY fund_code
) n ON f.fund_code = n.fund_code
WHERE n.latest IS NULL OR n.latest < CURDATE() - INTERVAL 3 DAY
LIMIT 20;
```

### fund_holding

```sql
SELECT COUNT(DISTINCT fund_code) AS funds,
       COUNT(*) AS rows,
       MIN(report_date), MAX(report_date)
FROM fund_holding;
```

### fund_screening

```sql
SELECT COUNT(DISTINCT fund_code) FROM fund_scale;
SELECT COUNT(DISTINCT fund_code) FROM fund_manager_rel;
SELECT COUNT(DISTINCT fund_code) FROM fund_operation;
SELECT COUNT(*) FROM fund_metrics;
SELECT COUNT(*) FROM v_fund_black_horse;
```

### stock_capital_flow（日 K）

```sql
SELECT COUNT(DISTINCT stock_code) AS stocks,
       COUNT(*) AS rows,
       MIN(trade_date), MAX(trade_date)
FROM stock_capital_flow;

-- 未同步至最近交易日的股票
SELECT s.stock_code, s.stock_name, q.latest
FROM stock s
LEFT JOIN (
  SELECT stock_code, MAX(trade_date) AS latest
  FROM stock_capital_flow GROUP BY stock_code
) q ON s.stock_code = q.stock_code
WHERE q.latest IS NULL OR q.latest < CURDATE() - INTERVAL 3 DAY
LIMIT 20;
```

---

## 十一、常见问题

**Q: fund_nav 日增量会抓多少数据？**  
A: 默认只抓第 1 页（约 49 条），过滤已入库日期后通常只剩 1~几条新净值。

**Q: stock_quarterly_quote 和 fund_nav 的「日增」一样吗？**  
A: 不一样。`fund_nav` 有 incremental 模式只补新页；`stock_quarterly_quote` 未同步时会整只重拉约 4000 根 K 线，靠 upsert 覆盖。

**Q: 新基金如何入库？**  
A: 先 `scrapy crawl fund` 写入 `fund` 表，再跑 `fund_nav`（自动 full 回填）、`fund_holding`、`fund_screening`。

**Q: fund_ranking 和 fund_nav 怎么选？**  
A: 当日排行、近 1 月 Top → `fund_ranking`；任意历史区间涨跌幅、净值曲线 → `fund_nav`。

**Q: fund_screening 跑 5 只为什么 metrics 很多？**  
A: `closed` 钩子对全库 `fund_nav` / `fund_holding` 重算，与 `FUND_SCREENING_MAX_FUNDS` 无关。

**Q: push2 API 经常断连？**  
A: 默认已优先 `push2delay.eastmoney.com`；仍失败可调低并发或调整 `EASTMONEY_PUSH2_HOSTS` 顺序。

**Q: 如何只看 Scrapy 日志级别？**  
A: `scrapy crawl fund_nav -s LOG_LEVEL=INFO`（或 `WARNING` 减少输出）。
