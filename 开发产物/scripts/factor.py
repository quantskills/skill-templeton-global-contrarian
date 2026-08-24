#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Templeton 逆向全球价值因子计算脚本（官方SDK版）

修复内容：
  P0-1：非A股市场因子计算（left join + 日线PE/PB或52周低点比率）
  P0-2：PE TTM年化（最近4个季度累加净利润）
  P0-3：因子符号方向（market_z × stock_z，不带负号）
  P1-4：验证和回测使用真实因子计算逻辑

因子逻辑（V2 全球价值多因子）：
  - 7 子因子：EP/BP/SP/股息/ROE/杠杆/动量，各自截面 RANK → 市值中性化
    （每子因子减 0.1×市值 zscore）→ 按可得子因子集合归一化加权 → SCALE(raw_score)
  - factor_value = SCALE(raw_score)，方向为「大=买」：raw_score 越大 = 越优质便宜
    * 买卖信号按截面分位：score = factor_value 的截面百分位 × 100，
      buy = score ≥ 80，sell = score < 20，其余 hold
  - 三市降级（数据权限所致，详见 SKILL.md「降级声明」）：
    * A股：完整 7 子因子（get_fina_reports 自算；动量窗口不足则权重置 0）
    * 港股：仅 EP（PB 可得则 EP+BP）
    * 美股：无 PE 权限，退化为 SCALE(-pct_from_low) 价格代理

API调用方式：使用官方 panda_data SDK
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

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
    print(f"  TTM季度范围: {ttm_quarters}")
    
    all_fina = []
    for quarter in ttm_quarters:
        print(f"    获取财务数据: {quarter}")
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
        except Exception as e:
            print(f"    ⚠️  获取{quarter}数据失败: {e}")
            continue
    
    if not all_fina:
        print("    ⚠️  get_fina_reports 返回空，尝试使用 get_fina_statement")
        try:
            df = panda_data.get_fina_statement(symbol=ts_codes[:100])
            if df is not None and not df.empty:
                df["quarter"] = "latest"
                all_fina.append(df)
                print(f"    get_fina_statement 返回 {len(df)} 条记录")
        except Exception as e:
            print(f"    ⚠️  get_fina_statement 也失败: {e}")
    
    if not all_fina:
        return pd.DataFrame()
    
    fina_df = pd.concat(all_fina, ignore_index=True)
    
    ts_col = _find_column(fina_df, ["ts_code", "symbol", "stock_symbol"])
    if ts_col:
        fina_df = fina_df.rename(columns={ts_col: "ts_code"})
    
    # V2 多子因子所需字段：
    #   累加类（TTM，近4季 sum）：净利润、营收、股利
    #   存量类（时点值，取最新一季）：净资产(不含少数)、总负债、总资产、总股本
    net_profit_col = _find_column(fina_df, ["net_profit", "np_parent_company_owners", "np", "is_n_income_attr_p", "is_end_net_profit"])
    revenue_col = _find_column(fina_df, ["is_total_revenue", "is_revenue", "revenue"])
    div_col = _find_column(fina_df, ["is_div_payt", "dividend"])
    equity_col = _find_column(fina_df, ["bs_total_hldr_eqy_exc_min_int", "total_equity", "所有者权益合计"])
    liab_col = _find_column(fina_df, ["bs_total_liab", "total_liab"])
    assets_col = _find_column(fina_df, ["bs_total_assets", "total_assets"])
    shares_col = _find_column(fina_df, ["bs_cap_stk", "total_shares", "total_share"])
    
    fina_df["net_profit"] = pd.to_numeric(fina_df[net_profit_col], errors="coerce") if net_profit_col else np.nan
    fina_df["revenue"] = pd.to_numeric(fina_df[revenue_col], errors="coerce") if revenue_col else np.nan
    fina_df["dividend"] = pd.to_numeric(fina_df[div_col], errors="coerce") if div_col else np.nan
    fina_df["equity"] = pd.to_numeric(fina_df[equity_col], errors="coerce") if equity_col else np.nan
    fina_df["total_liab"] = pd.to_numeric(fina_df[liab_col], errors="coerce") if liab_col else np.nan
    fina_df["total_assets"] = pd.to_numeric(fina_df[assets_col], errors="coerce") if assets_col else np.nan
    fina_df["total_shares"] = pd.to_numeric(fina_df[shares_col], errors="coerce") if shares_col else np.nan
    
    fina_df = fina_df.dropna(subset=["ts_code", "net_profit"])
    
    if "ts_code" not in fina_df.columns:
        return pd.DataFrame()
    
    # 季度排序：_get_ttm_quarters 返回从新到旧，quarter_rank 越大越新，last=最新季
    quarter_order = {q: i for i, q in enumerate(reversed(_get_ttm_quarters(as_of_date)))}
    if "quarter" in fina_df.columns:
        fina_df["_qrank"] = fina_df["quarter"].map(quarter_order).fillna(-1)
        fina_df = fina_df.sort_values(["ts_code", "_qrank"])
    
    # 累加类（流量）
    ttm_df = fina_df.groupby("ts_code").agg(
        net_profit_ttm=("net_profit", "sum"),
        revenue_ttm=("revenue", "sum"),
        div_ttm=("dividend", "sum"),
    ).reset_index()
    # 存量类（取最新季）
    latest_df = fina_df.groupby("ts_code").agg(
        equity=("equity", "last"),
        total_liab=("total_liab", "last"),
        total_assets=("total_assets", "last"),
        total_shares=("total_shares", "last"),
    ).reset_index()
    ttm_df = ttm_df.merge(latest_df, on="ts_code", how="left")
    
    return ttm_df


