#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟 pandadata API 服务器（用于测试）
"""

import json
import os
import random
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

app = Flask(__name__)

# 本地测试用的假账号。绝不要在这里写真实凭证——本仓库是公开的。
# 需要换成别的值时用环境变量覆盖，不要改动源码：
#   MOCK_PANDA_USERNAME=... MOCK_PANDA_PASSWORD=... python scripts/mock_panda_server.py
users = {
    os.environ.get("MOCK_PANDA_USERNAME", "mock_user"):
        os.environ.get("MOCK_PANDA_PASSWORD", "mock_password"),
}

tokens = {}


def generate_token():
    return "mock_token_" + str(random.randint(100000, 999999))


def verify_token(token):
    return token in tokens.values()


@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if username in users and users[username] == password:
        token = generate_token()
        tokens[username] = token
        return jsonify({"code": 0, "message": "success", "token": token})
    return jsonify({"code": -1, "message": "invalid credentials"}), 401


@app.route('/api/index/indicator', methods=['GET'])
def get_index_indicator():
    if not verify_token(request.headers.get('Authorization', '').replace('Bearer ', '')):
        return jsonify({"code": -1, "message": "invalid token"}), 401
    
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    if not start_date or not end_date:
        return jsonify({"code": -1, "message": "missing params"}), 400
    
    start_dt = datetime.strptime(start_date, "%Y%m%d")
    end_dt = datetime.strptime(end_date, "%Y%m%d")
    
    data = []
    current_dt = start_dt
    pe_base = 12.0
    while current_dt <= end_dt:
        pe_ttm = pe_base + random.uniform(-2, 3)
        pb_ttm = 1.5 + random.uniform(-0.3, 0.5)
        data.append({
            "date": current_dt.strftime("%Y%m%d"),
            "pe_ttm": round(pe_ttm, 2),
            "pb_ttm": round(pb_ttm, 2),
            "pe_lyr": round(pe_ttm * 1.05, 2),
            "pb_lf": round(pb_ttm * 1.1, 2)
        })
        current_dt += timedelta(days=1)
    
    return jsonify({"code": 0, "message": "success", "data": data})


@app.route('/api/stock/daily', methods=['GET'])
def get_stock_daily():
    if not verify_token(request.headers.get('Authorization', '').replace('Bearer ', '')):
        return jsonify({"code": -1, "message": "invalid token"}), 401
    
    symbols = request.args.getlist('symbol')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    if symbols and len(symbols) > 0 and symbols[0]:
        symbol_list = []
        for s in symbols:
            symbol_list.extend(s.split(','))
    else:
        symbol_list = [f"{i:06d}.SH" for i in range(1, 301)] + [f"{i:06d}.SZ" for i in range(1, 301)]
    
    data = []
    for symbol in symbol_list[:500]:
        start_dt = datetime.strptime(start_date, "%Y%m%d")
        end_dt = datetime.strptime(end_date, "%Y%m%d")
        current_dt = start_dt
        
        base_close = random.uniform(5, 100)
        while current_dt <= end_dt:
            base_close = base_close * (1 + random.uniform(-0.05, 0.05))
            data.append({
                "symbol": symbol,
                "date": current_dt.strftime("%Y%m%d"),
                "close": round(base_close, 2),
                "open": round(base_close * (1 + random.uniform(-0.02, 0.02)), 2),
                "high": round(base_close * (1 + random.uniform(0, 0.03)), 2),
                "low": round(base_close * (1 + random.uniform(-0.03, 0)), 2),
                "volume": random.randint(1000000, 100000000)
            })
            current_dt += timedelta(days=1)
    
    return jsonify({"code": 0, "message": "success", "data": data})


@app.route('/api/hk/daily', methods=['GET'])
def get_hk_daily():
    if not verify_token(request.headers.get('Authorization', '').replace('Bearer ', '')):
        return jsonify({"code": -1, "message": "invalid token"}), 401
    
    symbols = request.args.get('symbol')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    if not symbols:
        symbol_list = [f"{i:04d}.HK" for i in range(1, 501)]
    else:
        symbol_list = symbols.split(',') if ',' in symbols else [symbols]
    
    data = []
    for symbol in symbol_list[:500]:
        start_dt = datetime.strptime(start_date, "%Y%m%d")
        end_dt = datetime.strptime(end_date, "%Y%m%d")
        current_dt = start_dt
        
        base_close = random.uniform(2, 200)
        while current_dt <= end_dt:
            base_close = base_close * (1 + random.uniform(-0.05, 0.05))
            data.append({
                "symbol": symbol,
                "date": current_dt.strftime("%Y%m%d"),
                "close": round(base_close, 2),
                "open": round(base_close * (1 + random.uniform(-0.02, 0.02)), 2),
                "high": round(base_close * (1 + random.uniform(0, 0.03)), 2),
                "low": round(base_close * (1 + random.uniform(-0.03, 0)), 2),
                "pe": round(random.uniform(5, 50), 2),
                "pb": round(random.uniform(0.5, 3), 2)
            })
            current_dt += timedelta(days=1)
    
    return jsonify({"code": 0, "message": "success", "data": data})


@app.route('/api/us/daily', methods=['GET'])
def get_us_daily():
    if not verify_token(request.headers.get('Authorization', '').replace('Bearer ', '')):
        return jsonify({"code": -1, "message": "invalid token"}), 401
    
    symbols = request.args.get('symbol')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    if not symbols:
        symbol_list = [f"US{i:03d}" for i in range(1, 501)]
    else:
        symbol_list = symbols.split(',') if ',' in symbols else [symbols]
    
    data = []
    for symbol in symbol_list[:500]:
        start_dt = datetime.strptime(start_date, "%Y%m%d")
        end_dt = datetime.strptime(end_date, "%Y%m%d")
        current_dt = start_dt
        
        base_close = random.uniform(10, 500)
        while current_dt <= end_dt:
            base_close = base_close * (1 + random.uniform(-0.05, 0.05))
            data.append({
                "symbol": symbol,
                "date": current_dt.strftime("%Y%m%d"),
                "close": round(base_close, 2),
                "open": round(base_close * (1 + random.uniform(-0.02, 0.02)), 2),
                "high": round(base_close * (1 + random.uniform(0, 0.03)), 2),
                "low": round(base_close * (1 + random.uniform(-0.03, 0)), 2),
                "pe": round(random.uniform(10, 80), 2),
                "pb": round(random.uniform(1, 5), 2)
            })
            current_dt += timedelta(days=1)
    
    return jsonify({"code": 0, "message": "success", "data": data})


@app.route('/api/stock/fina_reports', methods=['GET'])
def get_fina_reports():
    if not verify_token(request.headers.get('Authorization', '').replace('Bearer ', '')):
        return jsonify({"code": -1, "message": "invalid token"}), 401
    
    start_quarter = request.args.get('start_quarter')
    end_quarter = request.args.get('end_quarter')
    
    data = []
    for i in range(1, 601):
        symbol = f"{i:06d}.SH" if i <= 300 else f"{i-300:06d}.SZ"
        data.append({
            "symbol": symbol,
            "report_quarter": start_quarter,
            "bs_cap_stk": round(random.uniform(1000000, 100000000), 0),
            "bs_total_hldr_eqy_inc_min_int": round(random.uniform(500000, 50000000), 0),
            "is_n_income_attr_p": round(random.uniform(-10000, 5000000), 0)
        })
    
    return jsonify({"code": 0, "message": "success", "data": data})


@app.route('/api/stock/industry_constituents', methods=['GET'])
def get_industry_constituents():
    if not verify_token(request.headers.get('Authorization', '').replace('Bearer ', '')):
        return jsonify({"code": -1, "message": "invalid token"}), 401
    
    level = request.args.get('level', 'L1')
    
    industries = ['信息技术', '消费', '医疗健康', '工业', '材料', '可选消费', '公用事业', '能源', '房地产']
    excluded = {'银行', '保险', '证券', '多元金融', '信托'}
    
    data = []
    for i in range(1, 601):
        symbol = f"{i:06d}.SH" if i <= 300 else f"{i-300:06d}.SZ"
        name = f"股票{i}"
        industry = random.choice([i for i in industries if i not in excluded])
        
        if 'ST' not in name and '退市' not in name:
            data.append({
                "stock_symbol": symbol,
                "stock_name": name,
                "l1_name": industry
            })
    
    return jsonify({"code": 0, "message": "success", "data": data})


if __name__ == '__main__':
    print("🚀 启动模拟 pandadata API 服务器...")
    print("监听地址: http://127.0.0.1:5000")
    print("可用接口:")
    print("  POST /api/login")
    print("  GET /api/index/indicator")
    print("  GET /api/stock/daily")
    print("  GET /api/hk/daily")
    print("  GET /api/us/daily")
    print("  GET /api/stock/fina_reports")
    print("  GET /api/stock/industry_constituents")
    app.run(host='127.0.0.1', port=5000, debug=False)
