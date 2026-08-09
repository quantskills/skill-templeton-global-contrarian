#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Templeton 全球价值多因子 V2 回测脚本（官方SDK版）

分析指标：
  - IC / RankIC 时序
  - ICIR（信息系数比率）
  - 分层收益（5组，多空组合）
  - 最大回撤
  - 换手率

因子口径：与 factor.py/validate.py 统一为 V2（build_subfactors + combine_raw_score）。
  - A股：完整 7 子因子（含 12-1 月动量；窗口不足则动量权重降级为 0）
  - 港股：仅 EP（PB 可得则 EP+BP）
  - 美股：无 PE 权限，退化为 SCALE(-pct_from_low) 价格代理

窗口（长拉短评）：拉 end_date-550 天历史以保证每评估日能回看 252 交易日算动量，
  仅对 end_date-150 天之后的评估日计算 IC；触套餐限额（600003）则回退 150 天且动量权重置 0。
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import panda_data

from v2_operators import (
    build_subfactors,
    combine_raw_score,
    get_available_subfactors,
    op_scale,
)


def _load_env_file(env_path: str = None):
    if env_path is None:
        env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    # 环境变量优先，.env 不覆盖已注入的凭据
                    if key.strip() not in os.environ:
                        os.environ[key.strip()] = value.strip()


def _init_panda_token(
    username: str = None,
    password: str = None,
    interactive: bool = True,
):
    _load_env_file()

    if not username:
        username = os.environ.get("PANDA_USERNAME", "")
    if not password:
        password = os.environ.get("PANDA_PASSWORD", "")

    if interactive and not username:
        username = input("请输入 PandaAI 用户名（86手机号）: ").strip()
    if interactive and not password:
        password = input("请输入 PandaAI 密码: ").strip()

    if not username or not password:
        raise RuntimeError(
            "❌ 缺少认证信息。请通过以下方式之一提供：\n"
            "  1. 命令行参数: --username '86手机号' --password '密码'\n"
            "  2. 环境变量: export PANDA_USERNAME='86手机号' PANDA_PASSWORD='密码'\n"
            "  3. .env 文件\n"
            "  4. 运行时交互式输入\n"
        )

    panda_data.init_token(username, password)
    print("[API] ✅ 登录成功")


def _find_column(df: pd.DataFrame, candidates: list) -> str:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _get_ttm_quarters(as_of_date: str) -> list:
    dt = datetime.strptime(as_of_date, "%Y%m%d")
    year, month = dt.year, dt.month

    if month <= 3:
        return [f"{year}q1", f"{year-1}q4", f"{year-1}q3", f"{year-1}q2"]
    elif month <= 6:
        return [f"{year}q2", f"{year}q1", f"{year-1}q4", f"{year-1}q3"]
    elif month <= 9:
        return [f"{year}q3", f"{year}q2", f"{year}q1", f"{year-1}q4"]
    else:
        return [f"{year}q4", f"{year}q3", f"{year}q2", f"{year}q1"]


