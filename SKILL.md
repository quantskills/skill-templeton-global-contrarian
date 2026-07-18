---
name: skill-templeton-global-contrarian
description: "当需要开发、计算、验证 John Templeton 逆向全球价值因子时，使用此 skill。适用于 A 股/港股/美股跨市场价值筛选，基于估值偏离度识别极端低估/高估机会，生成 buy/sell/hold 信号。"
quantSkills:
  organization: https://github.com/quantskills
  repository: quantskills/skill-templeton-global-contrarian
  repository_url: https://github.com/quantskills/skill-templeton-global-contrarian
  project_type: skill
  collection: alpha-factor
  license: GPL-3.0
  category: factor
  tags: [quant, alpha, stock, global, contrarian, value,逆向,价值因子,跨市场]
  platforms: [claude-code, codex, openclaw]
  language: zh-en
  status: stable
  validation_level: verified
  maintainer_type: community
  requires: []
  summary_zh: 邓普顿逆向全球价值因子，A股/港股/美股跨市场估值偏离度筛选
  summary_en: John Templeton contrarian global value factor for cross-market valuation deviation screening
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
# 市场情绪 Z-score（基于A股指数 PE/PB 历史偏离度）
market_z_score = (当前估值 - 历史均值) / 历史标准差

# 个股估值 Z-score（基于个股 PE 相对于行业均值的偏离度，过滤 pe <= 0）
stock_z_score = -(个股PE - 行业均值) / 行业标准差

# 综合逆向因子
factor_value = market_z_score × stock_z_score

# 信号方向
# market_z > 0（乐观市场）：factor 值低 = 便宜 → sell；factor 值高 = 贵 → buy（反向逻辑）
# market_z < 0（悲观市场）：factor 值高 = 便宜 → buy；factor 值低 = 贵 → sell（正向逻辑）
# score = abs(factor_value) 排名百分位
```

### 排序方向

`score` 越高 → 估值偏离程度越大 → 信号越强（buy/sell 均取极端值）

### 适用市场

- A 股全市场（CN）
- 港股全市场（HK）
- 美股全市场（US）
- 跨市场综合筛选

## 输入数据

| 字段 | 来源 | 说明 |
|------|------|------|
| trade_date | `get_stock_daily` / `get_hk_daily` / `get_us_daily` | 筛选基准日 |
| ts_code | 各市场日线接口 | 股票代码 |
| close | 各市场日线接口 | 收盘价 |
| pe | `get_stock_mktfin_indicator` | 动态市盈率（A股） |
| pe_ttm | 日线接口自带 | 动态市盈率（港/美） |
| pb | `get_index_indicator` | 市净率 |
| index_pe | `get_index_indicator` | A 股指数估值（市场情绪） |
| industry | `get_industry_constituents` | 行业分类 |

### 时点对齐（as_of_date）

- **行情数据**：取 as_of_date 当日或最近交易日
- **估值指标**：取 as_of_date 当日已披露的最新值
- **市场情绪**：取 as_of_date 当日指数估值

### PandaAI data 实现

详见 [references/data_guide.md](references/data_guide.md)

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
| rank | int | 截面排名（分位越高越极端） |
| signal | str | buy / hold / sell |
| confidence | float | 信号置信度 0-1 |
| data_version | str | 数据版本号 YYYYMMDD_HHMMSS |
| update_time | str | 生成时间 ISO 8601 |

### signal 生成规则

- `buy`：`score >= 80`（最被低估的 20%）
- `sell`：`score >= 60` 且 < 80（高估的 20%）
- `hold`：其余

### 附加输出字段

| 字段 | 说明 |
|------|------|
| pe | 市盈率 |
| pb | 市净率 |
| market_z_score | 市场情绪偏离度 |
| stock_z_score | 个股估值偏离度 |
| industry | 行业 |
| valuation_method | PE_TTM_calc / PE_indicator |

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
6. **验证脚本输出 PASS**：validate.py 所有检测项输出 PASS

## 目录结构

```
skill-templeton-global-contrarian/
├── SKILL.md
├── scripts/
│   ├── factor.py          # 因子计算主脚本
│   ├── validate.py         # 因子验证脚本
│   ├── backtest.py        # 因子回测脚本
│   ├── probe_interfaces.py # 接口探测脚本
│   └── mock_panda_server.py # Mock 测试服务器
├── references/
│   ├── data_guide.md       # 数据接口文档
│   └── source_boundary.md  # 外部数据边界说明
├── agents/
│   └── openai.yaml        # Agent 配置
├── 生产产物/
│   └── 数据库.parquet      # 最新因子输出
├── review_templeton_panda_sdk_factcheck_20260717.md  # 接口调研报告
├── README.md
├── README.en.md
└── LICENSE
```

## References

Use `references/source_boundary.md`.
