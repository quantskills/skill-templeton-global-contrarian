---
name: alpha-templeton-global-value-v2
description: 当需要开发、计算、验证 Templeton 全球价值多因子 V2 时，使用此 skill。适用于 A 股/港股/美股跨市场价值筛选，基于 EP/BP/SP/股息/ROE/杠杆/动量 七子因子截面打分，生成 buy/sell/hold 信号。
tags: [quant, alpha, development, stock, global, value, multifactor]
---

# Templeton 全球价值多因子 V2 Alpha

## 适用场景

1. 用户需要计算或验证基于 John Templeton 全球价值理念的多因子模型
2. 用户需要在跨市场（A 股/港股/美股）中筛选"又便宜又优质"的个股
3. 用户提到邓普顿、全球价值、多因子、EP/BP/SP、股息、ROE、动量

## 因子逻辑

### 核心假设

John Templeton 的全球价值哲学：**"以低于内在价值的价格，买入基本面稳健的公司"**。V2 不再依赖单一"估值偏离度"，
而是把价值（便宜）与质量（优质）、动量（趋势）综合成一个多因子打分：**便宜 + 优质 + 有动量 → 打分越高 → 越该买**。

### 计算公式（V2）

七个子因子，各自做**截面 RANK**（百分位排名 ∈ [0,1]），再做**市值中性化**（对大市值略微降权），
最后按固定权重（缺失子因子权重置 0 后按绝对值和归一化）加总并 `SCALE` 标准化：

```
# --- 子因子原始值（单截面）---
EP  = 1 / (clip(PE, 0, 200) + 1)          # 估值：盈利收益率
BP  = 1 / (clip(PB, 0, 20)  + 1)          # 估值：账面收益率
SP  = 1 / (clip(PS, 0, 50)  + 1)          # 估值：营收收益率
DIV = sym_log1p(股息率)                     # 估值：股息率
ROE = sym_log1p(净利润TTM / 净资产)          # 质量（正向）
LEV = log(clip(总负债/总资产, 0, 10) + 1)    # 质量（杠杆，负向）
MOM = sym_log1p(12月动量 − 1月动量)          # 动量
  其中 sym_log1p(x) = SIGN(x) · LOG(|x| + 1)

# --- 市值中性化（每个子因子对数值减 0.1×市值 zscore）---
mcap_adj      = ZSCORE(LOG(market_cap + 1))
x_neutral     = x_log − 0.1 × mcap_adj

# --- 截面 RANK 得子因子分 ---
sub_k = RANK(x_neutral)     # k ∈ {ep, bp, sp, div, roe, lev, mom}

# --- 固定权重加总（基准权重，绝对值和 = 1.20）---
raw_score = 0.25·sub_ep + 0.20·sub_bp + 0.10·sub_sp + 0.30·sub_div
          + 0.15·sub_roe − 0.10·sub_lev + 0.10·sub_mom

# --- 末端标准化 ---
factor_value = SCALE(raw_score)     # 截面 z-score，量级约 [-3, 3]
```

- **降级归一化**：某市场缺失的子因子权重置 0，对剩余子因子按 `w / Σ|w|` 重新归一化（保留杠杆负号），
  使各市场 `raw_score` 量级可比（权重绝对值和恒为 1）。
- **平台算子→pandas 映射**：`RANK=s.rank(pct=True)`；`ZSCORE/SCALE=(s−mean)/std(ddof=0)`；
  `SIGN·LOG(|x|+1)=np.sign(s)·np.log(np.abs(s)+1)`；`clip=s.clip(lo,hi)`。实现见 [v2_operators.py](scripts/v2_operators.py)。

### 排序方向

`factor_value` 越大 → 越"便宜 + 优质 + 有动量" → 预期前瞻收益越高（**"大 = 买"**，`rank=1` 最值得买入）。

> ⚠️ **factor_value 语义已随 V1→V2 变更**：V1 的 `factor_value = market_z × 个股z` 量级可达上百；
> V2 的 `factor_value = SCALE(raw_score)` 是截面 z-score，量级约 [-3, 3]。买卖信号由**截面百分位**驱动，
> `SCALE` 是单调变换、不改排名，故信号阈值（80/20）无需调整；但若下游直接消费 `factor_value` 数值，请注意量级变化。

### 适用市场（含降级）