def get_valuation_data(ts_codes: list, market: str) -> pd.DataFrame:
    if not ts_codes:
        return pd.DataFrame()
    
    try:
        api = None
        if market == "cn":
            api = panda_data.get_stock_mktfin_metric
        elif market == "hk":
            api = panda_data.get_stock_mktfin_indicator
        
        if api is None:
            return pd.DataFrame()
        
        # 接口单次最多 500 只，超出则分片获取，保证港股/全市场覆盖完整
        chunks = [ts_codes[i:i+500] for i in range(0, len(ts_codes), 500)]
        all_dfs = []
        for chunk in chunks:
            try:
                df = api(symbol=chunk)
                if df is not None and not df.empty:
                    all_dfs.append(df)
            except Exception as e:
                print(f"    ⚠️  获取{market}估值分片(前5: {chunk[:5]})失败: {e}")
                continue
        if not all_dfs:
            return pd.DataFrame()
        df = pd.concat(all_dfs, ignore_index=True)
        print(f"  获取{market}估值数据: {len(df)} 条记录（{len(chunks)} 分片）")
        
        ts_col = _find_column(df, ["symbol", "ts_code", "stock_symbol"])
        if ts_col:
            df = df.rename(columns={ts_col: "ts_code"})
        
        pe_cols = ["curr_pe_dil_excl_ttm", "curr_pe_basic_excl_ttm", "pe_ttm", "pe"]
        pb_cols = ["curr_pb", "pb_ttm", "pb"]
        
        pe_col = None
        for col in pe_cols:
            if col in df.columns:
                pe_col = col
                break
        
        pb_col = None
        for col in pb_cols:
            if col in df.columns:
                pb_col = col
                break
        
        result = df[["ts_code"]].copy()
        if pe_col:
            result["pe"] = pd.to_numeric(df[pe_col], errors="coerce")
        else:
            result["pe"] = np.nan
        if pb_col:
            result["pb"] = pd.to_numeric(df[pb_col], errors="coerce")
        else:
            result["pb"] = np.nan
        
        result = result.dropna(subset=["pe"])
        result = result.drop_duplicates(subset=["ts_code"], keep="first")
        print(f"  去重后估值数据: {len(result)} 条记录")
        
        return result
    
    except Exception as e:
        print(f"    ⚠️  获取{market}估值数据失败: {e}")
    
    return pd.DataFrame()


def compute_price_proxy(daily_getter, symbols, as_of_date: str, lookback_days: int = 250) -> pd.DataFrame:
    """52 周低点价格代理：pct_from_low = (close - low_win) / low_win。
    用于无 PE 估值权限的市场（如美股）。值越小越接近低点、越"低估"。
    daily_getter: 接收 (symbol, start_date, end_date) 的日线获取函数。
    返回 [ts_code, close, pct_from_low]。
    """
    start = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=lookback_days)).strftime("%Y%m%d")
    try:
        hist = daily_getter(symbol=symbols, start_date=start, end_date=as_of_date)
    except Exception as e:
        print(f"    ⚠️  价格代理历史数据获取失败: {e}")
        return pd.DataFrame()
    if hist is None or hist.empty:
        return pd.DataFrame()

    sym_col = _find_column(hist, ["symbol", "ts_code", "stock_symbol"])
    close_col = _find_column(hist, ["close", "close_price"])
    date_col = _find_column(hist, ["date", "trade_date"])
    if not (sym_col and close_col and date_col):
        return pd.DataFrame()

    hist = hist.rename(columns={sym_col: "ts_code", close_col: "close", date_col: "date"})
    hist["close"] = pd.to_numeric(hist["close"], errors="coerce")
    hist = hist.dropna(subset=["ts_code", "close", "date"])
    hist = hist[hist["close"] > 0]
    if hist.empty:
        return pd.DataFrame()

    low_win = hist.groupby("ts_code")["close"].min().rename("low_win")
    # 取每只股票窗口内最后一个交易日的收盘价作为当前价
    hist = hist.sort_values(["ts_code", "date"])
    last_close = hist.groupby("ts_code")["close"].last().rename("close")

    proxy = pd.concat([last_close, low_win], axis=1).reset_index()
    proxy["pct_from_low"] = (proxy["close"] - proxy["low_win"]) / proxy["low_win"].replace(0, np.nan)
    proxy = proxy.dropna(subset=["pct_from_low"])
    return proxy[["ts_code", "close", "pct_from_low"]]