def get_financial_ttm(symbols: list, as_of_date: str) -> pd.DataFrame:
    quarters = _get_ttm_quarters(as_of_date)
    all_financial = []

    for q in quarters:
        try:
            df = panda_data.get_fina_reports(
                symbol=symbols,
                start_quarter=q,
                end_quarter=q,
                is_latest=True
            )
            if df is not None and not df.empty:
                df["report_quarter"] = q
                all_financial.append(df)
        except Exception:
            continue

    if not all_financial:
        return pd.DataFrame()

    financial_df = pd.concat(all_financial, ignore_index=True)

    sym_col = _find_column(financial_df, ["symbol", "ts_code", "stock_symbol", "code"])
    shares_col = _find_column(financial_df, ["bs_cap_stk", "total_shares", "总股本"])
    equity_col = _find_column(financial_df, ["bs_total_hldr_eqy_exc_min_int", "total_equity", "所有者权益合计"])
    profit_col = _find_column(financial_df, ["is_n_income_attr_p", "net_profit", "净利润"])
    revenue_col = _find_column(financial_df, ["is_total_revenue", "is_revenue", "revenue"])
    div_col = _find_column(financial_df, ["is_div_payt", "dividend"])
    liab_col = _find_column(financial_df, ["bs_total_liab", "total_liab"])
    assets_col = _find_column(financial_df, ["bs_total_assets", "total_assets"])

    if sym_col:
        financial_df = financial_df.rename(columns={sym_col: "ts_code"})
    if shares_col:
        financial_df = financial_df.rename(columns={shares_col: "total_shares"})
    if equity_col:
        financial_df = financial_df.rename(columns={equity_col: "total_equity"})
    if profit_col:
        financial_df = financial_df.rename(columns={profit_col: "net_profit"})
    if revenue_col:
        financial_df = financial_df.rename(columns={revenue_col: "revenue"})
    if div_col:
        financial_df = financial_df.rename(columns={div_col: "dividend"})
    if liab_col:
        financial_df = financial_df.rename(columns={liab_col: "total_liab"})
    if assets_col:
        financial_df = financial_df.rename(columns={assets_col: "total_assets"})

    for c in ["total_shares", "total_equity", "net_profit", "revenue",
              "dividend", "total_liab", "total_assets"]:
        if c not in financial_df.columns:
            financial_df[c] = np.nan
        financial_df[c] = pd.to_numeric(financial_df[c], errors="coerce")

    grouped = financial_df.groupby("ts_code").agg(
        total_shares=("total_shares", "last"),
        total_equity=("total_equity", "last"),
        total_liab=("total_liab", "last"),
        total_assets=("total_assets", "last"),
        net_profit_ttm=("net_profit", "sum"),
        revenue_ttm=("revenue", "sum"),
        div_ttm=("dividend", "sum"),
    ).reset_index()

    return grouped


def calculate_factor_value(daily_df: pd.DataFrame, financial_df: pd.DataFrame,
                           industry_df: pd.DataFrame, market: str,
                           has_momentum: bool = True,
                           eval_start: str = None) -> pd.DataFrame:
    """V2 全球价值多因子逐日截面计算（与 factor.py/validate.py 口径一致）。

    对每个交易日做截面 build_subfactors + combine_raw_score(可得子因子集合)，
    factor_value = SCALE(raw_score)，方向「大=买」。动量在整段历史上按 shift(252/21)
    预先算好（长拉短评），再仅保留 eval_start 之后的评估日以避免动量窗口不足。
      - cn：market_cap=close×total_shares → pe/pb/ps/div_yield/roe/leverage + 动量
      - hk：仅 pe（EP，PB 可得则 EP+BP）
      - us：pct_from_low → factor_value=SCALE(-pct_from_low)，不走子因子链
    """
    df = daily_df.copy()

    # 行业并入（供参考；V2 子因子为全截面 RANK，不做行业相对）
    if industry_df is not None and not industry_df.empty:
        industry_df = industry_df.copy()
        if "stock_symbol" in industry_df.columns:
            industry_df = industry_df.rename(columns={"stock_symbol": "ts_code"})
        if "industry" in industry_df.columns and "ts_code" in industry_df.columns:
            industry_df = industry_df.drop_duplicates(subset=["ts_code"], keep="first")
            df = df.merge(industry_df[["ts_code", "industry"]], on="ts_code", how="left")
    if "industry" not in df.columns:
        df["industry"] = "unknown"
    df["industry"] = df["industry"].fillna("unknown")

    if market == "cn":
        if financial_df is None or financial_df.empty:
            return pd.DataFrame()
        df = df.merge(financial_df, on="ts_code", how="inner")
        df["market_cap"] = df["close"] * df["total_shares"]
        df["pe"] = df["market_cap"] / df["net_profit_ttm"].replace(0, np.nan)
        df["pb"] = df["market_cap"] / df["total_equity"].replace(0, np.nan)
        df["ps"] = df["market_cap"] / df["revenue_ttm"].replace(0, np.nan)
        df["div_yield"] = df["div_ttm"] / df["market_cap"].replace(0, np.nan)
        df["roe"] = df["net_profit_ttm"] / df["total_equity"].replace(0, np.nan)
        df["leverage"] = df["total_liab"] / df["total_assets"].replace(0, np.nan)
        # 12-1 月动量：整段历史 shift（长拉），窗口不足则该 (股票,日) 为 NaN
        df = df.sort_values(["ts_code", "date"])
        g = df.groupby("ts_code")["close"]
        df["mom_12_1"] = (df["close"] / g.shift(252) - 1.0) - (df["close"] / g.shift(21) - 1.0)
        if not has_momentum:
            df["mom_12_1"] = np.nan
        available = get_available_subfactors("cn", has_pb=True, has_momentum=has_momentum)
    elif market == "hk":
        if "pe" not in df.columns:
            pe_col = _find_column(df, ["pe_ttm", "pe"])
            if pe_col:
                df["pe"] = df[pe_col]
            else:
                return pd.DataFrame()
        df["pe"] = pd.to_numeric(df["pe"], errors="coerce")
        df = df[df["pe"].notna() & (df["pe"] > 0)]
        has_pb = ("pb" in df.columns) and pd.to_numeric(df["pb"], errors="coerce").notna().any()
        available = get_available_subfactors("hk", has_pb=has_pb, has_momentum=False)
    else:  # us
        if "pct_from_low" not in df.columns:
            return pd.DataFrame()
        df["pct_from_low"] = pd.to_numeric(df["pct_from_low"], errors="coerce")
        df = df[df["pct_from_low"].notna()]
        available = []

    if df.empty:
        return pd.DataFrame()

    # 长拉短评：仅保留评估窗口内的交易日计算因子/IC
    if eval_start is not None:
        eval_dt = pd.to_datetime(eval_start, format="%Y%m%d")
        df = df[df["date"] >= eval_dt]
        if df.empty:
            return pd.DataFrame()

    out = []
    for _, day_df in df.groupby("date"):
        z = _cross_section_v2(day_df.copy(), market, available)
        if z is not None and not z.empty:
            out.append(z)
    if not out:
        return pd.DataFrame()
    return pd.concat(out, ignore_index=True)


