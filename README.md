# Templeton 逆向全球价值因子

**简体中文** | [English](README.en.md)

> John Templeton 逆向投资理念的量化实现：A 股/港股/美股跨市场估值偏离度筛选，在市场情绪极端时识别低估/高估机会，生成 buy/sell/hold 信号。

![type](https://img.shields.io/badge/type-alpha--factor-blue)
![license](https://img.shields.io/badge/license-GPLv3-blue)

---

## 这是什么

本技能实现 John Templeton 的核心投资哲学——**"在别人恐惧时贪婪，在别人贪婪时恐惧"**。通过构建双层 Z-score 模型，量化衡量：

1. **市场情绪偏离度**（market_z_score）：当前市场估值偏离历史中枢的程度
2. **个股估值偏离度**（stock_z_score）：个股 PE 相对于行业均值的偏离程度
3. **综合逆向因子**（factor_value）：market_z × stock_z，取极端值产生信号

## 核心逻辑

```
market_z_score = (当前指数PE - 历史均值) / 历史标准差
stock_z_score  = -(个股PE - 行业均值) / 行业标准差   （仅 pe > 0 的股票纳入计算）
factor_value   = market_z_score × stock_z_score
score          = abs(factor_value) 排名百分位（0-100）
signal         = buy(score≥80) / sell(score≥60且<80) / hold(其余)
```

## 快速开始

### 前置依赖

```bash
pip install panda_data pandas numpy scipy
```

### 认证

```bash
# 方式1：环境变量
export PANDA_USERNAME='86手机号'
export PANDA_PASSWORD='密码'

# 方式2：.env 文件（同目录新建 .env）
PANDA_USERNAME=86手机号
PANDA_PASSWORD=密码
PANDA_BASE_URL=http://pandadata.pandaaiquant.com
```

### 运行

```bash
# 全市场计算（默认当日）
python scripts/factor.py

# 指定日期和市场
python scripts/factor.py --as-of-date 20250630 --market cn

# 因子验证
python scripts/validate.py

# 因子回测
python scripts/backtest.py --period 20
```

---

## 目录结构

```
skill-templeton-global-contrarian/
├── SKILL.md                        # 技能定义
├── scripts/
│   ├── factor.py                   # 因子计算主脚本
│   ├── validate.py                 # 三层沙漏验证
│   ├── backtest.py                 # IC/分层回测
│   ├── probe_interfaces.py         # 接口探测
│   └── mock_panda_server.py        # Mock 测试桩
├── references/
│   ├── data_guide.md               # 数据接口文档
│   └── source_boundary.md          # 外部数据边界
├── 生产产物/
│   └── 数据库.parquet               # 最新因子输出
├── review_templeton_panda_sdk_factcheck_20260717.md  # SDK 接口调研
├── agents/
│   └── openai.yaml
└── LICENSE
```

---

## 核心约束

| 约束 | 说明 |
| --- | --- |
| 只用 PandaAI 数据 | 所有数据来自 panda_data SDK |
| 只述不荐 | 输出研究结构与事实归纳，不构成任何投资建议 |
| 仅支持盈利股 | pe <= 0 的股票排除在 stock_z_score 计算之外 |

---

## 验证结果（2026-07-17）

| 指标 | 结果 |
| --- | --- |
| 数据量 | 611 只（CN 377 + HK 234） |
| 重复行 | 0 |
| buy 组 PE 中位数 | 7.86（低估值） |
| sell 组 PE 中位数 | 91.38（高估值） |
| market_z_score | +1.39（当前市场偏乐观） |

---

## ⚠️ 免责声明

本仓库仅作研究方法层面的整理，非官方、不隶属任何被研究对象，不验证任何收益声明，不构成任何投资建议。

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE).

## QUANTSKILLS 社群

<div align="center">
  <img src="https://raw.githubusercontent.com/quantskills/.github/main/profile/assets/pandaai-community-qr.jpg" alt="PandaAI 社群二维码" width="220">
  <br>
  <sub>扫码加入 PandaAI 社群，交流 QUANTSKILLS 技能、Agent 工作流与量化研究实践。</sub>
</div>