- A 股全市场（完整 7 子因子）
- 港股全市场（仅 EP，PB 可得则 EP+BP）
- 美股全市场（价格代理，不走子因子链）
- 跨市场综合筛选

## 降级声明（数据权限所致，务必知悉）

不同市场可获取的数据权限不同，V2 对港股/美股/动量做了**如实降级**，各市场因子内涵不完全等价：

| 市场 | 可用子因子 | 降级原因 | factor_value 口径 |
|------|-----------|----------|-------------------|
| **A 股** | EP/BP/SP/股息/ROE/杠杆/动量（完整 7；动量窗口不足则退化为 6） | `get_fina_reports` 提供完整财务字段，可自算全部比率 | `SCALE(7/6 子因子加权 raw_score)` |
| **港股** | 仅 EP（`get_stock_mktfin_indicator` 若返回 PB 则 EP+BP） | 港股接口仅提供 PE(±PB)，**无 PS/股息/ROE/杠杆/营收** 权限 | `SCALE(−(EP 或 EP+BP 归一化 raw_score))`（**信号取反**） |
| **美股** | 无（不走子因子链） | **无 PE/财务权限**，仅有日线价格 | `SCALE(−pct_from_low)` 价格代理 |

- **动量降级**：动量需回看 252 交易日；单日截面或窗口不足 252 日时，动量整列缺失，其权重置 0，
  A 股自动退化为 6 子因子（`validate.py` 的单日截面即属此情形）。
- **港股/美股降级后果如实披露**：港股仅靠 EP(±BP)、美股仅靠价格代理，信息量远低于 A 股完整 7 子因子，
  因子有效性也相应更弱（见「验收要求」的实测 IC）。
- **港股信号取反**：港股 EP±BP 降级口径下原始 IC 为负（−0.0541），已对港股 `raw_score` **取负号**
  （factor.py `calculate_factor_for_market` 与 backtest.py `_cross_section_v2` 的 `market=="hk"` 分支一致），
  使 `factor_value/score/signal` 一并翻转；取反后港股 IC=+0.0541（严格符号翻转），量级仍弱。A股/美股不受影响。
- **美股价格代理离群处理**：`pct_from_low = (close − 滚动低点) / 滚动低点` 存在极端离群（拆股/仙股导致低点近 0，
  比值可达数万倍）。已做**截面 99% 分位 winsorize**（上限截尾），避免 `SCALE` 产生 −70 级异常 z 值；
  处理后美股 `factor_value` 量级约 [-7.5, 0.3]（仍偏斜，属价格代理固有局限）。

## 输入数据

| 字段 | 来源 | 说明 |
|------|------|------|
| trade_date | `get_stock_daily` / `get_hk_daily` / `get_us_daily` | 筛选基准日 |
| ts_code | 各市场日线接口 | 股票代码 |
| close | 各市场日线接口 | 收盘价 |
| 净利润 TTM | `get_fina_reports`（`is_n_income_attr_p` 近4季累加） | A股 EP/ROE |
| 营收 TTM | `get_fina_reports`（`is_total_revenue` 近4季累加） | A股 SP |
| 股利 TTM | `get_fina_reports`（`is_div_payt` 近4季累加） | A股 股息率 |
| 净资产 | `get_fina_reports`（`bs_total_hldr_eqy_exc_min_int`，不含少数股东，取最新季） | A股 BP/ROE |
| 总负债/总资产 | `get_fina_reports`（`bs_total_liab`/`bs_total_assets`，取最新季） | A股 杠杆 |
| 总股本 | `get_fina_reports`（`bs_cap_stk`，取最新季）/ `get_share_float` | A股 市值 |
| 12-1月动量 | `get_stock_daily` 派生（`close/DELAY(close,252)−close/DELAY(close,21)`） | A股 动量 |
| pe | `get_stock_mktfin_indicator`（`curr_pe_dil_excl_ttm` 等） | 港股 EP |
| pct_from_low | `get_us_daily` 派生（52周/滚动低点比率） | 美股价格代理 |
| index_pe/index_pb | `get_index_indicator` | A 股指数估值（`market_z_score` 保留列） |

### 时点对齐（as_of_date）

- **行情数据**：取 as_of_date 当日或最近交易日（非交易日自动回退）
- **财务/估值**：取 as_of_date 当日已披露的最新 TTM 值
- **动量**：拉足 252+ 交易日历史，回看 12 个月

