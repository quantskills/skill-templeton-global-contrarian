---
name: alpha-templeton-contrarian
description: 当需要开发、计算、验证 John Templeton 逆向全球价值因子时，使用此 skill。适用于 A 股/港股/美股跨市场价值筛选，基于估值偏离度识别极端低估/高估机会，生成 buy/sell/hold 信号。
tags: [quant, alpha, development, stock, global, contrarian]
---

# Templeton 逆向全球价值 Alpha

## 适用场景

1. 用户需要计算或验证基于 John Templeton 逆向投资理念的全球价值因子
2. 用户需要筛选跨市场（A 股/港股/美股）中估值极端偏离的个股
3. 用户提到邓普顿、逆向投资、全球价值、估值偏离、市场情绪

## 因子逻辑

### 核心假设

John Templeton 的逆向投资哲学：**"在极度悲观时买入，在极度乐观时卖出"**。市场情绪极端时，资产价格会严重偏离其内在价值，最终将回归均值。本因子衡量的是"估值相对于历史中枢的偏离程度"。

### 计算公式

```
# 单市场估值偏离度
market_z_score = (当前估值 - 历史均值) / 历史标准差

# 个股估值偏离度
stock_z_score = (个股P/E - 行业均值) / 行业标准差

# 综合逆向因子
factor_value = -market_z_score * stock_z_score  ← 越大越值得逆向买入
```

### 排序方向

`factor_value` 越大 → 市场极度悲观 + 个股极度低估 → 信号越强（升序排列，值大优先）

### 适用市场

- A 股全市场
- 港股全市场
- 美股全市场
- 跨市场综合筛选

## 输入数据

| 字段 | 来源 | 说明 |
|------|------|------|
| trade_date | `get_stock_daily` / `get_hk_daily` / `get_us_daily` | 筛选基准日 |
| ts_code | 各市场日线接口 | 股票代码 |
| close | 各市场日线接口 | 收盘价 |
| pe_ttm | `get_stock_pv_indicator` / `pv_metric` | 动态市盈率 |
| pb | `get_stock_pv_indicator` / `pv_metric` | 市净率 |
| ps | `get_stock_pv_indicator` / `pv_metric` | 市销率 |
| dividend_yield | `get_stock_pv_indicator` / `pv_metric` | 股息率 |
| index_pe | `get_index_indicator` | A 股指数估值（市场情绪） |
| index_pb | `get_index_indicator` | A 股指数市净率 |

### 时点对齐（as_of_date）

- **行情数据**：取 as_of_date 当日或最近交易日
- **估值指标**：取 as_of_date 当日已披露的最新值
- **市场情绪**：取 as_of_date 当日指数估值

### PandaAI data 实现

详见 [data_guide.md](references/data_guide.md)

## 输出结果

### 标准 Parquet 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| trade_date | str | 筛选基准日 YYYYMMDD |
| asset_type | str | "stock" |
| ts_code | str | 股票代码 |
| market | str | "cn" / "hk" / "us" |
| factor_id | str | "templeton_contrarian" |
| factor_name | str | "邓普顿逆向全球价值因子" |
| factor_value | float | 综合逆向因子值 |
| score | float | 截面 rank 百分位 0-100 |
| rank | int | 截面排名（升序，rank=1 最值得买入） |
| signal | str | buy / hold / sell |
| confidence | float | 信号置信度 0-1 |
| data_version | str | 数据版本号 YYYYMMDD_HHMMSS |
| update_time | str | 生成时间 ISO 8601 |

### signal 生成规则

- `buy`：市场 z_score < -1.5（极度悲观）且个股 z_score < -1.0（极度低估）且流动性达标
- `hold`：市场 z_score < 0 或个股 z_score < 0，但不满足 buy 全部条件
- `sell`：其余

### 附加输出字段

| 字段 | 说明 |
|------|------|
| pe_ttm | 动态市盈率 |
| pb | 市净率 |
| ps | 市销率 |
| dividend_yield | 股息率 |
| market_z_score | 市场情绪偏离度 |
| stock_z_score | 个股估值偏离度 |
| industry_pe_avg | 行业平均市盈率 |

## 使用方式

### 认证方式

使用此 skill 需要 PandaAI 账号权限，支持以下四种认证方式：

**方式一：命令行参数**
```bash
python scripts/factor.py --username '86手机号' --password '密码'
```

**方式二：环境变量**
```bash
export PANDA_USERNAME='86手机号'
export PANDA_PASSWORD='密码'
python scripts/factor.py
```

**方式三：.env 文件**
```
PANDA_USERNAME=86手机号
PANDA_PASSWORD=密码
PANDA_BASE_URL=http://pandadata.pandaaiquant.com
```

**方式四：交互式输入**
```bash
python scripts/factor.py
```

### 常用命令

```bash
# 计算因子（默认 as_of_date 为当日，全市场）
python scripts/factor.py

# 指定基准日和市场
python scripts/factor.py --as-of-date 20250630 --market cn

# 跨市场综合筛选
python scripts/factor.py --market all

# 验证因子
python scripts/validate.py

# 回测因子
python scripts/backtest.py --period 20
```

## 验收要求

1. **未来函数检测通过**：shift 对齐验证
2. **样本外检测通过**：IC 衰减不超过 50%
3. **回测指标达标**：|IC| > 0.03，|ICIR| > 0.5
4. **PandaAI data 数据源确认**：所有数据来自 panda_data SDK
5. **Parquet 质量检查通过**：主键唯一、字段完整
6. **验证脚本输出 PASS**：validate.py 所有检测项输出 ✅ PASS
