# Templeton Contrarian Global Value Factor

**简体中文** | English

> Quantitative implementation of John Templeton's contrarian investment philosophy: cross-market (A-share/HK/US) valuation deviation screening to identify under/over-valued opportunities during extreme market sentiment, generating buy/sell/hold signals.

![type](https://img.shields.io/badge/type-alpha--factor-blue)
![license](https://img.shields.io/badge/license-GPLv3-blue)

---

## What This Is

This skill implements John Templeton's core investment philosophy — *"buy when others are fearful, sell when others are greedy"* — via a dual-layer Z-score model:

1. **Market sentiment deviation** (market_z_score): how far current market valuation diverges from its historical mean
2. **Stock-level valuation deviation** (stock_z_score): how far a stock's P/E diverges from its industry mean (only stocks with pe > 0)
3. **Combined contrarian factor** (factor_value): market_z × stock_z; extreme values generate signals

## Quick Start

```bash
pip install panda_data pandas numpy scipy
export PANDA_USERNAME='your_phone_number'
export PANDA_PASSWORD='your_password'

python scripts/factor.py
python scripts/validate.py
python scripts/backtest.py --period 20
```

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE).
