# skill-templeton-global-contrarian 审查报告

**技能名称**: skill-templeton-global-contrarian（邓普顿逆向全球价值因子）  
**审查日期**: 2026-07-17  
**审查结论**: P0 阻断，零合规，不通过  
**优先级**: Phase 0（立即修复）

---

## 一、数据结果

| 指标 | 值 | 说明 |
|------|------|------|
| 行数 | 2793 | 无重复 ✓ |
| 市场 | 100% 港股 | A股/美股返回空 |
| factor_value | 全部 = 0.0 | 因子完全失效 |
| score | 全部 = 50.02 | 无区分度 |
| market_z_score | 全部 = 0.0 | 市场情绪数据失败 |
| signal | 2285 hold / 508 sell | 无任何 buy |

---

## 二、P0 阻断性问题

### 问题 1：SDK 接口不存在（get_stock_pv_indicator）

**位置**: [factor.py](file:///e:/因子skill集/skill-templeton-global-contrarian/开发产物/scripts/factor.py#L259) `get_stock_valuation()` 函数

**根因**: panda_data SDK v0.0.9 公开接口里没有 `get_stock_pv_indicator` 和 `pv_metric`。能用的只有：
- `get_stock_daily()` — 行情数据
- `get_fina_reports()` — 财务数据

**影响**: 无法获取 PE/PB/PS 估值字段，因子计算缺乏核心输入数据。

**证据**: probe_interfaces.py 存在但从未运行过，无实测结果。探测时使用港股代码 `0001.HK` 调用成功，但接口返回的是 `pv_market_cap`、`pv_beta_5y`、`pv_high_52w`、`pv_low_52w` 等 PV 指标，**并非 PE/PB/PS**。

### 问题 2：因子值恒等于 0

**位置**: [factor.py](file:///e:/因子skill集/skill-templeton-global-contrarian/开发产物/scripts/factor.py#L323) `calculate_factor()` 函数

**代码**:
```python
factor_value = -market_z_score * stock_z_score
```

**根因**:
- `market_z_score = 0`（指数数据失败，返回空 DataFrame）
- `factor_value = -0 × stock_z_score = 0`（所有行）
- `score = rank(pct=True) × 100 = 50.02`（所有行完全均匀）

**影响**: 整个因子毫无预测能力，score 没有任何区分度，无法筛选任何股票。

### 问题 3：个股 Z-score 计算无意义

**位置**: [factor.py](file:///e:/因子skill集/skill-templeton-global-contrarian/开发产物/scripts/factor.py#L320) `calculate_factor()` 函数

**代码**:
```python
stock_z_score = (个股52周低价比 − 全市场均值) / 全市场标准差
```

**根因**: Templeton 逆向逻辑要求"个股估值偏离行业均值"，但代码用的是：
- 个股自身 52 周最低价 / 当前价（这测的是"个股是否接近自己低点"）
- 再对全市场做截面标准化（而非行业内标准化）

**影响**: 逻辑和公式完全不匹配，因子衡量的不是"相对低估"而是"接近自身低点"。

### 问题 4：get_index_indicator 字段未确认

**位置**: [data_guide.md](file:///e:/因子skill集/skill-templeton-global-contrarian/开发产物/references/data_guide.md)

**根因**: data_guide.md 标注"需实际探测确认"，SKILL.md 里写的 `pe_ttm`/`pb_ttm`/`ps` 字段无法确认存在。

**实测结果**: `get_index_indicator` 返回字段为 `symbol`, `date`, `pb_lf`, `pb_lyr`, `pb_ttm`, `pe_lyr`, `pe_ttm`，**没有 ps 字段**。

**影响**: 查询结果集超过套餐限额（错误码 600003），无法获取市场情绪数据。

---

## 三、P1 严重问题

### 问题 5：validate.py 全假数据

**位置**: [validate.py](file:///e:/因子skill集/skill-templeton-global-contrarian/开发产物/scripts/validate.py)

**代码**:
```python
results = []
for thresh in thresholds:
    result = {"threshold": thresh, "score": np.random.uniform(0.3, 0.7)}
    results.append(result)

in_sample_ic = np.random.uniform(0.05, 0.15)
out_sample_ic = np.random.uniform(0.03, 0.10)
```

**影响**: 三层沙漏零真实检测，纯输出 ✅ PASS，验证脚本完全失效。

### 问题 6：backtest.py 用随机因子

**位置**: [backtest.py](file:///e:/因子skill集/skill-templeton-global-contrarian/开发产物/scripts/backtest.py)

**代码**:
```python
daily_df["factor_value"] = np.random.uniform(-2, 2, len(daily_df))
```

**影响**: IC/ICIR/分层收益全是随机数，没有任何意义。

### 问题 7：A股/美股数据获取失败无降级

**位置**: [factor.py](file:///e:/因子skill集/skill-templeton-global-contrarian/开发产物/scripts/factor.py#L437-L448) `main()` 函数

**根因**: `get_cn_stocks()` / `get_us_stocks()` 失败时只追加空 DataFrame，最终只有港股数据，`market` 参数完全失效。

**影响**: 用户指定 `--market cn` 或 `--market us` 时返回空结果，体验极差。

---

## 四、P2 一般问题

### 问题 8：SKILL.md 字段名与实际不匹配

**位置**: [SKILL.md](file:///e:/因子skill集/skill-templeton-global-contrarian/开发产物/SKILL.md)

**根因**: SKILL.md 写明输出字段包含 `pe_ttm`/`pb`/`ps`/`dividend_yield`，但实际代码完全没有拉这些字段。

### 问题 9：审计问题

**位置**: [probe_interfaces.py](file:///e:/因子skill集/skill-templeton-global-contrarian/开发产物/scripts/probe_interfaces.py)

**根因**: probe 脚本存在但从未执行，没有产出任何探测结果文件。

---

## 五、改进优先级

| 优先级 | 内容 | 预期产出 |
|--------|------|---------|
| **Phase 0** | 确认 `get_index_indicator` 和 `get_stock_pv_indicator` 是否存在 SDK，不存在则切换数据源 | 确认可用接口列表 |
| **Phase 1** | 重写 `calculate_factor()`，用 `get_fina_reports` 的财务字段（PE/PB）做行业标准化 Z-score | 因子值有区分度，score 范围 0-100 |
| **Phase 2** | 重写 `validate.py` 真实检测 | 三层沙漏真实验证 |
| **Phase 3** | 重写 `backtest.py` 真实 IC 计算 | 真实 IC/ICIR/分层收益 |
| **Phase 4** | 运行 `probe_interfaces.py` 补充实测数据 | 完整字段探测报告 |

---

## 六、SDK 接口实测汇总

| 接口 | 存在 | 参数要求 | 返回字段 | 备注 |
|------|------|---------|---------|------|
| `get_index_indicator` | ✅ | `start_date`, `end_date` | `symbol`, `date`, `pb_lf`, `pb_lyr`, `pb_ttm`, `pe_lyr`, `pe_ttm` | 查询超限（错误码 600003） |
| `get_hk_daily` | ✅ | `symbol=None`, `start_date`, `end_date` | `symbol`, `date`, `open`, `high`, `low`, `close`, `volume`, `amount` | 正常 |
| `get_us_daily` | ✅ | `symbol=None`, `start_date`, `end_date` | `symbol`, `date`, `open`, `high`, `low`, `close`, `volume` | 正常 |
| `get_stock_pv_indicator` | ⚠️ | `symbol`（仅港股代码） | `pv_market_cap`, `pv_beta_5y`, `pv_high_52w`, `pv_low_52w`, `pv_close` | 非估值字段，无 PE/PB |
| `get_stock_daily` | ✅ | `symbol`, `start_date`, `end_date`, `st` | `symbol`, `date`, `close`, `volume`, `amount` | A股专用 |
| `get_fina_reports` | ✅ | `symbol=None`, `start_quarter`, `end_quarter` | 财务报表全字段（无 PE/PB/PS） | 含 `bs_total_cur_assets`, `is_n_income_attr_p` 等 |
| `get_industry_constituents` | ✅ | `level='L1'` | `stock_symbol`, `stock_name`, `l1_name` | A股专用 |

---

## 七、结论

当前 skill-templeton-global-contrarian 处于**零合规状态**，核心问题：

1. **因子值恒为 0** — 市场情绪数据失败 + 逻辑错误
2. **接口依赖不存在** — `get_stock_pv_indicator` 非公开接口
3. **逻辑与公式不匹配** — 个股 Z-score 计算无意义
4. **验证/回测全假数据** — 零真实检测能力
5. **多市场支持失效** — A股/美股无法获取

**建议**: 立即进入 Phase 0，确认可用数据源后重新设计因子逻辑。

**审查人**: 自动审查  
**日期**: 2026-07-17  
**状态**: ❌ P0 阻断，不通过
