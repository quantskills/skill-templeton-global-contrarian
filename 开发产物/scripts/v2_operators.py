#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 全球价值多因子 - 共享算子与子因子构建模块

将 V2 平台算子（RANK/ZSCORE/SCALE/SIGN/LOG/ABS/IF/DELAY）翻译为 pandas 实现，
并提供 7 子因子构建（build_subfactors）与降级权重合成（combine_raw_score），
供 factor.py / backtest.py / validate.py 三脚本复用。

因子逻辑（V2）：
  7 子因子：EP/BP/SP/股息/ROE/杠杆/动量，各自截面 RANK 后按固定权重加总，
  经市值中性化（每个子因子减 0.1×市值 zscore）与末端 SCALE 标准化得到 raw_score。
  方向：raw_score 越大 = 越优质便宜 = 越该买。

三市降级（数据权限所致）：
  - A股：完整 7 子因子（get_fina_reports 320 字段自算）
  - 港股：仅 EP(+BP 若可得)（get_stock_mktfin_indicator 无 ps/股息/roe/杠杆/营收）
  - 美股：无估值权限，退化为 SCALE(-pct_from_low) 价格代理，不走子因子链
  - 动量：窗口不足 252 交易日时权重置 0，剩余权重重归一化
  缺失子因子权重置 0 后，对剩余权重按绝对值和重新归一化（各市场权重绝对值和恒为 1）。