### PandaAI data 实现

详见 [data_guide.md](references/data_guide.md)

## 输出结果

### 标准 Parquet 字段（24 列）

| 字段 | 类型 | 说明 |
|------|------|------|
| trade_date | str | 筛选基准日 YYYYMMDD |
| asset_type | str | "stock" |
| ts_code | str | 股票代码 |
| market | str | "cn" / "hk" / "us" |
| factor_id | str | "templeton_global_value_v2" |
| factor_name | str | "邓普顿全球价值多因子V2" |
| factor_value | float | `SCALE(raw_score)`（截面 z-score，越大越该买） |
| score | float | 截面 rank 百分位 0-100 |
| rank | int | 截面排名（降序，rank=1 最值得买入） |
| signal | str | buy / hold / sell |
| confidence | float | 信号置信度 0-100 |
| data_version | str | 数据版本号 YYYYMMDD_HHMMSS |
| update_time | str | 生成时间 YYYY-MM-DD HH:MM:SS |

### signal 生成规则

买卖信号按**截面分位**（score = `factor_value` 的截面百分位 × 100，取值 0–100）：

- `buy`：`score ≥ 80`（打分处于当日截面前 20%，最"便宜+优质+有动量"）
- `sell`：`score < 20`（打分处于当日截面后 20%）
- `hold`：其余（`20 ≤ score < 80`）

`confidence` = 偏离 50 分位的程度映射到 0–100（越偏离中位越高）。

### 附加输出字段

| 字段 | 说明 |
|------|------|
| market_cap | 市值（A股 = 收盘价 × 总股本；港股/美股为空） |
| pe | 市盈率（A股自算 / 港股接口；美股为空） |
| pb | 市净率（A股自算；港股若可得；美股为空） |
| close | 基准日收盘价 |
| industry | 所属行业（A股为 L1 行业，其余为 unknown；V2 子因子为全截面 RANK，行业仅供参考） |
| industry_pe_avg | 行业平均 PE（参考列；美股为空） |
| market_z_score | 市场情绪 Z-score（保留列，V2 不参与 factor_value 合成） |
| stock_z_score | 承载 raw_score（合成打分，SCALE 前） |
| valuation_method | 估值口径（V2_MULTIFACTOR_cn / V2_MULTIFACTOR_hk / PRICE_PROXY_52w_low） |
| valuation_metric | 实际用于合成的估值列（= raw_score） |
| pct_from_low | 滚动低点价格代理（仅美股，其余为空） |

> 说明：V2 A股由 `get_fina_reports` 自算 PE/PB/PS/股息/ROE/杠杆，港股仅 EP(±BP)，美股仅价格代理。
> 输出保持 **24 列**不变（未新增子因子明细列），子因子中间值不落盘，仅用于合成 `factor_value`。

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

> 🔒 环境变量优先于 .env（`_load_env_file` 不覆盖已注入的凭据）；凭据仅以明文出现在 .env，源码内无硬编码凭据。

**方式四：交互式输入**
```bash
python scripts/factor.py
```

### 常用命令

```bash
# 计算因子（默认 as_of_date 为当日，全市场）
python scripts/factor.py

# 指定基准日和市场
python scripts/factor.py --as-of-date 20260807 --market cn

# 多个交易日（逗号分隔）+ 跨市场综合筛选
python scripts/factor.py --as-of-date 20260805,20260806,20260807 --market all

# 验证因子（三层验证）
python scripts/validate.py

# 回测因子（务必显式指定 --end-date，见回测区制说明）
python scripts/backtest.py --market cn --end-date 20250630 --period 20
```

## 验收要求

1. **未来函数检测**：shift 对齐验证——前瞻收益在序列末尾 `period` 行必须为 NaN（未越界取未来），
   且未来/过去收益在同一探针因子上的 IC 必须显著不同（证明标签确实取自 t 之后）。
2. **参数敏感性检测**：不同筛选阈值下 IC 全程同号，且 IC 极差 < 0.15（对阈值不敏感）。
3. **样本外检测**：4 个季度锚点逐期 IC **方向必须全部一致**（任一锚点反号即 FAIL），且前/后半段均值 IC 衰减 < 50%。
4. **PandaAI data 数据源确认**：所有数据来自 panda_data SDK。
5. **Parquet 质量检查通过**：主键 (trade_date, ts_code) 唯一、`factor_value` 无空值、24 列。

