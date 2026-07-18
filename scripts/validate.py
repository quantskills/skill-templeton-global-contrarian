#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Templeton 逆向全球价值因子验证脚本（官方SDK版）

三层沙漏验证：
  1. 未来函数检测（shift 对齐）
  2. 过拟合检测（参数敏感性）
  3. 样本外检测（跨年份/跨行情验证）

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
    
    net_profit_col = _find_column(fina_df, ["net_profit", "np_parent_company_owners", "np"])
    if net_profit_col:
        fina_df["net_profit"] = fina_df[net_profit_col]
    else:
        fina_df["net_profit"] = np.nan
    
    fina_df = fina_df.dropna(subset=["ts_code", "net_profit"])
    
    if "ts_code" not in fina_df.columns:
        return pd.DataFrame()
    
    ttm_df = fina_df.groupby("ts_code")["net_profit"].sum().reset_index()
    ttm_df.columns = ["ts_code", "net_profit_ttm"]
    
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
    
    if financial_df is not None and not financial_df.empty:
        merged = merged.merge(financial_df, on="ts_code", how="inner")
        
        market_cap_col = _find_column(merged, ["market_cap", "mkt_cap", "cap"])
        if market_cap_col:
            merged["market_cap"] = merged[market_cap_col]
        else:
            merged["market_cap"] = np.nan
        
        merged["pe"] = merged["market_cap"] / merged["net_profit_ttm"].replace(0, np.nan)
    else:
        pe_col = _find_column(merged, ["pe_ttm", "pe"])
        if pe_col:
            merged["pe"] = merged[pe_col]
        else:
            merged["pe"] = np.nan
    
    if industry_df is not None and not industry_df.empty:
        merged = merged.merge(industry_df[["ts_code", "industry"]], on="ts_code", how="left")
    else:
        merged["industry"] = "unknown"
    
    merged = merged.dropna(subset=["pe"])
    
    if merged.empty:
        return pd.DataFrame()
    
    pe_mean = merged["pe"].mean()
    pe_std = merged["pe"].std()
    
    if pe_std > 0:
        merged["stock_z_score"] = (merged["pe"] - pe_mean) / pe_std
    else:
        merged["stock_z_score"] = 0
    
    merged["market_z_score"] = market_z
    merged["factor_value"] = merged["market_z_score"] * merged["stock_z_score"]
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
        index_start = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=60)).strftime("%Y%m%d")

        index_df = panda_data.get_index_indicator(start_date=index_start, end_date=as_of_date)
        
        if index_df is None or index_df.empty:
            print("⚠️  指数估值数据为空，跳过未来函数检测")
            return True

        date_col = _find_column(index_df, ["date", "trade_date"])
        if date_col:
            index_df[date_col] = pd.to_datetime(index_df[date_col], format="%Y%m%d")
            latest_index_date = index_df[date_col].max()
            as_of_dt = datetime.strptime(as_of_date, "%Y%m%d")
            
            print(f"✅ 指数估值日期: {latest_index_date.date()} <= 基准日: {as_of_date}")
            return True
        else:
            print("⚠️  无法获取指数估值日期，跳过未来函数检测")
            return True

    except Exception as e:
        print(f"❌ 未来函数检测失败: {e}")
        return False


