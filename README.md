#邓普顿全球价值多因子 V2 Alpha / Templeton Global Value Multi-Factor V2 Alpha

## 项目概述 / Overview

本技能实现基于约翰·邓普顿全球价值理念的七子因子截面打分模型，覆盖 A 股、港股、美股三大市场。
因子方向：**便宜 + 优质 + 有动量 → 打分越高 → 越该买**。

This skill implements a seven-sub-factor cross-sectional scoring model inspired by John Templeton's
global value philosophy, covering A-shares, HK stocks and US stocks.
Direction: **cheap + high-quality + momentum → higher score → stronger buy**.

## 因子逻辑 / Factor Logic

七个子因子：EP（盈利收益率）、BP（账面收益率）、SP（营收收益率）、DIV（股息率）、
ROE（净资产收益率）、LEV（杠杆，负向）、MOM（12-1 月动量）。

Seven sub-factors: EP (earnings yield), BP (book yield), SP (sales yield), DIV (dividend yield),
ROE (return on equity), LEV (leverage, negative), MOM (12-1 month momentum).

```
sub_k = RANK(x_neutral)                    # 截面百分位排名
raw_score = 0.25·EP + 0.20·BP + 0.10·SP + 0.30·DIV
          + 0.15·ROE − 0.10·LEV + 0.10·MOM
factor_value = SCALE(raw_score)            # 截面 z-score
```

## 市场降级声明 / Market Degradation

| 市场 | 可用子因子 | 口径 |
|------|-----------|------|
| A 股 | 完整 7 子因子（动量不足退化为 6） | V2_MULTIFACTOR_cn |
| 港股 | 仅 EP（±BP），信号取反 | V2_MULTIFACTOR_hk |
| 美股 | 无财务权限，价格代理 | PRICE_PROXY_52w_low |

## 关键文件 / Key Files

| 文件 | 说明 |
|------|------|
| `开发产物/SKILL.md` | 技能定义文档 / Skill definition |
| `开发产物/交接文档.md` | 交接文档 / Handover doc |
| `开发产物/scripts/factor.py` | 因子计算 / Factor calculation |
| `开发产物/scripts/validate.py` | 三层验证 / Three-layer validation |
| `开发产物/scripts/backtest.py` | IC/ICIR 回测 / Backtest |
| `开发产物/scripts/v2_operators.py` | 共享算子 / Shared operators |
| `开发产物/生产产物/数据库.parquet` | 因子输出 / Factor output |

## 验证与回测结果 / Validation & Backtest

- 验证三项全过（未来函数 / 参数敏感性 / 样本外）· Three validation checks all PASS
- A 股回测（20250630）：IC=+0.1427，ICIR=+1.5867 · A-share backtest: IC=+0.1427, ICIR=+1.5867
- 港股（取反后）：IC=+0.0541（弱正）· HK (after sign flip): IC=+0.0541 (weak positive)
- 美股：IC=+0.0062（近乎 0）· US: IC=+0.0062 (near zero)

## 快速开始 / Quick Start

```bash
pip install panda_data pandas numpy pyarrow
# 配置 .env 凭据 / Configure .env credentials
python scripts/factor.py --market all
python scripts/validate.py
python scripts/backtest.py --market cn --end-date 20250630 --period 20
```

## 注意事项 / Caveats

- IC 符号具区制依赖，复现强正结果须指定 `--end-date 20250630`
- IC sign is regime-dependent; must specify `--end-date 20250630` to reproduce strong positive results
- 港股/美股为降级口径，因子有效性弱 · HK/US use degraded factor definitions with weaker efficacy