def get_momentum(daily_getter, symbols, as_of_date: str) -> pd.DataFrame:
    """12-1 月动量：mom_12_1 = (close/close_252 - 1) - (close/close_21 - 1)。
    拉取 as_of_date - 400 自然日历史（≈覆盖 252 交易日），分片 500 只。
    返回 [ts_code, mom_12_1]。窗口不足则该股为 NaN。
    """
    if not symbols:
        return pd.DataFrame()
    start = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=400)).strftime("%Y%m%d")
    chunks = [symbols[i:i+500] for i in range(0, len(symbols), 500)]
    parts = []
    for chunk in chunks:
        try:
            df = daily_getter(symbol=chunk, start_date=start, end_date=as_of_date)
            if df is not None and not df.empty:
                parts.append(df)
        except Exception as e:
            print(f"    ⚠️  动量历史分片(前5: {chunk[:5]})失败: {e}")
            continue
    if not parts:
        return pd.DataFrame()
    hist = pd.concat(parts, ignore_index=True)

    sym_col = _find_column(hist, ["symbol", "ts_code", "stock_symbol"])
    close_col = _find_column(hist, ["close", "close_price"])
    date_col = _find_column(hist, ["date", "trade_date"])
    if not (sym_col and close_col and date_col):
        return pd.DataFrame()
    hist = hist.rename(columns={sym_col: "ts_code", close_col: "close", date_col: "date"})
    hist["close"] = pd.to_numeric(hist["close"], errors="coerce")
    hist = hist.dropna(subset=["ts_code", "close", "date"])
    hist = hist[hist["close"] > 0]
    if hist.empty:
        return pd.DataFrame()

    hist = hist.sort_values(["ts_code", "date"])
    g = hist.groupby("ts_code")["close"]
    hist["close_252"] = g.shift(252)
    hist["close_21"] = g.shift(21)
    # 取每只股票最后一个交易日（as_of_date 当日或最近）的动量
    last = hist.groupby("ts_code").last().reset_index()
    ret_12m = last["close"] / last["close_252"] - 1.0
    ret_1m = last["close"] / last["close_21"] - 1.0
    last["mom_12_1"] = ret_12m - ret_1m
    return last[["ts_code", "mom_12_1"]]



def get_industry_data() -> pd.DataFrame:
    print("[factor] 获取行业数据")
    try:
        df = panda_data.get_industry_constituents(level="L1")
        if df is None or df.empty:
            return pd.DataFrame()
        
        symbol_col = _find_column(df, ["stock_symbol", "symbol", "ts_code", "code"])
        name_col = _find_column(df, ["stock_name", "name", "sec_name"])
        ind_col = _find_column(df, ["l1_name", "industry", "industry_name"])
        
        if symbol_col:
            df = df.rename(columns={symbol_col: "ts_code"})
        if name_col:
            df = df.rename(columns={name_col: "stock_name"})
        if ind_col:
            df = df.rename(columns={ind_col: "industry"})
        
        return df[["ts_code", "stock_name", "industry"]].dropna().drop_duplicates(subset=["ts_code"], keep="first")
    except Exception as e:
        print(f"[factor] ⚠️  获取行业数据失败: {e}")
        return pd.DataFrame()