"""

import numpy as np
import pandas as pd


# ==================== 基础算子（V2 平台算子 → pandas） ====================

def op_rank(s: pd.Series) -> pd.Series:
    """RANK：截面百分位排名 ∈ [0, 1]。"""
    return s.rank(pct=True)


def op_zscore(s: pd.Series) -> pd.Series:
    """ZSCORE：(x - mean) / std（总体标准差 ddof=0）。std=0 时返回 0。"""
    std = s.std(ddof=0)
    if std is None or not np.isfinite(std) or std == 0:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def op_scale(s: pd.Series) -> pd.Series:
    """SCALE：末端归一。用 ZSCORE 近似（单调变换，不改变截面百分位排名）。"""
    return op_zscore(s)


def op_sym_log1p(s: pd.Series) -> pd.Series:
    """对称 log1p：SIGN(x) * LOG(ABS(x) + 1)。"""
    return np.sign(s) * np.log(np.abs(s) + 1.0)


def op_clip(s: pd.Series, lo: float, hi: float) -> pd.Series:
    """限幅：等价于嵌套 IF(x>hi,hi,IF(x<lo,lo,x))。"""
    return s.clip(lower=lo, upper=hi)


# ==================== 子因子构建 ====================

# 7 子因子基准权重（含符号）；value_combo + quality_combo + momentum_combo
BASE_WEIGHTS = {
    "sub_ep": +0.25,   # EP  = 1/PE   价值
    "sub_bp": +0.20,   # BP  = 1/PB   价值
    "sub_sp": +0.10,   # SP  = 1/PS   价值
    "sub_div": +0.30,  # 股息率        价值
    "sub_roe": +0.15,  # ROE          质量（正）
    "sub_lev": -0.10,  # 杠杆          质量（负，越低越好）
    "sub_mom": +0.10,  # 12-1 动量     动量
}

SUB_COLS = list(BASE_WEIGHTS.keys())


def build_subfactors(df: pd.DataFrame) -> pd.DataFrame:
    """在单截面 df 上构建 7 个截面 RANK 子因子。

    输入列（缺失的列按缺失处理，对应 sub_ 返回全 NaN）：
      pe, pb, ps, div_yield, roe, leverage, market_cap, mom_12_1
    输出：sub_ep, sub_bp, sub_sp, sub_div, sub_roe, sub_lev, sub_mom
    NaN 处理：每个 sub_ 列 fillna(0.5)（中性百分位）；整列缺失则该 sub_ 全 NaN。
    """
    out = pd.DataFrame(index=df.index)
    eps = 1e-6

    # 市值中性调整项（缺失市值则不中性化）
    if "market_cap" in df.columns and df["market_cap"].notna().any():
        log_mcap = np.log(pd.to_numeric(df["market_cap"], errors="coerce").clip(lower=0) + 1.0 + eps)
        mcap_adj = op_zscore(log_mcap.fillna(log_mcap.median()))
    else:
        mcap_adj = pd.Series(0.0, index=df.index)

    def _rank_neutral(x_log):
        # 子因子对数值先做市值中性，再截面 RANK，最后 fillna(0.5)
        neutral = x_log - 0.1 * mcap_adj
        return op_rank(neutral).fillna(0.5)

    # EP / BP / SP：估值倍数限幅 → 取倒数 → LOG(x+1)
    if "pe" in df.columns:
        pe_safe = op_clip(pd.to_numeric(df["pe"], errors="coerce"), 0.0, 200.0)
        ep = 1.0 / (pe_safe + 1.0 + eps)
        out["sub_ep"] = _rank_neutral(np.log(ep + 1.0 + eps))
    else:
        out["sub_ep"] = np.nan

    if "pb" in df.columns and pd.to_numeric(df["pb"], errors="coerce").notna().any():
        pb_safe = op_clip(pd.to_numeric(df["pb"], errors="coerce"), 0.0, 20.0)
        bp = 1.0 / (pb_safe + 1.0 + eps)
        out["sub_bp"] = _rank_neutral(np.log(bp + 1.0 + eps))
    else:
        out["sub_bp"] = np.nan

    if "ps" in df.columns and pd.to_numeric(df["ps"], errors="coerce").notna().any():
        ps_safe = op_clip(pd.to_numeric(df["ps"], errors="coerce"), 0.0, 50.0)
        sp = 1.0 / (ps_safe + 1.0 + eps)
        out["sub_sp"] = _rank_neutral(np.log(sp + 1.0 + eps))
    else:
        out["sub_sp"] = np.nan

    # 股息率 / ROE：对称 log1p
    if "div_yield" in df.columns and pd.to_numeric(df["div_yield"], errors="coerce").notna().any():
        out["sub_div"] = _rank_neutral(op_sym_log1p(pd.to_numeric(df["div_yield"], errors="coerce")))
    else:
        out["sub_div"] = np.nan

    if "roe" in df.columns and pd.to_numeric(df["roe"], errors="coerce").notna().any():
        out["sub_roe"] = _rank_neutral(op_sym_log1p(pd.to_numeric(df["roe"], errors="coerce")))
    else:
        out["sub_roe"] = np.nan

    # 杠杆：限幅 + LOG(x+1)
    if "leverage" in df.columns and pd.to_numeric(df["leverage"], errors="coerce").notna().any():
        lev_safe = op_clip(pd.to_numeric(df["leverage"], errors="coerce"), 0.0, 10.0)
        out["sub_lev"] = _rank_neutral(np.log(lev_safe + 1.0 + eps))
    else:
        out["sub_lev"] = np.nan

    # 动量：对称 log1p
    if "mom_12_1" in df.columns and pd.to_numeric(df["mom_12_1"], errors="coerce").notna().any():
        out["sub_mom"] = _rank_neutral(op_sym_log1p(pd.to_numeric(df["mom_12_1"], errors="coerce")))
    else:
        out["sub_mom"] = np.nan

    return out


def combine_raw_score(subs_df: pd.DataFrame, available: list) -> pd.Series:
    """按可得子因子集合 available 合成 raw_score 并 SCALE。

    权重归一化：对 available 子集，w_norm[k] = BASE_WEIGHTS[k] / Σ|BASE_WEIGHTS[j]|,
    保留 lev 负号；各市场权重绝对值和恒为 1，raw_score 量级可比。
    返回 SCALE(raw_score)（截面内 ZSCORE）。
    """
    avail = [k for k in available if k in subs_df.columns and subs_df[k].notna().any()]
    if not avail:
        return pd.Series(np.nan, index=subs_df.index)

    norm = sum(abs(BASE_WEIGHTS[k]) for k in avail)
    if norm == 0:
        return pd.Series(np.nan, index=subs_df.index)

    raw = pd.Series(0.0, index=subs_df.index)
    for k in avail:
        w = BASE_WEIGHTS[k] / norm
        raw = raw + w * subs_df[k].fillna(0.5)

    return op_scale(raw)


def get_available_subfactors(market: str, has_pb: bool = False, has_momentum: bool = True) -> list:
    """返回某市场可用子因子清单（用于降级权重归一化）。
    market: 'cn' / 'hk' / 'us'
    """
    if market == "cn":
        cols = ["sub_ep", "sub_bp", "sub_sp", "sub_div", "sub_roe", "sub_lev"]
        if has_momentum:
            cols.append("sub_mom")
        return cols
    if market == "hk":
        return ["sub_ep", "sub_bp"] if has_pb else ["sub_ep"]
    # us: 不走子因子链
    return []
