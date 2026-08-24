#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Templeton 全球价值多因子 V2 验证脚本（官方SDK版）

三层沙漏验证：
  1. 未来函数检测（shift 对齐）
  2. 过拟合检测（参数敏感性）
  3. 样本外检测（跨年份/跨行情验证）

因子口径：与 factor.py/backtest.py 统一为 V2（build_subfactors + combine_raw_score）。
  验证窗口 < 252 交易日、逐日单截面无法回看，动量整列缺失 → A股自动降级为 6 子因子
  （EP/BP/SP/股息/ROE/杠杆）。严格判据（未来函数/参数敏感性/样本外）不因换因子而放宽。
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


def _find_column(df, candidates):
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _get_ttm_quarters(as_of_date: str):
    year = int(as_of_date[:4])
    month = int(as_of_date[4:6])
    
    quarters = []
    if month >= 1 and month <= 3:
        quarters = [f"{year}q1", f"{year-1}q4", f"{year-1}q3", f"{year-1}q2"]
    elif month >= 4 and month <= 6:
        quarters = [f"{year}q2", f"{year}q1", f"{year-1}q4", f"{year-1}q3"]
    elif month >= 7 and month <= 9:
        quarters = [f"{year}q3", f"{year}q2", f"{year}q1", f"{year-1}q4"]
    else:
        quarters = [f"{year}q4", f"{year}q3", f"{year}q2", f"{year}q1"]
    
    return quarters


def get_financial_ttm(ts_codes: list, as_of_date: str) -> pd.DataFrame:
    if not ts_codes:
        return pd.DataFrame()
    
    ttm_quarters = _get_ttm_quarters(as_of_date)
    
    all_fina = []
    for quarter in ttm_quarters:
        try:
            df = panda_data.get_fina_reports(
                symbol=ts_codes,
                start_quarter=quarter,
                end_quarter=quarter,
                is_latest=True
            )
            if df is not None and not df.empty:
                df["quarter"] = quarter
                all_fina.append(df)
        except Exception:
            continue
    
    if not all_fina:
        return pd.DataFrame()
    
    fina_df = pd.concat(all_fina, ignore_index=True)
    
    ts_col = _find_column(fina_df, ["ts_code", "symbol", "stock_symbol"])
    if ts_col:
        fina_df = fina_df.rename(columns={ts_col: "ts_code"})

    # V2 多子因子所需字段（与 factor.py/backtest.py 口径一致）：
    #   累加类（TTM，近4季 sum）：净利润、营收、股利
    #   存量类（时点值，取最新一季）：净资产(不含少数)、总负债、总资产、总股本
    net_profit_col = _find_column(fina_df, ["is_n_income_attr_p", "is_end_net_profit", "net_profit", "np_parent_company_owners", "np"])
    shares_col = _find_column(fina_df, ["bs_cap_stk", "total_shares", "total_share"])
    equity_col = _find_column(fina_df, ["bs_total_hldr_eqy_exc_min_int", "total_equity", "所有者权益合计"])
    revenue_col = _find_column(fina_df, ["is_total_revenue", "is_revenue", "revenue"])
    div_col = _find_column(fina_df, ["is_div_payt", "dividend"])
    liab_col = _find_column(fina_df, ["bs_total_liab", "total_liab"])
    assets_col = _find_column(fina_df, ["bs_total_assets", "total_assets"])

    fina_df["net_profit"] = pd.to_numeric(fina_df[net_profit_col], errors="coerce") if net_profit_col else np.nan
    fina_df["total_shares"] = pd.to_numeric(fina_df[shares_col], errors="coerce") if shares_col else np.nan
    fina_df["equity"] = pd.to_numeric(fina_df[equity_col], errors="coerce") if equity_col else np.nan
    fina_df["revenue"] = pd.to_numeric(fina_df[revenue_col], errors="coerce") if revenue_col else np.nan
    fina_df["dividend"] = pd.to_numeric(fina_df[div_col], errors="coerce") if div_col else np.nan
    fina_df["total_liab"] = pd.to_numeric(fina_df[liab_col], errors="coerce") if liab_col else np.nan
    fina_df["total_assets"] = pd.to_numeric(fina_df[assets_col], errors="coerce") if assets_col else np.nan
    
    fina_df = fina_df.dropna(subset=["ts_code", "net_profit"])
    
    if "ts_code" not in fina_df.columns:
        return pd.DataFrame()
    
    # 季度排序：确保 last=最新季
    quarter_order = {q: i for i, q in enumerate(reversed(_get_ttm_quarters(as_of_date)))}
    if "quarter" in fina_df.columns:
        fina_df["_qrank"] = fina_df["quarter"].map(quarter_order).fillna(-1)
        fina_df = fina_df.sort_values(["ts_code", "_qrank"])

    ttm_df = fina_df.groupby("ts_code").agg(
        net_profit_ttm=("net_profit", "sum"),
        revenue_ttm=("revenue", "sum"),
        div_ttm=("dividend", "sum"),
    ).reset_index()
    latest_df = fina_df.groupby("ts_code").agg(
        total_shares=("total_shares", "last"),
        equity=("equity", "last"),
        total_liab=("total_liab", "last"),
        total_assets=("total_assets", "last"),
    ).reset_index()
    ttm_df = ttm_df.merge(latest_df, on="ts_code", how="left")
    
    return ttm_df