def calculate_factor_for_market(
    daily_df: pd.DataFrame,
    financial_df: pd.DataFrame,
    industry_df: pd.DataFrame,
    market_z_score: float,
    market: str,
    as_of_date: str = None,
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
    
    if market == "cn":
        market_df = daily_df.copy()
        
        if financial_df is not None and not financial_df.empty:
            market_df = market_df.merge(financial_df, on="ts_code", how="left")
            
            if "pe" in financial_df.columns:
                market_df["pe"] = market_df["pe"]
                market_df["valuation_method"] = "PE_indicator"
            elif "net_profit_ttm" in financial_df.columns:
                market_cap_col = _find_column(market_df, ["market_cap", "mkt_cap", "cap"])
                if market_cap_col:
                    market_df["market_cap"] = market_df[market_cap_col]
                    market_df["pe"] = market_df["market_cap"] / market_df["net_profit_ttm"].replace(0, np.nan)
                    market_df["valuation_method"] = "PE_TTM"
                else:
                    print(f"    ⚠️  日线数据无market_cap，尝试从股本接口计算")
                    ts_codes = market_df["ts_code"].unique().tolist()
                    try:
                        # get_share_float 单日区间返回空，需取区间内最后一条股本；
                        # 套餐限额（错误码600003）限制结果集行数，窗口取 10 天以内
                        share_start = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=10)).strftime("%Y%m%d")
                        chunks = [ts_codes[i:i+500] for i in range(0, len(ts_codes), 500)]
                        share_parts = []
                        for chunk in chunks:
                            try:
                                part = panda_data.get_share_float(
                                    symbol=chunk,
                                    start_date=share_start,
                                    end_date=as_of_date
                                )
                                if part is not None and not part.empty:
                                    share_parts.append(part)
                            except Exception as e:
                                print(f"    ⚠️  股本分片失败: {e}")
                                continue
                        if share_parts:
                            share_df = pd.concat(share_parts, ignore_index=True)
                        else:
                            share_df = None
                        if share_df is not None and not share_df.empty:
                            ts_col = _find_column(share_df, ["symbol", "ts_code"])
                            if ts_col:
                                share_df = share_df.rename(columns={ts_col: "ts_code"})
                            date_col = _find_column(share_df, ["date", "trade_date"])
                            if "total" in share_df.columns:
                                if date_col:
                                    share_df[date_col] = pd.to_numeric(share_df[date_col], errors="coerce")
                                    share_df = share_df.sort_values([ "ts_code", date_col])
                                latest_share = share_df.drop_duplicates(subset=["ts_code"], keep="last")
                                market_df = market_df.merge(latest_share[["ts_code", "total"]], on="ts_code", how="left")
                                market_df["market_cap"] = market_df["total"] * market_df["close"]
                                market_df["pe"] = market_df["market_cap"] / market_df["net_profit_ttm"].replace(0, np.nan)
                                market_df["valuation_method"] = "PE_TTM_calc"
                            else:
                                market_df["pe"] = np.nan
                                market_df["valuation_method"] = "unknown"
                        else:
                            market_df["pe"] = np.nan
                            market_df["valuation_method"] = "unknown"
                    except Exception as e:
                        print(f"    ⚠️  获取股本数据失败: {e}")
                        market_df["pe"] = np.nan
                        market_df["valuation_method"] = "unknown"
            else:
                market_df["pe"] = np.nan
                market_df["valuation_method"] = "unknown"
        else:
            pe_col = _find_column(market_df, ["pe_ttm", "pe"])
            if pe_col:
                market_df["pe"] = market_df[pe_col]
                market_df["valuation_method"] = "PE_daily"
            else:
                market_df["pe"] = np.nan
                market_df["valuation_method"] = "unknown"
        
        if industry_df is not None and not industry_df.empty:
            market_df = market_df.merge(industry_df, on="ts_code", how="left")
        else:
            market_df["industry"] = "unknown"
        
        market_df["market"] = "cn"
    
    else:
        market_df = daily_df.copy()
        
        if financial_df is not None and not financial_df.empty:
            if "pct_from_low" in financial_df.columns:
                # 价格代理（美股无PE权限）：只并入代理列，避免与日线 close 冲突
                proxy_cols = ["ts_code", "pct_from_low"]
                market_df = market_df.merge(financial_df[proxy_cols], on="ts_code", how="inner")
                market_df["valuation_method"] = "PRICE_PROXY_52w_low"
            else:
                market_df = market_df.merge(financial_df, on="ts_code", how="left")
                market_df["valuation_method"] = "PE_indicator"
        else:
            pe_col = _find_column(market_df, ["pe_ttm", "pe"])
            pb_col = _find_column(market_df, ["pb_ttm", "pb_lf", "pb"])
            
            if pe_col:
                market_df["pe"] = market_df[pe_col]
                market_df["valuation_method"] = "PE_daily"
            elif pb_col:
                market_df["pb"] = market_df[pb_col]
                market_df["valuation_method"] = "PB_daily"
            else:
                market_df["pe"] = np.nan
                market_df["valuation_method"] = "unknown"
        
        market_df["market"] = market
    
    if "industry" not in market_df.columns:
        market_df["industry"] = "unknown"
    market_df["industry"] = market_df["industry"].fillna("unknown")

    # ===================== V2 全球价值多因子 =====================
    # 7 子因子（EP/BP/SP/股息/ROE/杠杆/动量）各自截面 RANK → 市值中性 → 按可得
    # 子因子集合归一化加权 → SCALE(raw_score)。方向：raw_score 越大 = 越优质便宜 = 买。
    #   - cn：get_fina_reports 自算完整 7 子因子（动量窗口不足则权重置 0 降级）
    #   - hk：get_stock_mktfin_indicator 仅 EP（PB 可得则 EP+BP）
    #   - us：无 PE 权限，退化为 SCALE(-pct_from_low) 价格代理，不走子因子链
    if market == "us":
        # 美股：价格代理，越接近低点越"低估"，factor_value = SCALE(-pct_from_low)
        if "pct_from_low" not in market_df.columns:
            return pd.DataFrame()
        market_df = market_df.dropna(subset=["pct_from_low"])
        market_df = market_df[market_df["pct_from_low"] >= 0]
        if market_df.empty:
            return pd.DataFrame()
        # 截面 99% 分位 winsorize：pct_from_low 存在极端离群（如拆股/仙股导致 52 周低点近 0，
        # 比值可达数万倍），未处理会令 SCALE(z-score) 出现 -70 级异常值。与 V2 对估值倍数
        # 做限幅的思路一致，此处对价格代理做上限截尾（不改排序，仅压缩极端尾部）。
        pfl = pd.to_numeric(market_df["pct_from_low"], errors="coerce")
        upper = pfl.quantile(0.99)
        if upper is not None and np.isfinite(upper) and upper > 0:
            market_df["pct_from_low"] = pfl.clip(upper=upper)
        market_df["raw_score"] = op_scale(-market_df["pct_from_low"])
        market_df["valuation_metric"] = market_df["raw_score"]
        if "pe" not in market_df.columns:
            market_df["pe"] = np.nan
        if "pb" not in market_df.columns:
            market_df["pb"] = np.nan
        market_df["industry_pe_avg"] = np.nan
    else:
        if market == "cn":
            # A股：由财务字段派生 V2 各估值/质量比率
            if "market_cap" not in market_df.columns:
                market_df["market_cap"] = np.nan
            mcap = pd.to_numeric(market_df["market_cap"], errors="coerce")
            np_ttm = pd.to_numeric(market_df.get("net_profit_ttm", np.nan), errors="coerce")
            rev_ttm = pd.to_numeric(market_df.get("revenue_ttm", np.nan), errors="coerce")
            div_ttm = pd.to_numeric(market_df.get("div_ttm", np.nan), errors="coerce")
            equity = pd.to_numeric(market_df.get("equity", np.nan), errors="coerce")
            liab = pd.to_numeric(market_df.get("total_liab", np.nan), errors="coerce")
            assets = pd.to_numeric(market_df.get("total_assets", np.nan), errors="coerce")

            market_df["pe"] = mcap / np_ttm.replace(0, np.nan)
            market_df["pb"] = mcap / equity.replace(0, np.nan)
            market_df["ps"] = mcap / rev_ttm.replace(0, np.nan)
            market_df["div_yield"] = div_ttm / mcap.replace(0, np.nan)
            market_df["roe"] = np_ttm / equity.replace(0, np.nan)
            market_df["leverage"] = liab / assets.replace(0, np.nan)

            # 动量：拉 400 天历史算 12-1 月动量并入；窗口不足则整列缺失 → 权重降级为 0
            has_momentum = False
            try:
                cn_symbols = market_df["ts_code"].unique().tolist()
                mom_df = get_momentum(panda_data.get_stock_daily, cn_symbols, as_of_date)
                if mom_df is not None and not mom_df.empty:
                    market_df = market_df.merge(mom_df, on="ts_code", how="left")
                    has_momentum = market_df["mom_12_1"].notna().any()
            except Exception as e:
                print(f"    ⚠️  A股动量计算失败，动量权重降级为0: {e}")
            if not has_momentum:
                market_df["mom_12_1"] = np.nan
                print(f"    ⚠️  A股动量窗口不足，动量子因子权重降级为0")

            available = get_available_subfactors("cn", has_pb=True, has_momentum=has_momentum)
            market_df["valuation_method"] = "V2_MULTIFACTOR_cn"
        else:  # hk
            # 港股：仅 EP（若 PB 可得则 EP+BP）；无 ps/股息/roe/杠杆/营收权限
            if "pe" not in market_df.columns:
                market_df["pe"] = np.nan
            market_df = market_df.dropna(subset=["pe"])
            market_df = market_df[market_df["pe"] > 0]
            if market_df.empty:
                return pd.DataFrame()
            has_pb = ("pb" in market_df.columns) and pd.to_numeric(market_df["pb"], errors="coerce").notna().any()
            available = get_available_subfactors("hk", has_pb=has_pb, has_momentum=False)
            market_df["valuation_method"] = "V2_MULTIFACTOR_hk"

        # 构建子因子并按可得集合合成 raw_score（SCALE 后）
        market_df = market_df.reset_index(drop=True)
        subs = build_subfactors(market_df)
        raw = combine_raw_score(subs, available)
        market_df["raw_score"] = raw.values
        market_df = market_df.dropna(subset=["raw_score"])
        if market_df.empty:
            return pd.DataFrame()
        if market == "hk":
            # 港股信号取反：回测显示港股口径 IC 为负（EP±BP 降级下方向与前瞻收益相反），
            # 故对 raw_score 取负，使 factor_value/score/signal 一并翻转为「大=买」正向。
            market_df["raw_score"] = -market_df["raw_score"]
        market_df["valuation_metric"] = market_df["raw_score"]
        if "pb" not in market_df.columns:
            market_df["pb"] = np.nan
        # industry_pe_avg 保留列语义（行业平均 PE），供输出参考
        if "pe" in market_df.columns:
            market_df["industry_pe_avg"] = (
                market_df.groupby("industry")["pe"].transform("mean")
            )
        else:
            market_df["industry_pe_avg"] = np.nan

    # raw_score 承载于 stock_z_score；factor_value = SCALE(raw_score)（单调变换，不改排名）
    market_df["stock_z_score"] = market_df["raw_score"]
    market_df["market_z_score"] = market_z_score
    market_df["factor_value"] = op_scale(market_df["raw_score"])
    
    market_df["score"] = market_df["factor_value"].rank(pct=True) * 100
    market_df["rank"] = market_df["factor_value"].rank(ascending=False)
    
    score_threshold = 80
    sell_threshold = 20
    
    conditions = [
        (market_df["score"] >= score_threshold),
        (market_df["score"] < sell_threshold),
    ]
    choices = ["buy", "sell"]
    market_df["signal"] = np.select(conditions, choices, default="hold")
    
    market_df["confidence"] = market_df["score"].apply(
        lambda x: min(100, max(0, (x - 50) * 2)) if x >= 50 else min(100, max(0, (50 - x) * 2))
    )
    
    return market_df


