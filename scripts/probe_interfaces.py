#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Templeton 逆向全球价值因子 - 接口字段探测脚本

探测以下接口的真实字段名：
  - get_index_indicator: A股指数估值
  - get_hk_daily: 港股日线
  - get_us_daily: 美股日线
  - get_stock_pv_indicator/pv_metric: 估值指标
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

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


def _get_panda_token(username=None, password=None, base_url=None, interactive=True):
    _load_env_file()
    if not username:
        username = os.environ.get("PANDA_USERNAME", "")
    if not password:
        password = os.environ.get("PANDA_PASSWORD", "")
    if not base_url:
        base_url = os.environ.get("PANDA_BASE_URL", "http://pandadata.pandaaiquant.com")

    if interactive and not username:
        username = input("请输入 PandaAI 用户名: ").strip()
    if interactive and not password:
        password = input("请输入 PandaAI 密码: ").strip()

    if not username or not password:
        raise RuntimeError("缺少认证信息")
    return panda_data.init_token(username, password, base_url)


def probe_get_index_indicator():
    """探测 get_index_indicator 接口"""
    print("\n=== get_index_indicator ===")
    try:
        as_of_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

        df = panda_data.get_index_indicator(
            start_date=start_date,
            end_date=as_of_date,
        )
        if df is None or df.empty:
            print("  ❌ 返回空数据")
            return
        print(f"  行数: {len(df)}")
        print(f"  列名: {df.columns.tolist()}")
        print(f"  前2行:\n{df.head(2).to_string()}")
    except Exception as e:
        print(f"  ❌ 调用失败: {e}")


def probe_get_hk_daily():
    """探测 get_hk_daily 接口"""
    print("\n=== get_hk_daily ===")
    try:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
        df = panda_data.get_hk_daily(
            symbol=None,
            start_date=start_date,
            end_date=end_date,
        )
        if df is None or df.empty:
            print("  ❌ 返回空数据")
            return
        print(f"  行数: {len(df)}")
        print(f"  列名: {df.columns.tolist()}")
        print(f"  前2行:\n{df.head(2).to_string()}")
    except Exception as e:
        print(f"  ❌ 调用失败: {e}")


def probe_get_us_daily():
    """探测 get_us_daily 接口"""
    print("\n=== get_us_daily ===")
    try:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
        df = panda_data.get_us_daily(
            symbol=None,
            start_date=start_date,
            end_date=end_date,
        )
        if df is None or df.empty:
            print("  ❌ 返回空数据")
            return
        print(f"  行数: {len(df)}")
        print(f"  列名: {df.columns.tolist()}")
        print(f"  前2行:\n{df.head(2).to_string()}")
    except Exception as e:
        print(f"  ❌ 调用失败: {e}")


def probe_get_stock_pv_indicator():
    """探测 get_stock_pv_indicator / pv_metric 接口"""
    print("\n=== get_stock_pv_indicator ===")
    try:
        df = panda_data.get_stock_pv_indicator(
            symbol=["0001.HK"],
        )
        if df is None or df.empty:
            print("  ⚠️  get_stock_pv_indicator 返回空，尝试 pv_metric")
            df = panda_data.pv_metric(
                symbol=["0001.HK"],
            )
        if df is None or df.empty:
            print("  ❌ 两个接口都返回空")
            return
        print(f"  行数: {len(df)}")
        print(f"  列名: {df.columns.tolist()}")
        print(f"  前2行:\n{df.head(2).to_string()}")
    except Exception as e:
        print(f"  ❌ 调用失败: {e}")


def probe_get_fina_reports_for_valuation():
    """探测 get_fina_reports 获取A股估值相关字段"""
    print("\n=== get_fina_reports (估值字段) ===")
    try:
        df = panda_data.get_fina_reports(
            symbol=["000001.SZ"],
            start_quarter="2026q1",
            end_quarter="2026q1",
            is_latest=True,
        )
        if df is None or df.empty:
            print("  ❌ 返回空数据")
            return
        print(f"  行数: {len(df)}")
        print(f"  列名: {df.columns.tolist()}")
        pe_cols = [c for c in df.columns if "pe" in c.lower()]
        pb_cols = [c for c in df.columns if "pb" in c.lower()]
        ps_cols = [c for c in df.columns if "ps" in c.lower()]
        print(f"  PE相关列: {pe_cols}")
        print(f"  PB相关列: {pb_cols}")
        print(f"  PS相关列: {ps_cols}")
    except Exception as e:
        print(f"  ❌ 调用失败: {e}")


def main():
    print("=" * 60)
    print("Templeton 逆向全球价值因子 - 接口字段探测")
    print("=" * 60)

    print("[probe] 正在连接 PandaAI...")
    _get_panda_token()
    print("[probe] ✅ 已连接")

    probe_get_index_indicator()
    probe_get_hk_daily()
    probe_get_us_daily()
    probe_get_stock_pv_indicator()
    probe_get_fina_reports_for_valuation()

    print("\n" + "=" * 60)
    print("探测完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