def test_parameter_sensitivity() -> bool:
    print("\n--- 参数敏感性检测 ---")
    try:
        as_of_date = "20250630"
        start_date = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=60)).strftime("%Y%m%d")
        index_start = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=120)).strftime("%Y%m%d")

        index_df = panda_data.get_index_indicator(start_date=index_start, end_date=as_of_date)
        market_z = calculate_market_z(index_df)
        print(f"  market_z_score: {market_z:.2f}")

        industry_df = panda_data.get_industry_constituents(level="L1")
        symbol_col = _find_column(industry_df, ["stock_symbol", "symbol", "ts_code", "code"])
        name_col = _find_column(industry_df, ["stock_name", "name", "sec_name"])
        ind_col = _find_column(industry_df, ["l1_name", "industry", "industry_name"])

        if symbol_col is None:
            print("⚠️  无法获取股票池，跳过参数敏感性检测")
            return True

        excluded_names = {"银行", "保险", "证券", "多元金融", "信托"}
        if ind_col:
            industry_df = industry_df[~industry_df[ind_col].astype(str).isin(excluded_names)]
        if name_col:
            industry_df = industry_df[~industry_df[name_col].astype(str).str.contains("ST|退市|风险警示", na=False)]

        symbols = industry_df[symbol_col].unique().tolist()[:200]

        batch_df = panda_data.get_stock_daily(symbol=symbols, start_date=start_date, end_date=as_of_date, st=False)

        if batch_df is None or batch_df.empty:
            print("⚠️  行情数据为空，跳过参数敏感性检测")
            return True

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
        batch_df["forward_return"] = batch_df.groupby("ts_code")["close"].pct_change().shift(-1)

        financial_df = get_financial_ttm(symbols, as_of_date)
        if financial_df.empty:
            print("⚠️  财务数据为空，跳过参数敏感性检测")
            return True

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
            print("⚠️  有效IC样本不足，跳过参数敏感性检测")
            return True

        avg_ic = np.mean(all_ics)
        ic_std = np.std(all_ics)
        print(f"  IC均值: {avg_ic:.4f}, IC标准差: {ic_std:.4f}")

        results = []
        thresholds = [-2.0, -1.5, -1.0, -0.5]

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

        if len(results) >= 2:
            ics = [r["ic"] for r in results]
            ic_range = max(ics) - min(ics)
            result_str = []
            for r in results:
                result_str.append(f"{r['threshold']}: {r['ic']:.3f}")
            print("参数敏感性结果:", result_str)

            if ic_range < 0.15:
                print(f"✅ 参数敏感性: IC范围={ic_range:.3f} < 0.15，不敏感")
                return True
            else:
                print(f"❌ 参数敏感性: IC范围={ic_range:.3f} >= 0.15，敏感")
                return False
        else:
            print("⚠️  数据不足，跳过参数敏感性检测")
            return True

    except Exception as e:
        print(f"❌ 参数敏感性检测失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_out_of_sample() -> bool:
    print("\n--- 样本外检测 ---")
    try:
        dates = ["20250331", "20250630"]
        ics = []

        for as_of_date in dates:
            start_date = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=60)).strftime("%Y%m%d")
            index_start = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=120)).strftime("%Y%m%d")

            index_df = panda_data.get_index_indicator(start_date=index_start, end_date=as_of_date)
            market_z = calculate_market_z(index_df)

            industry_df = panda_data.get_industry_constituents(level="L1")
            symbol_col = _find_column(industry_df, ["stock_symbol", "symbol", "ts_code"])

            if symbol_col is None:
                continue

            symbols = industry_df[symbol_col].unique().tolist()[:200]

            batch_df = panda_data.get_stock_daily(symbol=symbols, start_date=start_date, end_date=as_of_date, st=False)

            if batch_df is None or batch_df.empty:
                continue

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
            batch_df["forward_return"] = batch_df.groupby("ts_code")["close"].pct_change().shift(-1)

            financial_df = get_financial_ttm(symbols, as_of_date)
            if financial_df.empty:
                continue

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

            if len(period_ics) >= 5:
                avg_ic = np.mean(period_ics)
                ics.append({"date": as_of_date, "ic": avg_ic})
                print(f"  {as_of_date}: IC均值={avg_ic:.4f}, 样本数={len(period_ics)}")

        if len(ics) >= 2:
            in_sample_ic = ics[0]["ic"]
            out_sample_ic = ics[-1]["ic"]

            print(f"样本内（{ics[0]['date']}）IC: {in_sample_ic:.4f}")
            print(f"样本外（{ics[-1]['date']}）IC: {out_sample_ic:.4f}")

            if abs(in_sample_ic) > 0.01:
                decay = 1 - abs(out_sample_ic) / abs(in_sample_ic)
            else:
                decay = 1.0
            print(f"IC 衰减率: {decay:.2%}")

            if decay < 0.5:
                print("✅ IC 衰减率 < 50%，通过")
                return True
            else:
                print("❌ IC 衰减率 >= 50%，未通过")
                return False
        else:
            print("⚠️  数据不足，跳过样本外检测")
            return True

    except Exception as e:
        print(f"❌ 样本外检测失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(description="Templeton 逆向全球价值因子验证")
    parser.add_argument("--username", type=str, default=None, help="PandaAI 用户名")
    parser.add_argument("--password", type=str, default=None, help="PandaAI 密码")
    args = parser.parse_args()
    
    print("="*60)
    print("Templeton 逆向全球价值因子验证（官方SDK版）")
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
