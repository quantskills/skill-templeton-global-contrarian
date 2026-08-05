#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Templeton 逆向全球价值因子回测脚本（官方SDK版）

分析指标：
  - IC / RankIC 时序
  - ICIR（信息系数比率）
  - 分层收益（5组，多空组合）
  - 最大回撤
  - 换手率

修复：使用 get_fina_reports 获取财务数据计算 PE，不再依赖 get_stock_daily 返回 pe_ttm
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import panda_data


def _load_env_file(env_path: str = None):
    if env_path is None:
        env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
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
    equity_col = _find_column(financial_df, ["bs_total_hldr_eqy_inc_min_int", "total_equity", "所有者权益合计"])
    profit_col = _find_column(financial_df, ["is_n_income_attr_p", "net_profit", "净利润"])

    if sym_col:
        financial_df = financial_df.rename(columns={sym_col: "ts_code"})
    if shares_col:
        financial_df = financial_df.rename(columns={shares_col: "total_shares"})
    if equity_col:
        financial_df = financial_df.rename(columns={equity_col: "total_equity"})
    if profit_col:
        financial_df = financial_df.rename(columns={profit_col: "net_profit"})

    grouped = financial_df.groupby("ts_code").agg(
        total_shares=("total_shares", "last"),
        total_equity=("total_equity", "last"),
        net_profit_ttm=("net_profit", "sum"),
    ).reset_index()

    return grouped


def calculate_factor_value(daily_df: pd.DataFrame, financial_df: pd.DataFrame, 
                           industry_df: pd.DataFrame, market_z: float) -> pd.DataFrame:
    df = daily_df.copy()

    if not financial_df.empty:
        df = df.merge(financial_df, on="ts_code", how="inner")

        df["market_cap"] = df["close"] * df["total_shares"]
        df["pe"] = df["market_cap"] / df["net_profit_ttm"].replace(0, np.nan)

        valid_mask = df["pe"].notna() & (df["pe"] > 0) & (df["pe"] < 1000)
        df = df[valid_mask]
    else:
        pe_col = _find_column(df, ["pe_ttm", "pe"])
        if pe_col:
            df["pe"] = df[pe_col]
            valid_mask = df["pe"].notna() & (df["pe"] > 0) & (df["pe"] < 1000)
            df = df[valid_mask]
        else:
            return pd.DataFrame()

    if industry_df is not None and not industry_df.empty:
        industry_df = industry_df.copy()
        if "stock_symbol" in industry_df.columns:
            industry_df = industry_df.rename(columns={"stock_symbol": "ts_code"})
        if "industry" in industry_df.columns and "ts_code" in industry_df.columns:
            # 一股可能归属多个 L1 行业，去重保证 merge 为 1:1，避免日线行数重复
            industry_df = industry_df.drop_duplicates(subset=["ts_code"], keep="first")
            df = df.merge(industry_df, on="ts_code", how="left")
    if "industry" not in df.columns:
        df["industry"] = "unknown"
    else:
        df["industry"] = df["industry"].fillna("unknown")

    industry_stats = df.groupby("industry")["pe"].agg(["mean", "std"]).reset_index()
    industry_stats.columns = ["industry", "industry_pe_mean", "industry_pe_std"]

    df = df.merge(industry_stats, on="industry", how="left")
    df["stock_z_score"] = -(df["pe"] - df["industry_pe_mean"]) / df["industry_pe_std"].replace(0, np.nan)
    df["factor_value"] = market_z * df["stock_z_score"]

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


def main():
    parser = argparse.ArgumentParser(description="Templeton 逆向全球价值因子回测（官方SDK版）")
    parser.add_argument("--period", type=int, default=20, help="持有期天数")
    parser.add_argument("--market", type=str, default="cn", choices=["cn", "hk", "us"])
    parser.add_argument("--end-date", type=str, default="20260717",
                        help="回测基准日（格式：YYYYMMDD），默认取最近可用交易日")
    parser.add_argument("--username", type=str, default=None)
    parser.add_argument("--password", type=str, default=None)
    parser.add_argument("--no-interactive", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("Templeton 逆向全球价值因子回测（官方SDK版）")
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
        start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=500)).strftime("%Y%m%d")

        daily_df = None
        industry_df = None

        if args.market == "cn":
            industry_df = panda_data.get_industry_constituents(level="L1")
            symbol_col = _find_column(industry_df, ["stock_symbol", "symbol", "ts_code"])

            if symbol_col is None:
                print("❌ 无法获取股票池")
                return

            symbols = industry_df[symbol_col].unique().tolist()[:200]

            daily_df = panda_data.get_stock_daily(symbol=symbols, start_date=start_date, end_date=end_date, st=False)
        elif args.market == "hk":
            daily_df = panda_data.get_hk_daily(symbol=None, start_date=start_date, end_date=end_date)
        else:
            daily_df = panda_data.get_us_daily(symbol=None, start_date=start_date, end_date=end_date)

        if daily_df is None or daily_df.empty:
            print("❌ 行情数据获取失败")
            return

        close_col = _find_column(daily_df, ["close", "close_price"])
        sym_col = _find_column(daily_df, ["symbol", "ts_code", "stock_symbol"])
        date_col = _find_column(daily_df, ["date", "trade_date"])

        if close_col:
            daily_df = daily_df.rename(columns={close_col: "close"})
        if sym_col:
            daily_df = daily_df.rename(columns={sym_col: "ts_code"})
        if date_col:
            daily_df = daily_df.rename(columns={date_col: "date"})

        daily_df = load_forward_returns(daily_df, args.period)

        index_start = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=60)).strftime("%Y%m%d")
        index_df = panda_data.get_index_indicator(start_date=index_start, end_date=end_date)
        market_z = calculate_market_z(index_df)
        print(f"[backtest] 市场情绪 Z-score: {market_z:.2f}")

        if args.market == "cn":
            financial_df = get_financial_ttm(symbols, end_date)
        else:
            financial_df = pd.DataFrame()

        if args.market == "cn" and "industry" not in industry_df.columns:
            ind_col = _find_column(industry_df, ["l1_name", "industry", "industry_name"])
            if ind_col:
                industry_df = industry_df.rename(columns={ind_col: "industry"})

        factor_df = calculate_factor_value(daily_df, financial_df, industry_df, market_z)

        if factor_df.empty:
            print("❌ 因子计算结果为空")
            return

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


if __name__ == "__main__":
    main()
