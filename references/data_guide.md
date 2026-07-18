# PandaAI Data 接口使用指南

## 认证

```python
import panda_data

token = panda_data.init_token(username, password, base_url="http://pandadata.pandaaiquant.com")
```

## 接口说明

### 1. get_index_indicator（A股指数估值）

获取A股指数的估值指标，用于衡量市场整体情绪。

```python
df = panda_data.get_index_indicator(
    index_code=None,      # None 表示全市场指数
    start_date="20250630",
    end_date="20250630",
)
```

返回字段（需实际探测确认）：
- index_code: 指数代码
- date: 日期
- pe: 市盈率
- pb: 市净率
- ps: 市销率
- dividend_yield: 股息率

### 2. get_hk_daily（港股日线）

获取港股日线行情数据。

```python
df = panda_data.get_hk_daily(
    symbol=None,          # None 表示全市场
    start_date="20250601",
    end_date="20250630",
)
```

返回字段（需实际探测确认）：
- symbol: 股票代码
- date: 日期
- open: 开盘价
- high: 最高价
- low: 最低价
- close: 收盘价
- volume: 成交量
- amount: 成交额

### 3. get_us_daily（美股日线）

获取美股日线行情数据。

```python
df = panda_data.get_us_daily(
    symbol=None,          # None 表示全市场
    start_date="20250601",
    end_date="20250630",
)
```

返回字段（需实际探测确认）：
- symbol: 股票代码
- date: 日期
- open: 开盘价
- high: 最高价
- low: 最低价
- close: 收盘价
- volume: 成交量

### 4. get_stock_pv_indicator / pv_metric（个股估值指标）

获取个股的估值指标。

```python
df = panda_data.get_stock_pv_indicator(
    symbol=["000001.SZ", "600000.SH"],
    date=None,            # None 表示最新
    market="cn",          # cn/hk/us
)

# 或
df = panda_data.pv_metric(
    symbol=["000001.SZ"],
    date=None,
    market="cn",
)
```

返回字段（需实际探测确认）：
- symbol: 股票代码
- pe_ttm: 动态市盈率
- pb: 市净率
- ps: 市销率
- dividend_yield: 股息率

## 字段名探测

代码中使用 `_find_column()` 自动探测字段名，支持多种候选名：

```python
def _find_column(df, candidates):
    for col in candidates:
        if col in df.columns:
            return col
    return None
```

## 认证方式

支持四种认证方式：

1. 命令行参数：`--username` / `--password`
2. 环境变量：`PANDA_USERNAME` / `PANDA_PASSWORD`
3. .env 文件
4. 交互式输入

## 注意事项

1. 所有接口直接返回 DataFrame，无需 pyarrow 解析
2. 字段名可能因 SDK 版本不同有差异，使用 `_find_column()` 兼容
3. 港股和美股市场代码格式可能不同，需注意统一
4. 估值指标可能存在缺失值，需做好异常处理