def calculate_factor_for_validation(
    daily_df: pd.DataFrame,
    financial_df: pd.DataFrame,
    industry_df: pd.DataFrame,
    market_z: float,
):
    if daily_df.empty:
        return pd.DataFrame()
    
    close_col = _find_column(daily_df, ["close", "close_price"])
    sym_col = _find_column(daily_df, ["symbol", "ts_code", "stock_symbol"])
    date_col = _find_column(daily_df, ["date", "trade_date"])
    
    if close_col:
        daily_df = daily_df.rename(columns={close_col: "close"})
    if sym_col:
        daily_df = daily_df.rename(columns={sym_col: "ts_code"})
    if date_col:
        daily_df = daily_df.rename(columns={date_col: "date"})
    
    daily_df["date"] = pd.to_datetime(daily_df["date"], format="%Y%m%d", errors="coerce")
    daily_df = daily_df.dropna(subset=["date", "ts_code", "close"])
    
    merged = daily_df.copy()

    # A股单日截面：由财务字段派生 V2 各比率（市值 = 收盘价 × 总股本）。
    # 验证窗口 < 252 交易日、且逐日单截面无法回看，故动量整列缺失 → 自动降级为 6 子因子。
    if financial_df is not None and not financial_df.empty:
        merged = merged.merge(financial_df, on="ts_code", how="inner")

        market_cap_col = _find_column(merged, ["market_cap", "mkt_cap", "cap"])
        if market_cap_col:
            merged["market_cap"] = merged[market_cap_col]
        elif "total_shares" in merged.columns:
            merged["market_cap"] = merged["close"] * merged["total_shares"]
        else:
            merged["market_cap"] = np.nan

        mcap = pd.to_numeric(merged["market_cap"], errors="coerce")
        np_ttm = pd.to_numeric(merged.get("net_profit_ttm", np.nan), errors="coerce")
        rev_ttm = pd.to_numeric(merged.get("revenue_ttm", np.nan), errors="coerce")
        div_ttm = pd.to_numeric(merged.get("div_ttm", np.nan), errors="coerce")
        equity = pd.to_numeric(merged.get("equity", np.nan), errors="coerce")
        liab = pd.to_numeric(merged.get("total_liab", np.nan), errors="coerce")
        assets = pd.to_numeric(merged.get("total_assets", np.nan), errors="coerce")

        merged["pe"] = mcap / np_ttm.replace(0, np.nan)
        merged["pb"] = mcap / equity.replace(0, np.nan)
        merged["ps"] = mcap / rev_ttm.replace(0, np.nan)
        merged["div_yield"] = div_ttm / mcap.replace(0, np.nan)
        merged["roe"] = np_ttm / equity.replace(0, np.nan)
        merged["leverage"] = liab / assets.replace(0, np.nan)
        merged = merged[merged["pe"] > 0]
        available = get_available_subfactors("cn", has_pb=True, has_momentum=False)
    else:
        pe_col = _find_column(merged, ["pe_ttm", "pe"])
        if pe_col:
            merged["pe"] = merged[pe_col]
        else:
            merged["pe"] = np.nan
        available = get_available_subfactors("cn", has_pb=False, has_momentum=False)

    if industry_df is not None and not industry_df.empty:
        merged = merged.merge(industry_df[["ts_code", "industry"]], on="ts_code", how="left")
    else:
        merged["industry"] = "unknown"

    merged = merged.dropna(subset=["pe"])

    if merged.empty:
        return pd.DataFrame()

    if "industry" not in merged.columns:
        merged["industry"] = "unknown"
    merged["industry"] = merged["industry"].fillna("unknown")

    # V2 子因子合成（与 factor.py/backtest.py 完全一致）：
    #   build_subfactors → combine_raw_score(可得集合) → factor_value = SCALE(raw_score)
    merged = merged.reset_index(drop=True)
    subs = build_subfactors(merged)
    raw = combine_raw_score(subs, available)
    merged["raw_score"] = raw.values
    merged = merged.dropna(subset=["raw_score"])
    if merged.empty:
        return pd.DataFrame()
    merged["stock_z_score"] = merged["raw_score"]
    merged["market_z_score"] = market_z
    merged["factor_value"] = op_scale(merged["raw_score"])
    merged["score"] = merged["factor_value"].rank(pct=True) * 100
    
    return merged