def _cross_section_v2(df: pd.DataFrame, market: str, available: list) -> pd.DataFrame:
    """单个交易日截面：V2 子因子合成，factor_value = SCALE(raw_score)。"""
    df = df.reset_index(drop=True)
    if market == "us":
        # 截面 99% 分位 winsorize（与 factor.py 一致）：pct_from_low 极端离群会令 SCALE 出现异常 z 值
        pfl = pd.to_numeric(df["pct_from_low"], errors="coerce")
        upper = pfl.quantile(0.99)
        if upper is not None and np.isfinite(upper) and upper > 0:
            pfl = pfl.clip(upper=upper)
        df["raw_score"] = op_scale(-pfl)
    else:
        subs = build_subfactors(df)
        raw = combine_raw_score(subs, available)
        df["raw_score"] = raw.values
        if market == "hk":
            # 港股信号取反：与 factor.py 一致（港股口径 IC 为负，取反使方向正向）
            df["raw_score"] = -df["raw_score"]
    df["stock_z_score"] = df["raw_score"]
    df["factor_value"] = op_scale(df["raw_score"])
    df = df.dropna(subset=["factor_value"])
    return df


def load_forward_returns(daily_df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    df = daily_df.copy()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    df = df.sort_values(["ts_code", "date"])

    df[f"ret_{period}d"] = df.groupby("ts_code")["close"].pct_change(period).shift(-period)

    return df


def calculate_ic(factor_df: pd.DataFrame) -> tuple:
    factor_valid = factor_df.dropna(subset=["factor_value", "ret_20d"])

    if len(factor_valid) < 10:
        return np.nan, np.nan

    ic = factor_valid["factor_value"].corr(factor_valid["ret_20d"])
    rank_ic = factor_valid["factor_value"].rank().corr(factor_valid["ret_20d"].rank())

    return ic, rank_ic


def calculate_icir(ic_values: list) -> float:
    ic_array = np.array([i for i in ic_values if not np.isnan(i)])
    if len(ic_array) < 2:
        return np.nan
    return np.mean(ic_array) / np.std(ic_array)


def calculate_stratified_returns(factor_df: pd.DataFrame, n_groups: int = 5) -> pd.DataFrame:
    df = factor_df.copy()
    df["group"] = pd.qcut(df["factor_value"], n_groups, labels=False, duplicates="drop")

    grouped = df.groupby("group")["ret_20d"].agg(["mean", "std", "count"])
    grouped.index = [f"Group {i+1}" for i in grouped.index]

    if len(grouped) >= 2:
        grouped.loc["Long-Short"] = pd.Series({
            "mean": grouped.iloc[-1]["mean"] - grouped.iloc[0]["mean"],
            "std": np.sqrt(grouped.iloc[-1]["std"]**2 + grouped.iloc[0]["std"]**2),
            "count": min(grouped.iloc[-1]["count"], grouped.iloc[0]["count"]),
        })

    return grouped


def calculate_max_drawdown(returns: pd.Series) -> float:
    cumulative = (1 + returns.dropna()).cumprod()
    peak = cumulative.cummax()
    drawdown = (cumulative - peak) / peak
    return drawdown.min()


def calculate_turnover(factor_df: pd.DataFrame) -> float:
    df = factor_df.copy()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    df = df.sort_values(["ts_code", "date"])

    df["prev_factor"] = df.groupby("ts_code")["factor_value"].shift(1)
    df["factor_change"] = abs(df["factor_value"] - df["prev_factor"])

    avg_change = df["factor_change"].mean()
    return min(avg_change, 1.0)


def calculate_market_z(index_df: pd.DataFrame) -> float:
    if index_df is None or index_df.empty:
        return 0.0

    pe_col = _find_column(index_df, ["pe_ttm", "pe_lyr"])
    if pe_col:
        pe_values = pd.to_numeric(index_df[pe_col].dropna(), errors="coerce").dropna().values
        if len(pe_values) >= 2:
            pe_mean = np.mean(pe_values)
            pe_std = np.std(pe_values)
            current_pe = pe_values[-1]
            return (current_pe - pe_mean) / pe_std if pe_std > 0 else 0

    pb_col = _find_column(index_df, ["pb_ttm", "pb_lyr", "pb_lf"])
    if pb_col:
        pb_values = pd.to_numeric(index_df[pb_col].dropna(), errors="coerce").dropna().values
        if len(pb_values) >= 2:
            pb_mean = np.mean(pb_values)
            pb_std = np.std(pb_values)
            current_pb = pb_values[-1]
            return (current_pb - pb_mean) / pb_std if pb_std > 0 else 0

    return 0.0


def _prepare_and_compute(market: str, hist_start: str, end_date: str,
                         eval_start: str, has_momentum: bool,
                         period: int, market_z: float) -> pd.DataFrame:
    """按市场拉取行情/财务/估值，计算前瞻收益，调用 V2 因子逐日截面合成。
    quota 超限异常（600003）由调用方捕获并回退更短窗口。
    """
    industry_df = None
    symbols = None

    if market == "cn":
        industry_df = panda_data.get_industry_constituents(level="L1")
        symbol_col = _find_column(industry_df, ["stock_symbol", "symbol", "ts_code"])
        name_col = _find_column(industry_df, ["stock_name", "name", "sec_name"])
        ind_col = _find_column(industry_df, ["l1_name", "industry", "industry_name"])
        if symbol_col is None:
            raise RuntimeError("无法获取股票池")
        if ind_col:
            industry_df = industry_df.rename(columns={ind_col: "industry"})
        if symbol_col != "ts_code":
            industry_df = industry_df.rename(columns={symbol_col: "ts_code"})
        industry_df = industry_df.drop_duplicates(subset=["ts_code"], keep="first")
        # 与 factor.py/validate.py 一致：剔除金融行业与 ST/退市/风险警示
        excluded_names = {"银行", "保险", "证券", "多元金融", "信托"}
        if "industry" in industry_df.columns:
            industry_df = industry_df[~industry_df["industry"].astype(str).isin(excluded_names)]
        if name_col and name_col in industry_df.columns:
            industry_df = industry_df[~industry_df[name_col].astype(str).str.contains("ST|退市|风险警示", na=False)]
        symbols = industry_df["ts_code"].unique().tolist()[:200]
        daily_df = panda_data.get_stock_daily(symbol=symbols, start_date=hist_start, end_date=end_date, st=False)
    elif market == "hk":
        daily_df = panda_data.get_hk_daily(symbol=None, start_date=hist_start, end_date=end_date)
    else:
        daily_df = panda_data.get_us_daily(symbol=None, start_date=hist_start, end_date=end_date)

    if daily_df is None or daily_df.empty:
        raise RuntimeError("行情数据获取失败")

    close_col = _find_column(daily_df, ["close", "close_price"])
    sym_col = _find_column(daily_df, ["symbol", "ts_code", "stock_symbol"])
    date_col = _find_column(daily_df, ["date", "trade_date"])
    if close_col:
        daily_df = daily_df.rename(columns={close_col: "close"})
    if sym_col:
        daily_df = daily_df.rename(columns={sym_col: "ts_code"})
    if date_col:
        daily_df = daily_df.rename(columns={date_col: "date"})

    daily_df = load_forward_returns(daily_df, period)

    financial_df = pd.DataFrame()
    if market == "cn":
        financial_df = get_financial_ttm(symbols, end_date)
    elif market == "hk":
        # 港股：get_stock_mktfin_indicator 的 PE（每只股票取一条）并入日线
        hk_syms = daily_df["ts_code"].unique().tolist()
        val_df = panda_data.get_stock_mktfin_indicator(symbol=hk_syms[:500])
        if val_df is None or val_df.empty:
            raise RuntimeError("港股估值数据为空")
        ts_col = _find_column(val_df, ["symbol", "ts_code", "stock_symbol"])
        if ts_col:
            val_df = val_df.rename(columns={ts_col: "ts_code"})
        pe_col = _find_column(val_df, ["curr_pe_dil_excl_ttm", "curr_pe_basic_excl_ttm", "pe_ttm", "pe"])
        if pe_col is None:
            raise RuntimeError("港股估值数据无 PE 列")
        val_df["pe"] = pd.to_numeric(val_df[pe_col], errors="coerce")
        # PB 若可得则并入（EP+BP）
        pb_col = _find_column(val_df, ["curr_pb", "pb_ttm", "pb"])
        keep = ["ts_code", "pe"]
        if pb_col:
            val_df["pb"] = pd.to_numeric(val_df[pb_col], errors="coerce")
            keep.append("pb")
        val_df = val_df.dropna(subset=["pe"]).drop_duplicates(subset=["ts_code"], keep="first")
        daily_df = daily_df.merge(val_df[keep], on="ts_code", how="inner")
    else:
        # 美股：无 PE 权限，按每个交易日的滚动低点价格代理 pct_from_low 逐日计算
        daily_df = daily_df.sort_values(["ts_code", "date"])
        run_low = daily_df.groupby("ts_code")["close"].cummin()
        daily_df["pct_from_low"] = (daily_df["close"] - run_low) / run_low.replace(0, np.nan)
        daily_df = daily_df.dropna(subset=["pct_from_low"])

    return calculate_factor_value(
        daily_df, financial_df, industry_df, market,
        has_momentum=has_momentum, eval_start=eval_start
    )


def main():
    parser = argparse.ArgumentParser(description="Templeton 全球价值多因子 V2 回测（官方SDK版）")
    parser.add_argument("--period", type=int, default=20, help="持有期天数")
    parser.add_argument("--market", type=str, default="cn", choices=["cn", "hk", "us"])
    parser.add_argument("--end-date", type=str, default="20260717",
                        help="回测基准日（格式：YYYYMMDD），默认取最近可用交易日")
    parser.add_argument("--username", type=str, default=None)
    parser.add_argument("--password", type=str, default=None)
    parser.add_argument("--no-interactive", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("Templeton 全球价值多因子 V2 回测（官方SDK版）")
    print(f"  period: {args.period}d")
    print(f"  market: {args.market}")
    print("=" * 60)

    print("[backtest] 正在连接 PandaAI...")
    
    try:
        _init_panda_token(args.username, args.password, interactive=not args.no_interactive)
        print("[backtest] ✅ 已连接")
    except RuntimeError as e:
        print(f"[backtest] ❌ 连接失败: {e}")
        sys.exit(1)

    print("\n--- 回测指标 ---")

    try:
        end_date = args.end_date

        index_start = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=45)).strftime("%Y%m%d")
        index_df = panda_data.get_index_indicator(start_date=index_start, end_date=end_date)
        market_z = calculate_market_z(index_df)
        print(f"[backtest] 市场情绪 Z-score: {market_z:.2f}")

        # 长拉短评：先按 550 天历史 + 动量尝试；触套餐限额（600003）则回退 150 天且动量权重置 0
        attempts = [
            {"hist_days": 550, "eval_days": 150, "has_momentum": True},
            {"hist_days": 150, "eval_days": None, "has_momentum": False},
        ]
        factor_df = pd.DataFrame()
        used = None
        last_err = None
        for cfg in attempts:
            hist_start = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=cfg["hist_days"])).strftime("%Y%m%d")
            eval_start = None
            if cfg["eval_days"] is not None:
                eval_start = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=cfg["eval_days"])).strftime("%Y%m%d")
            print(f"[backtest] 取数窗口: hist_start={hist_start} eval_start={eval_start} "
                  f"动量={'开' if cfg['has_momentum'] else '关(降级)'}")
            try:
                factor_df = _prepare_and_compute(
                    args.market, hist_start, end_date, eval_start,
                    cfg["has_momentum"], args.period, market_z
                )
                used = cfg
                break
            except Exception as e:
                msg = str(e)
                last_err = e
                if "600003" in msg or "套餐" in msg or "quota" in msg.lower():
                    print(f"[backtest] ⚠️  触套餐限额，回退更短窗口并关闭动量: {msg}")
                    continue
                raise
        if factor_df is None or factor_df.empty:
            if last_err is not None:
                print(f"❌ 因子计算失败: {last_err}")
            else:
                print("❌ 因子计算结果为空，回测无法执行")
            sys.exit(1)
        print(f"[backtest] 采用窗口配置: {used}")

        print(f"[backtest] 因子值范围: [{factor_df['factor_value'].min():.2f}, {factor_df['factor_value'].max():.2f}]")
        print(f"[backtest] 因子值均值: {factor_df['factor_value'].mean():.2f}")
        print(f"[backtest] 因子值标准差: {factor_df['factor_value'].std():.2f}")

        ic, rank_ic = calculate_ic(factor_df)
        print(f"\nIC:        {ic:.4f}")
        print(f"RankIC:    {rank_ic:.4f}")

        ic_values = []
        date_groups = factor_df.groupby("date")
        for date, group in date_groups:
            group_ic, _ = calculate_ic(group)
            if not np.isnan(group_ic):
                ic_values.append(group_ic)

        if ic_values:
            icir = calculate_icir(ic_values)
            avg_ic = np.mean(ic_values)
            print(f"ICIR:      {icir:.4f}")
            print(f"平均 IC:   {avg_ic:.4f}")
            print(f"IC 标准差: {np.std(ic_values):.4f}")

        stratified = calculate_stratified_returns(factor_df)
        print("\n--- 分层收益 ---")
        print(stratified.round(4))

        max_dd = calculate_max_drawdown(factor_df["ret_20d"])
        print(f"\n最大回撤:  {max_dd:.2%}")

        turnover = calculate_turnover(factor_df)
        print(f"换手率:    {turnover:.2%}")

        print("\n✅ 回测完成")

    except Exception as e:
        print(f"❌ 回测失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
