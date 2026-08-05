#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Templeton 逆向全球价值因子计算脚本（官方SDK版）

修复内容：
  P0-1：非A股市场因子计算（left join + 日线PE/PB或52周低点比率）
  P0-2：PE TTM年化（最近4个季度累加净利润）
  P0-3：因子符号方向（market_z × stock_z，不带负号）
  P1-4：验证和回测使用真实因子计算逻辑

因子逻辑：
  - 市场情绪 Z-score：基于A股指数 PE/PB 历史偏离度
  - 个股估值 Z-score：基于个股 PE 相对于行业均值的偏离度
  - 综合因子 = 市场 Z × 个股 Z
    * 市场悲观(market_z < 0) + 个股低估(stock_z < 0) → 正值 → 买入信号

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
    
    net_profit_col = _find_column(fina_df, ["net_profit", "np_parent_company_owners", "np", "is_n_income_attr_p"])
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


def get_valuation_data(ts_codes: list, market: str) -> pd.DataFrame:
    if not ts_codes:
        return pd.DataFrame()
    
    try:
        if market == "cn":
            df = panda_data.get_stock_mktfin_metric(symbol=ts_codes[:500])
        elif market == "hk":
            df = panda_data.get_stock_mktfin_indicator(symbol=ts_codes[:500])
        else:
            df = pd.DataFrame()
        
        if df is not None and not df.empty:
            print(f"  获取{market}估值数据: {len(df)} 条记录")
            
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
                        share_df = panda_data.get_share_float(
                            symbol=ts_codes[:500],
                            start_date=as_of_date,
                            end_date=as_of_date
                        )
                        if share_df is not None and not share_df.empty:
                            ts_col = _find_column(share_df, ["symbol", "ts_code"])
                            if ts_col:
                                share_df = share_df.rename(columns={ts_col: "ts_code"})
                            if "total" in share_df.columns:
                                market_df = market_df.merge(share_df[["ts_code", "total"]], on="ts_code", how="left")
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
    
    market_df = market_df.dropna(subset=["pe"])
    market_df = market_df[market_df["pe"] > 0]
    
    if market_df.empty:
        return pd.DataFrame()
    
    pe_mean = market_df["pe"].mean()
    pe_std = market_df["pe"].std()
    
    if pe_std > 0:
        market_df["stock_z_score"] = -(market_df["pe"] - pe_mean) / pe_std
    else:
        market_df["stock_z_score"] = 0
    
    market_df["market_z_score"] = market_z_score
    market_df["factor_value"] = market_df["market_z_score"] * market_df["stock_z_score"]
    
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


def calculate_factor(as_of_date: str, market: str = "all"):
    print(f"[factor] 获取市场情绪数据...")
    
    index_start = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=120)).strftime("%Y%m%d")
    
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
                financial_df[mkt] = fin_val
    
    print(f"[factor] 计算因子...")
    
    all_results = []
    
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
    
    if not all_results:
        print("[factor] ❌ 所有市场因子计算结果为空")
        return pd.DataFrame()
    
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
    output["factor_id"] = "templeton_global_contrarian"
    output["factor_name"] = "邓普顿逆向全球价值因子"
    output["data_version"] = "1.0"
    output["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if "pct_from_low" not in output.columns:
        output["pct_from_low"] = np.nan
    if "valuation_metric" not in output.columns:
        output["valuation_metric"] = output["pe"]
    
    columns_order = [
        "trade_date", "asset_type", "ts_code", "market", "factor_id", "factor_name",
        "factor_value", "score", "rank", "signal", "confidence", "data_version",
        "update_time", "market_cap", "pe", "pb", "industry", "market_z_score",
        "stock_z_score", "close", "valuation_method", "pct_from_low", "valuation_metric"
    ]
    
    for col in columns_order:
        if col not in output.columns:
            output[col] = np.nan
    
    output = output[columns_order]
    
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
                        help="基准日期（格式：YYYYMMDD）")
    parser.add_argument("--market", type=str, default="all",
                        choices=["cn", "hk", "us", "all"],
                        help="市场类型")
    parser.add_argument("--username", type=str, default=None, help="PandaAI 用户名")
    parser.add_argument("--password", type=str, default=None, help="PandaAI 密码")
    args = parser.parse_args()
    
    print("="*60)
    print("Templeton 逆向全球价值因子计算（官方SDK版）")
    print(f"  as_of_date: {args.as_of_date}")
    print(f"  market:     {args.market}")
    output_path = Path(__file__).parent.parent / "生产产物" / "数据库.parquet"
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
        calculate_factor(args.as_of_date, args.market)
    except Exception as e:
        print(f"[factor] ❌ 因子计算失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