def resolve_trade_date(as_of_date: str, max_lookback_days: int = 10) -> str:
    """非交易日自动回退到最近有行情的交易日（防全市场报错）。"""
    dt = datetime.strptime(as_of_date, "%Y%m%d")
    for i in range(max_lookback_days + 1):
        d = (dt - timedelta(days=i)).strftime("%Y%m%d")
        try:
            probe = panda_data.get_hk_daily(symbol=None, start_date=d, end_date=d)
            if probe is not None and not probe.empty:
                if d != as_of_date:
                    print(f"[factor] ⚠️  {as_of_date} 无行情，自动回退至最近交易日 {d}")
                return d
        except Exception:
            continue
    return as_of_date


def calculate_factor(as_of_date: str, market: str = "all", write: bool = True):
    print(f"[factor] 获取市场情绪数据...")
    
    as_of_date = resolve_trade_date(as_of_date)
    # 指数估值窗口：45 天，避免结果集超过套餐配额（错误码 600003）
    index_start = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=45)).strftime("%Y%m%d")
    
    try:
        index_df = panda_data.get_index_indicator(start_date=index_start, end_date=as_of_date)
        print(f"[factor] 获取 A股指数估值: {as_of_date}")
        print(f"  指数估值数据: {len(index_df)} 条记录")
        if not index_df.empty:
            print(f"  列名: {index_df.columns.tolist()}")
    except Exception as e:
        print(f"[factor] ⚠️  获取指数估值失败: {e}")
        index_df = pd.DataFrame()
    
    pe_col = _find_column(index_df, ["pe_ttm", "pe_lyr"])
    if pe_col and not index_df.empty:
        pe_values = pd.to_numeric(index_df[pe_col].dropna(), errors="coerce").dropna().values
        if len(pe_values) >= 2:
            pe_mean = np.mean(pe_values)
            pe_std = np.std(pe_values)
            current_pe = pe_values[-1]
            market_z_score = (current_pe - pe_mean) / pe_std if pe_std > 0 else 0
        else:
            market_z_score = 0
    else:
        pb_col = _find_column(index_df, ["pb_ttm", "pb_lf"])
        if pb_col and not index_df.empty:
            pb_values = pd.to_numeric(index_df[pb_col].dropna(), errors="coerce").dropna().values
            if len(pb_values) >= 2:
                pb_mean = np.mean(pb_values)
                pb_std = np.std(pb_values)
                current_pb = pb_values[-1]
                market_z_score = (current_pb - pb_mean) / pb_std if pb_std > 0 else 0
            else:
                market_z_score = 0
        else:
            market_z_score = 0
    
    print(f"[factor] 市场PE Z-score: {market_z_score:.2f}")
    
    print(f"[factor] 获取行情数据...")
    
    all_daily = []
    
    if market in ("cn", "all"):
        try:
            industry_df_all = get_industry_data()
            
            excluded_names = {"银行", "保险", "证券", "多元金融", "信托"}
            if "industry" in industry_df_all.columns:
                industry_df_all = industry_df_all[~industry_df_all["industry"].astype(str).isin(excluded_names)]
            if "stock_name" in industry_df_all.columns:
                industry_df_all = industry_df_all[~industry_df_all["stock_name"].astype(str).str.contains("ST|退市|风险警示", na=False)]
            
            cn_symbols = industry_df_all["ts_code"].unique().tolist()
            print(f"[factor] 获取 A股数据: {as_of_date}")
            print(f"  A股股票池: {len(cn_symbols)} 只（抽样）")
            
            if cn_symbols:
                daily_cn = panda_data.get_stock_daily(
                    symbol=cn_symbols[:1000],
                    start_date=as_of_date,
                    end_date=as_of_date,
                    st=False
                )
                
                if daily_cn is not None and not daily_cn.empty:
                    print(f"  A股有效股票: {len(daily_cn)} 只")
                    all_daily.append(("cn", daily_cn, industry_df_all))
                else:
                    print(f"  ⚠️  A股数据为空")
        except Exception as e:
            print(f"[factor] ⚠️  获取A股数据失败: {e}")
    
    if market in ("hk", "all"):
        try:
            print(f"[factor] 获取港股数据: {as_of_date}")
            daily_hk = panda_data.get_hk_daily(
                symbol=None,
                start_date=as_of_date,
                end_date=as_of_date
            )
            
            if daily_hk is not None and not daily_hk.empty:
                print(f"  港股原始列名: {daily_hk.columns.tolist()}")
                print(f"  港股有效股票: {len(daily_hk)} 只")
                all_daily.append(("hk", daily_hk, None))
            else:
                print(f"  ⚠️  港股数据为空")
        except Exception as e:
            print(f"[factor] ⚠️  获取港股数据失败: {e}")
    
    if market in ("us", "all"):
        try:
            print(f"[factor] 获取美股数据: {as_of_date}")
            daily_us = panda_data.get_us_daily(
                symbol=None,
                start_date=as_of_date,
                end_date=as_of_date
            )
            
            if daily_us is not None and not daily_us.empty:
                print(f"  美股原始列名: {daily_us.columns.tolist()}")
                print(f"  美股有效股票: {len(daily_us)} 只")
                all_daily.append(("us", daily_us, None))
            else:
                print(f"  ⚠️  美股数据为空")
        except Exception as e:
            print(f"[factor] ⚠️  获取美股数据失败: {e}")
    
    total_count = sum(len(df[1]) for df in all_daily) if all_daily else 0
    print(f"[factor] 全市场有效股票: {total_count} 只")
    
    print(f"[factor] 获取财务和估值数据...")
    
    financial_df = {}
    for mkt, daily, _ in all_daily:
        sym_col = _find_column(daily, ["symbol", "ts_code", "stock_symbol"])
        if sym_col:
            ts_codes = daily[sym_col].unique().tolist()
            
            if mkt == "cn":
                print(f"[factor] 获取A股财务数据: {len(ts_codes)} 只")
                fin_ttm = get_financial_ttm(ts_codes, as_of_date)
                print(f"  财务数据: {len(fin_ttm)} 条记录（TTM累加）")
                
                if fin_ttm.empty:
                    print(f"  ⚠️  财务数据为空，尝试获取估值数据")
                    fin_val = get_valuation_data(ts_codes, "cn")
                    print(f"  估值数据: {len(fin_val)} 条记录")
                    financial_df["cn"] = fin_val
                else:
                    financial_df["cn"] = fin_ttm
            else:
                print(f"[factor] 获取{mkt}市场估值数据: {len(ts_codes)} 只")
                fin_val = get_valuation_data(ts_codes, mkt)
                print(f"  估值数据: {len(fin_val)} 条记录")
                if fin_val is None or fin_val.empty:
                    # 无 PE 估值权限（美股）→ 退化为 52 周低点价格代理
                    getter = panda_data.get_us_daily if mkt == "us" else panda_data.get_hk_daily
                    print(f"  ⚠️  {mkt}无PE估值，改用52周低点价格代理")
                    proxy = compute_price_proxy(getter, ts_codes, as_of_date)
                    print(f"  价格代理数据: {len(proxy)} 条记录")
                    financial_df[mkt] = proxy
                else:
                    financial_df[mkt] = fin_val
    
    print(f"[factor] 计算因子...")
    
    all_results = []
    produced_markets = set()
    
    for mkt, daily, industry in all_daily:
        fin_df = financial_df.get(mkt, pd.DataFrame()) if mkt in financial_df else None
        if fin_df is not None and fin_df.empty:
            fin_df = None
        
        ind_df = industry if industry is not None and not industry.empty else None
        
        result = calculate_factor_for_market(daily, fin_df, ind_df, market_z_score, mkt, as_of_date)
        
        if not result.empty:
            count = len(result)
            method = result["valuation_method"].value_counts().to_dict()
            print(f"[factor] 使用{method}填充{mkt}市场: {count}只")
            all_results.append(result)
            produced_markets.add(mkt)
    
    if not all_results:
        print("[factor] ❌ 所有市场因子计算结果为空")
        return pd.DataFrame()

    # 严格校验：请求的每个市场都必须产出结果，否则报错（不静默跳过）
    requested = {"cn", "hk", "us"} if market == "all" else {market}
    missing = requested - produced_markets
    if missing:
        raise RuntimeError(
            f"以下请求市场未产出任何因子结果: {sorted(missing)}。"
            f"请检查该市场在 {as_of_date} 是否有可用数据/估值权限。"
        )
    
    merged = pd.concat(all_results, ignore_index=True)
    
    valid_count = len(merged)
    invalid_count = total_count - valid_count
    print(f"[factor] 估值有效股票: {valid_count} 只（过滤无效值: {invalid_count}）")
    
    if "valuation_method" in merged.columns:
        method_dist = merged["valuation_method"].value_counts().to_dict()
        print(f"[factor] 估值方法分布: {method_dist}")
    
    market_dist = merged["market"].value_counts().to_dict()
    print(f"[factor] 市场分布: {market_dist}")
    
    for mkt in market_dist:
        mkt_df = merged[merged["market"] == mkt]
        z_mean = mkt_df["stock_z_score"].mean()
        print(f"[factor] {mkt}市场估值Z-score: 均值={z_mean:.2f}")
    
    overall_z_mean = merged["stock_z_score"].mean()
    print(f"[factor] 全市场stock_z_score: 均值={overall_z_mean:.2f}")
    
    print(f"[factor] 构建输出...")
    
    output = merged.copy()
    
    output["trade_date"] = as_of_date
    output["asset_type"] = "stock"
    output["factor_id"] = "templeton_global_value_v2"
    output["factor_name"] = "邓普顿全球价值多因子V2"
    # 数据版本号：YYYYMMDD_HHMMSS（与 SKILL.md 字段说明一致，标识本次生成批次）
    output["data_version"] = datetime.now().strftime("%Y%m%d_%H%M%S")
    output["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if "pct_from_low" not in output.columns:
        output["pct_from_low"] = np.nan
    if "valuation_metric" not in output.columns:
        output["valuation_metric"] = output["pe"]
    
    columns_order = [
        "trade_date", "asset_type", "ts_code", "market", "factor_id", "factor_name",
        "factor_value", "score", "rank", "signal", "confidence", "data_version",
        "update_time", "market_cap", "pe", "pb", "industry", "industry_pe_avg",
        "market_z_score", "stock_z_score", "close", "valuation_method",
        "pct_from_low", "valuation_metric"
    ]
    
    for col in columns_order:
        if col not in output.columns:
            output[col] = np.nan
    
    output = output[columns_order]
    
    if not write:
        # 多日模式：只返回本日截面，由调用方纵向拼接后统一落盘
        print(f"[factor] 计算完成（未落盘）: {as_of_date} → {len(output)} 行")
        return output

    output_path = Path(__file__).parent.parent / "生产产物" / "数据库.parquet"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    if output_path.exists():
        output_path.unlink()
    
    output.to_parquet(str(output_path), index=False)
    
    signal_dist = output["signal"].value_counts().to_dict()
    score_min = output["score"].min()
    score_max = output["score"].max()
    
    print(f"[factor] Parquet 已保存: {output_path}")
    print(f"  行数: {len(output)}, 列数: {len(output.columns)}")
    print(f"  signal 分布: {signal_dist}")
    print(f"  score 范围: [{score_min:.1f}, {score_max:.1f}]")
    print(f"  市场分布: {market_dist}")
    
    buy_count = signal_dist.get("buy", 0)
    hold_count = signal_dist.get("hold", 0)
    sell_count = signal_dist.get("sell", 0)
    print(f"[factor] 筛选完成: buy={buy_count}, hold={hold_count}, sell={sell_count}")
    
    return output


def main():
    parser = argparse.ArgumentParser(description="Templeton 逆向全球价值因子计算")
    parser.add_argument("--as-of-date", type=str, default=datetime.now().strftime("%Y%m%d"),
                        help="基准日期（格式：YYYYMMDD）；支持逗号分隔多个交易日，如 20260805,20260806,20260807")
    parser.add_argument("--market", type=str, default="all",
                        choices=["cn", "hk", "us", "all"],
                        help="市场类型")
    parser.add_argument("--username", type=str, default=None, help="PandaAI 用户名")
    parser.add_argument("--password", type=str, default=None, help="PandaAI 密码")
    args = parser.parse_args()

    dates = [d.strip() for d in args.as_of_date.split(",") if d.strip()]
    output_path = Path(__file__).parent.parent / "生产产物" / "数据库.parquet"

    print("="*60)
    print("Templeton 逆向全球价值因子计算（官方SDK版）")
    print(f"  as_of_date: {dates}")
    print(f"  market:     {args.market}")
    print(f"  output:     {output_path}")
    print("="*60)
    
    print("[factor] 正在连接 PandaAI...")
    
    try:
        _init_panda_token(args.username, args.password)
        print("[factor] ✅ 已连接")
    except RuntimeError as e:
        print(f"[factor] ❌ 连接失败: {e}")
        sys.exit(1)
    
    try:
        if len(dates) == 1:
            # 单日：沿用原路径直接落盘
            calculate_factor(dates[0], args.market)
        else:
            # 多日：逐日计算（不落盘）后纵向拼接，按 PK 去重，统一写入一份产物
            frames = []
            for d in dates:
                print(f"\n{'='*60}\n[factor] === 计算交易日 {d} ===\n{'='*60}")
                day_df = calculate_factor(d, args.market, write=False)
                if day_df is not None and not day_df.empty:
                    frames.append(day_df)
            if not frames:
                print("[factor] ❌ 多日计算结果全部为空")
                sys.exit(1)

            combined = pd.concat(frames, ignore_index=True)
            before = len(combined)
            # 主键 (trade_date, ts_code) 唯一
            combined = combined.drop_duplicates(subset=["trade_date", "ts_code"], keep="last")
            dropped = before - len(combined)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path.exists():
                output_path.unlink()
            combined.to_parquet(str(output_path), index=False)

            print(f"\n[factor] ✅ 多日产物已保存: {output_path}")
            print(f"  行数: {len(combined)}（去重 {dropped} 行），列数: {len(combined.columns)}")
            print(f"  交易日: {sorted(combined['trade_date'].unique().tolist())}")
            print(f"  每日市场分布:")
            for d in sorted(combined["trade_date"].unique().tolist()):
                sub = combined[combined["trade_date"] == d]
                print(f"    {d}: 有效={len(sub)} | {sub['market'].value_counts().to_dict()} | {sub['signal'].value_counts().to_dict()}")
    except Exception as e:
        print(f"[factor] ❌ 因子计算失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