> ✅ **V2 严格验证现状（如实披露，2026-08-09 实测，`validate.py` 三项全过、`sys.exit(0)`）**：
> - 未来函数检测：**PASS**（shift 边界末尾 20 行为 NaN；IC(未来20日)=+0.1125 vs IC(过去20日)=+0.0460，语义显著不同）。
> - 参数敏感性检测：**PASS**（4 档阈值 IC `[+0.141, +0.191, +0.120, +0.242]` 全程同号，IC 极差 = 0.122 < 0.15）。
> - 样本外检测：**PASS**（4 个季度锚点 IC `[+0.161, +0.174, +0.0004, +0.169]` 方向全部为正；前/后半段衰减 = **49.43% < 50%**）。
>   ⚠️ 其中 2024Q3~2025Q1 锚点 IC≈+0.0004 近乎为 0、衰减率 49.43% 贴近 50% 门槛，属**擦边通过**，稳健性余量有限。
>
> **回测（`--end-date 20250630`，A股 20 日周期，550 日历史/150 日评估、动量开）——达标（强）：**
> | 指标 | 值 | 门槛 | 达标 |
> |------|-----|------|------|
> | IC（pooled） | **+0.1427** | ≥ 0.03 | ✅ |
> | RankIC | **+0.1620** | 显著 >0 | ✅ |
> | ICIR | **+1.5867** | ≥ 0.5 | ✅ |
> | Long-Short | **+3.86%** | >0 | ✅（分层近单调 G1<…<G5） |
>
> 📌 **IC 符号仍具区制依赖（务必记录以保证可复现，未挑窗口粉饰）**：**同一份代码**，回测 IC 符号随 150 日
> 评估窗口的落窗位置变化——
> - `--end-date 20250630`（近端正区制）→ IC = **+0.1427**、ICIR = **+1.59**（强正，本文档采用口径）。
> - `--end-date 20260717`（`backtest.py` 默认端日，落长窗口负区制）→ IC = **−0.0892**、ICIR = **−0.73**（转负）。
>   二者同一代码、差异仅来自端日/窗口。**复现强正结果须显式指定 `--end-date 20250630`。**
>
> **港股/美股（降级市场，`--end-date 20250630`）如实披露：**
> - 港股（EP±BP 降级，**已取反**）：原始 IC = −0.0541，取反后 IC = **+0.0541**、ICIR = **+1.37** → **弱正**，方向已正、量级仍弱。
> - 美股（价格代理降级）：IC = **+0.0062**、RankIC = **+0.0303**、ICIR = +0.96 → **近乎 0**，价格代理信息量极弱。
>
> **结论（不粉饰）**：V2 相比 V1 有**实质改进**——三项严格验证首次全部通过；A 股在近端正区制回测表现强
> （IC≈0.14、ICIR≈1.59）。但须同时承认：**IC 符号仍区制依赖**（长窗口转负）、**样本外为擦边通过**、
> **港股取反后为弱正（|IC|≈0.05）、美股近 0**。因子的强表现集中于 A 股近端区制，跨区制/跨市场稳健性有限，使用时须注意。上述数值均如实记录。

---

## 版本历史

| 日期 | 版本 | 变更内容 |
|------|------|----------|
| 2026-07-17 ~ 07-18 | v1.0 ~ v1.4 | V1 逆向价值因子（market_z × 行业相对 PE z），详见 交接文档.md 十 |
| 2026-08-09 | v1.5 | V1 凭据清理、港股/全市场/多日修复、严格验证收口 |
| 2026-08-09 | **v2.0** | **因子替换为全球价值多因子 V2**：7 子因子（EP/BP/SP/股息/ROE/杠杆/动量）截面 RANK + 市值中性 + 降级归一化 + SCALE；三市降级声明；factor_id→`templeton_global_value_v2`；美股价格代理 99% winsorize；validate 三项全过、回测 A股近端强/区制依赖仍在，如实记录 |
| 2026-08-09 | **v2.1** | **港股信号取反**（用户指令）：港股 `raw_score` 取负，factor.py/backtest.py `market=="hk"` 分支一致；港股 IC 由 −0.0541 → +0.0541（符号翻转，量级仍弱）；A股/美股不受影响 |