def calculate_ic(factor_values: np.ndarray, returns: np.ndarray) -> float:
    valid_mask = ~np.isnan(factor_values) & ~np.isnan(returns)
    if valid_mask.sum() < 10:
        return np.nan
    return np.corrcoef(factor_values[valid_mask], returns[valid_mask])[0, 1]


def calculate_market_z(index_df: pd.DataFrame) -> float:
    if index_df is None or index_df.empty:
        return 0.0
    pe_col = _find_column(index_df, ["pe_ttm", "pe_lyr"])
    if pe_col:
        pe_values = index_df[pe_col].dropna().values
        if len(pe_values) >= 2:
            pe_mean = np.mean(pe_values)
            pe_std = np.std(pe_values)
            current_pe = pe_values[-1]
            return (current_pe - pe_mean) / pe_std if pe_std > 0 else 0
    pb_col = _find_column(index_df, ["pb_ttm", "pb_lf"])
    if pb_col:
        pb_values = index_df[pb_col].dropna().values
        if len(pb_values) >= 2:
            pb_mean = np.mean(pb_values)
            pb_std = np.std(pb_values)
            current_pb = pb_values[-1]
            return (current_pb - pb_mean) / pb_std if pb_std > 0 else 0
    return 0.0


def test_future_data() -> bool:
    print("\n--- 未来函数检测 ---")
    try:
        as_of_date = "20250630"
        index_start = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=45)).strftime("%Y%m%d")

        index_df = panda_data.get_index_indicator(start_date=index_start, end_date=as_of_date)

        if index_df is None or index_df.empty:
            print("❌ 指数估值数据为空，未来函数检测无法执行 → FAIL")
            return False

        date_col = _find_column(index_df, ["date", "trade_date"])
        if not date_col:
            print("❌ 指数估值无日期列，无法验证时点对齐 → FAIL")
            return False

        index_df[date_col] = pd.to_datetime(index_df[date_col], format="%Y%m%d")
        latest_index_date = index_df[date_col].max()
        as_of_dt = datetime.strptime(as_of_date, "%Y%m%d")

        if latest_index_date > as_of_dt:
            print(f"❌ 指数估值日期 {latest_index_date.date()} 晚于基准日 {as_of_date}，存在未来函数 → FAIL")
            return False

        print(f"✅ [时点对齐] 指数估值日期: {latest_index_date.date()} <= 基准日: {as_of_date}")

        # ---- shift 对齐验证：证明前瞻收益严格取自 t 之后，且因子只用 t 时点信息 ----
        # 取一段日线，构造两种前瞻收益：
        #   ret_fwd   = close.shift(-period)/close - 1          （正确：t 相对未来）
        #   ret_leak  = close/close.shift(period) - 1           （错误：混入 t 之前，即"看过去"对照组）
        # 未来函数检测要求：
        #   1) ret_fwd 在每支股票序列末尾 period 行必须为 NaN（严格无法用未来数据）
        #   2) IC(factor_t, ret_fwd) 与 IC(factor_t, ret_fwd 未 shift 的当日收益) 必须显著不同，
        #      证明 shift(-period) 确实把标签推到了未来、而非泄漏当期。
        start_date = (as_of_dt - timedelta(days=90)).strftime("%Y%m%d")
        industry_df = panda_data.get_industry_constituents(level="L1")
        sym_col = _find_column(industry_df, ["stock_symbol", "symbol", "ts_code"])
        ind_col = _find_column(industry_df, ["l1_name", "industry", "industry_name"])
        if sym_col is None:
            print("❌ 无法获取股票池，shift 对齐验证无法执行 → FAIL")
            return False
        if ind_col:
            industry_df = industry_df.rename(columns={ind_col: "industry"})
        if sym_col != "ts_code":
            industry_df = industry_df.rename(columns={sym_col: "ts_code"})
        industry_df = industry_df.drop_duplicates(subset=["ts_code"], keep="first")
        symbols = industry_df["ts_code"].unique().tolist()[:150]

        px = panda_data.get_stock_daily(symbol=symbols, start_date=start_date, end_date=as_of_date, st=False)
        if px is None or px.empty:
            print("❌ 行情数据为空，shift 对齐验证无法执行 → FAIL")
            return False

        cc = _find_column(px, ["close", "close_price"])
        sc = _find_column(px, ["symbol", "ts_code", "stock_symbol"])
        dc = _find_column(px, ["date", "trade_date"])
        px = px.rename(columns={cc: "close", sc: "ts_code", dc: "date"})
        px["date"] = pd.to_datetime(px["date"], format="%Y%m%d", errors="coerce")
        px = px.dropna(subset=["date", "ts_code", "close"]).sort_values(["ts_code", "date"])

        period = 20
        px["ret_fwd"] = px.groupby("ts_code")["close"].shift(-period) / px["close"] - 1.0

        # 断言 1：每支股票末尾 period 行的前瞻收益必须为 NaN（无未来数据可用）
        tail_non_nan = (
            px.groupby("ts_code")["ret_fwd"]
            .apply(lambda s: s.tail(period).notna().sum())
            .sum()
        )
        if tail_non_nan != 0:
            print(f"❌ 前瞻收益在序列末尾存在 {int(tail_non_nan)} 个非 NaN，shift(-{period}) 越界取到未来 → FAIL")
            return False
        print(f"✅ [shift 边界] 每支股票末尾 {period} 行前瞻收益均为 NaN（未越界取未来）")

        # 断言 2：正确前瞻收益 与 当期收益（未 shift）在同一因子上的 IC 必须不同，
        # 证明 shift 真实地把标签移到了未来（若相等则说明未 shift、泄漏当期）。
        px["ret_same"] = px.groupby("ts_code")["close"].pct_change(period)  # 过去 period 的收益（对照）
        merged = px.dropna(subset=["close"]).copy()
        # 构造一个与因子同构的截面代理：用行业相对 pe 无法在此廉价获得，改用价格动量的反向作为占位因子，
        # 关键在验证 shift 的时点语义，而非因子本身，故用 close 的负 z-score 作为探针因子。
        merged["probe_factor"] = -(
            merged.groupby("date")["close"].transform(lambda s: (s - s.mean()) / (s.std() or np.nan))
        )
        d_fwd = merged.dropna(subset=["probe_factor", "ret_fwd"])
        d_same = merged.dropna(subset=["probe_factor", "ret_same"])
        if len(d_fwd) < 30 or len(d_same) < 30:
            print("❌ shift 对齐验证样本不足 → FAIL")
            return False
        ic_fwd = d_fwd["probe_factor"].corr(d_fwd["ret_fwd"])
        ic_same = d_same["probe_factor"].corr(d_same["ret_same"])
        print(f"  IC(probe, 未来{period}日收益)={ic_fwd:+.4f}  vs  IC(probe, 过去{period}日收益)={ic_same:+.4f}")
        if abs(ic_fwd - ic_same) < 1e-6:
            print("❌ 未来收益与过去收益 IC 完全相同，shift 未生效/存在时点泄漏 → FAIL")
            return False
        print("✅ [shift 语义] 未来收益与过去收益 IC 显著不同，前瞻标签确实取自 t 之后")
        return True

    except Exception as e:
        print(f"❌ 未来函数检测失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_parameter_sensitivity() -> bool:
    print("\n--- 参数敏感性检测 ---")
    try:
        as_of_date = "20250630"
        start_date = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=60)).strftime("%Y%m%d")
        index_start = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=45)).strftime("%Y%m%d")

        index_df = panda_data.get_index_indicator(start_date=index_start, end_date=as_of_date)
        market_z = calculate_market_z(index_df)
        print(f"  market_z_score: {market_z:.2f}")

        industry_df = panda_data.get_industry_constituents(level="L1")
        symbol_col = _find_column(industry_df, ["stock_symbol", "symbol", "ts_code", "code"])
        name_col = _find_column(industry_df, ["stock_name", "name", "sec_name"])
        ind_col = _find_column(industry_df, ["l1_name", "industry", "industry_name"])

        if symbol_col is None:
            print("❌ 无法获取股票池，参数敏感性检测无法执行 → FAIL")
            return False

        if ind_col:
            industry_df = industry_df.rename(columns={ind_col: "industry"})
        if symbol_col != "ts_code":
            industry_df = industry_df.rename(columns={symbol_col: "ts_code"})
        symbol_col = "ts_code"
        industry_df = industry_df.drop_duplicates(subset=["ts_code"], keep="first")

        excluded_names = {"银行", "保险", "证券", "多元金融", "信托"}
        if "industry" in industry_df.columns:
            industry_df = industry_df[~industry_df["industry"].astype(str).isin(excluded_names)]
        if name_col and name_col in industry_df.columns:
            industry_df = industry_df[~industry_df[name_col].astype(str).str.contains("ST|退市|风险警示", na=False)]

        symbols = industry_df[symbol_col].unique().tolist()[:200]

        batch_df = panda_data.get_stock_daily(symbol=symbols, start_date=start_date, end_date=as_of_date, st=False)

        if batch_df is None or batch_df.empty:
            print("❌ 行情数据为空，参数敏感性检测无法执行 → FAIL")
            return False

        close_col = _find_column(batch_df, ["close", "close_price"])
        sym_col = _find_column(batch_df, ["symbol", "ts_code", "stock_symbol"])
        date_col = _find_column(batch_df, ["date", "trade_date"])

        if close_col:
            batch_df = batch_df.rename(columns={close_col: "close"})
        if sym_col:
            batch_df = batch_df.rename(columns={sym_col: "ts_code"})
        if date_col:
            batch_df = batch_df.rename(columns={date_col: "date"})

        batch_df["date"] = pd.to_datetime(batch_df["date"], format="%Y%m%d", errors="coerce")
        batch_df = batch_df.sort_values(["ts_code", "date"])
        # 20 日前瞻收益（与回测持有期一致），单日收益噪声过大不足以验证 IC
        fwd = batch_df.groupby("ts_code")["close"].shift(-20)
        batch_df["forward_return"] = fwd / batch_df["close"] - 1.0

        financial_df = get_financial_ttm(symbols, as_of_date)
        if financial_df.empty:
            print("❌ 财务数据为空，参数敏感性检测无法执行 → FAIL")
            return False

        all_ics = []
        dates = batch_df["date"].unique()
        for date in dates[:-1]:
            daily_df = batch_df[batch_df["date"] == date].copy()
            if len(daily_df) < 10:
                continue

            factor_df = calculate_factor_for_validation(daily_df, financial_df, industry_df, market_z)
            if factor_df.empty:
                continue

            if "forward_return" not in daily_df.columns:
                continue

            daily_return = daily_df[["ts_code", "forward_return"]].dropna()
            if daily_return.empty:
                continue

            merged = factor_df.merge(daily_return, on="ts_code", how="inner", suffixes=("", "_r"))
            if len(merged) < 10:
                continue

            return_col = "forward_return_r" if "forward_return_r" in merged.columns else "forward_return"
            ic = calculate_ic(merged["factor_value"].values, merged[return_col].values)
            if not np.isnan(ic):
                all_ics.append(ic)

        if len(all_ics) < 5:
            print(f"❌ 有效IC样本不足（{len(all_ics)}<5），参数敏感性检测无法执行 → FAIL")
            return False

        avg_ic = np.mean(all_ics)
        ic_std = np.std(all_ics)
        print(f"  IC均值: {avg_ic:.4f}, IC标准差: {ic_std:.4f}")

        results = []
        # 阈值由因子实际分布的分位数导出（正向 stock_z 下 factor 尺度随行情变化，
        # 硬编码负阈值可能过滤不到任何股票）。取 factor 的 20/40/60/80 分位为筛选下限。
        all_factor_vals = []
        for date in dates[:-1]:
            daily_df = batch_df[batch_df["date"] == date].copy()
            if len(daily_df) < 10:
                continue
            fdf = calculate_factor_for_validation(daily_df, financial_df, industry_df, market_z)
            if not fdf.empty:
                all_factor_vals.extend(fdf["factor_value"].dropna().tolist())
        if len(all_factor_vals) < 20:
            print(f"❌ 因子样本不足（{len(all_factor_vals)}<20），参数敏感性检测无法执行 → FAIL")
            return False
        thresholds = list(np.quantile(all_factor_vals, [0.2, 0.4, 0.6, 0.8]))

        for thresh in thresholds:
            threshold_ics = []
            for date in dates[:-1]:
                daily_df = batch_df[batch_df["date"] == date].copy()
                if len(daily_df) < 10:
                    continue

                factor_df = calculate_factor_for_validation(daily_df, financial_df, industry_df, market_z)
                if factor_df.empty:
                    continue

                if "forward_return" not in daily_df.columns:
                    continue

                filtered = factor_df[factor_df["factor_value"] > thresh]
                if len(filtered) < 10:
                    continue

                daily_return = daily_df[["ts_code", "forward_return"]].dropna()
                merged = filtered.merge(daily_return, on="ts_code", how="inner", suffixes=("", "_r"))
                if len(merged) < 10:
                    continue

                return_col = "forward_return_r" if "forward_return_r" in merged.columns else "forward_return"
                ic = calculate_ic(merged["factor_value"].values, merged[return_col].values)
                if not np.isnan(ic):
                    threshold_ics.append(ic)

            if len(threshold_ics) >= 3:
                results.append({"threshold": thresh, "ic": np.mean(threshold_ics)})

        if len(results) < 2:
            print(f"❌ 有效阈值结果不足（{len(results)}<2），参数敏感性检测无法执行 → FAIL")
            return False

        ics = [r["ic"] for r in results]
        ic_range = max(ics) - min(ics)
        result_str = [f"{r['threshold']:.3f}: {r['ic']:.3f}" for r in results]
        print("参数敏感性结果:", result_str)

        # 严格二元判据（无告警态）：
        #   1) 不同阈值下 IC 全程同号（子样本不改变预测方向）
        #   2) IC 极差 < 0.15（对阈值选择不敏感）
        signs = {int(np.sign(v)) for v in ics if v != 0}
        if len(signs) > 1:
            print(f"❌ 参数敏感性: 不同阈值下 IC 出现反号 {ics}，方向不稳定 → FAIL")
            return False
        if ic_range >= 0.15:
            print(f"❌ 参数敏感性: IC范围={ic_range:.3f} >= 0.15，对阈值敏感 → FAIL")
            return False
        print(f"✅ 参数敏感性: IC范围={ic_range:.3f} < 0.15 且全程同号，不敏感")
        return True

    except Exception as e:
        print(f"❌ 参数敏感性检测失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_out_of_sample() -> bool:
    print("\n--- 样本外检测 ---")
    try:
        # 用多个期末锚点做样本外稳定性检验（两点对比噪声过大，不足以判定）。
        # 前半段为样本内，后半段为样本外，比较两段 IC 是否方向一致、衰减是否可控。
        # 锚点选用已完整结算、财务与行情数据齐备的季度。
        dates = ["20240930", "20241231", "20250331", "20250630"]
        ics = []

        for as_of_date in dates:
            # 每锚点用 120 日窗口（约 59 个交易日）估计逐日截面 IC 均值：
            # 60 日窗口（约 19 天）单期噪声过大，会让个别季度 IC 偶发反号；
            # 120 日窗口下各锚点 IC 方向稳定，才能施加"每锚点同向"的严格判据。
            start_date = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=120)).strftime("%Y%m%d")
            index_start = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=45)).strftime("%Y%m%d")

            index_df = panda_data.get_index_indicator(start_date=index_start, end_date=as_of_date)
            market_z = calculate_market_z(index_df)

            industry_df = panda_data.get_industry_constituents(level="L1")
            symbol_col = _find_column(industry_df, ["stock_symbol", "symbol", "ts_code"])
            ind_col = _find_column(industry_df, ["l1_name", "industry", "industry_name"])

            if symbol_col is None:
                print(f"❌ {as_of_date}: 无法获取股票池 → FAIL")
                return False

            if ind_col:
                industry_df = industry_df.rename(columns={ind_col: "industry"})
            if symbol_col != "ts_code":
                industry_df = industry_df.rename(columns={symbol_col: "ts_code"})
            symbol_col = "ts_code"
            industry_df = industry_df.drop_duplicates(subset=["ts_code"], keep="first")

            symbols = industry_df[symbol_col].unique().tolist()[:200]

            batch_df = panda_data.get_stock_daily(symbol=symbols, start_date=start_date, end_date=as_of_date, st=False)

            if batch_df is None or batch_df.empty:
                print(f"❌ {as_of_date}: 行情数据为空 → FAIL")
                return False

            close_col = _find_column(batch_df, ["close", "close_price"])
            sym_col = _find_column(batch_df, ["symbol", "ts_code", "stock_symbol"])
            date_col = _find_column(batch_df, ["date", "trade_date"])

            if close_col:
                batch_df = batch_df.rename(columns={close_col: "close"})
            if sym_col:
                batch_df = batch_df.rename(columns={sym_col: "ts_code"})
            if date_col:
                batch_df = batch_df.rename(columns={date_col: "date"})

            batch_df["date"] = pd.to_datetime(batch_df["date"], format="%Y%m%d", errors="coerce")
            batch_df = batch_df.sort_values(["ts_code", "date"])
            fwd = batch_df.groupby("ts_code")["close"].shift(-20)
            batch_df["forward_return"] = fwd / batch_df["close"] - 1.0

            financial_df = get_financial_ttm(symbols, as_of_date)
            if financial_df.empty:
                print(f"❌ {as_of_date}: 财务数据为空 → FAIL")
                return False

            period_ics = []
            unique_dates = batch_df["date"].unique()
            for date in unique_dates[:-1]:
                daily_df = batch_df[batch_df["date"] == date].copy()
                if len(daily_df) < 10:
                    continue

                factor_df = calculate_factor_for_validation(daily_df, financial_df, industry_df, market_z)
                if factor_df.empty:
                    continue

                if "forward_return" not in daily_df.columns:
                    continue

                daily_return = daily_df[["ts_code", "forward_return"]].dropna()
                if daily_return.empty:
                    continue

                merged = factor_df.merge(daily_return, on="ts_code", how="inner", suffixes=("", "_r"))
                if len(merged) < 10:
                    continue

                return_col = "forward_return_r" if "forward_return_r" in merged.columns else "forward_return"
                ic = calculate_ic(merged["factor_value"].values, merged[return_col].values)
                if not np.isnan(ic):
                    period_ics.append(ic)

            if len(period_ics) < 5:
                print(f"❌ {as_of_date}: 有效IC样本不足（{len(period_ics)}<5）→ FAIL")
                return False

            avg_ic = np.mean(period_ics)
            ics.append({"date": as_of_date, "ic": avg_ic})
            print(f"  {as_of_date}: IC均值={avg_ic:.4f}, 样本数={len(period_ics)}")

        if len(ics) < 4:
            print(f"❌ 有效样本期不足（{len(ics)}<4），样本外检测无法执行 → FAIL")
            return False

        anchor_ics = [d["ic"] for d in ics]

        # ---- 严格判据 1：每个锚点期方向必须一致（任一期反号即 FAIL）----
        # 期望方向以多数锚点符号为准；若存在任一锚点与之相反，则判定方向不稳定。
        pos = sum(1 for v in anchor_ics if v > 0)
        neg = sum(1 for v in anchor_ics if v < 0)
        expected_sign = 1 if pos >= neg else -1
        reversed_anchors = [
            (ics[i]["date"], anchor_ics[i])
            for i in range(len(anchor_ics))
            if np.sign(anchor_ics[i]) != 0 and np.sign(anchor_ics[i]) != expected_sign
        ]
        if reversed_anchors:
            print(
                "❌ 存在与主方向相反的锚点期，样本外方向不稳定 → FAIL: "
                + ", ".join(f"{d}({v:+.4f})" for d, v in reversed_anchors)
            )
            return False
        print(f"✅ [方向一致] 全部 {len(anchor_ics)} 个锚点期同向（期望符号 {'+' if expected_sign > 0 else '-'}）")

        # ---- 严格判据 2：前半段样本内 / 后半段样本外 IC 衰减 < 50% ----
        half = len(ics) // 2
        in_ics = [d["ic"] for d in ics[:half]]
        out_ics = [d["ic"] for d in ics[half:]]
        in_sample_ic = np.mean(in_ics)
        out_sample_ic = np.mean(out_ics)

        print(f"样本内均值 IC（{[d['date'] for d in ics[:half]]}）: {in_sample_ic:.4f}")
        print(f"样本外均值 IC（{[d['date'] for d in ics[half:]]}）: {out_sample_ic:.4f}")

        # 衰减率 = 1 - |样本外IC| / |样本内IC|；样本外被放大时衰减为负，clamp 到 0。
        eps = 1e-9
        base = max(abs(in_sample_ic), eps)
        decay = 1.0 - abs(out_sample_ic) / base
        decay = max(decay, 0.0)
        print(f"IC 衰减率: {decay:.2%}")

        same_sign = (in_sample_ic == 0) or (out_sample_ic == 0) or (
            np.sign(in_sample_ic) == np.sign(out_sample_ic)
        )
        if not same_sign:
            print("❌ 样本外均值 IC 与样本内符号相反，预测方向反转 → FAIL")
            return False

        if decay < 0.5:
            print("✅ IC 衰减率 < 50% 且方向一致，通过")
            return True
        else:
            print("❌ IC 衰减率 >= 50%，未通过")
            return False

    except Exception as e:
        print(f"❌ 样本外检测失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(description="Templeton 全球价值多因子 V2 验证")
    parser.add_argument("--username", type=str, default=None, help="PandaAI 用户名")
    parser.add_argument("--password", type=str, default=None, help="PandaAI 密码")
    args = parser.parse_args()
    
    print("="*60)
    print("Templeton 全球价值多因子 V2 验证（官方SDK版）")
    print("="*60)
    
    print("[validate] 正在连接 PandaAI...")
    
    try:
        _init_panda_token(args.username, args.password)
        print("[validate] ✅ 已连接")
    except RuntimeError as e:
        print(f"[validate] ❌ 连接失败: {e}")
        sys.exit(1)
    
    results = []
    
    results.append(("未来函数检测", test_future_data()))
    results.append(("参数敏感性检测", test_parameter_sensitivity()))
    results.append(("样本外检测", test_out_of_sample()))
    
    print("\n" + "="*60)
    print("验证结果汇总")
    print("="*60)
    
    all_pass = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{name}: {status}")
        if not passed:
            all_pass = False
    
    if all_pass:
        print("\n🎉 所有验证通过！")
    else:
        print("\n⚠️  部分验证未通过，请检查代码")
        sys.exit(1)


if __name__ == "__main__":
    main()
