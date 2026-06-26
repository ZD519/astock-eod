
# -*- coding: utf-8 -*-
"""
拉取 A 股指数 + 指定标的的当日(最近交易日)EOD收盘与涨跌幅。
跑在 GitHub Actions 的 runner 上(满血外网),不在受限环境里跑。
数据源 AKShare(免费/无token)。取不到的标的写 error,绝不编造。
跑在 GitHub Actions 的 runner 上。

指数用新浪接口(stock_zh_index_daily),不用东方财富(_em),避免GitHub IP被封。
ETF/股票用东方财富接口,如失败会记录error但不编造数据。
数据源: Yahoo Finance (yfinance)。
原因: GitHub Actions 跑在 Azure 云 IP 上,国内财经站(东财/新浪/腾讯)封锁境外云IP;
      Yahoo 服务器全球可达、不封云IP、免费无token,且支持A股(.SS沪 / .SZ深)。
取不到的标的写 error,绝不编造。
"""
import json, time, datetime, sys
import akshare as ak
import yfinance as yf

# --- 你的监测清单 ---
INDICES = {  # 名称: (akshare代码, 交易所前缀)
    "上证":   ("000001", "sh"),
    "深成指": ("399001", "sz"),
    "创业板": ("399006", "sz"),
    "科创50": ("000688", "sh"),
    "沪深300":("000300", "sh"),
# --- 你的监测清单: 名称 -> Yahoo代码(.SS=上交所, .SZ=深交所) ---
TICKERS = {
    "上证":            "000001.SS",
    "深成指":          "399001.SZ",
    "创业板":          "399006.SZ",
    "科创50":          "000688.SS",
    "沪深300":         "000300.SS",
    "红利低波 512890": "512890.SS",
    "电力ETF 159611":  "159611.SZ",
    "半导体ETF 512480":"512480.SS",
    "通信ETF 515880":  "515880.SS",
    "科创50ETF 588000":"588000.SS",
    "国航 601111":     "601111.SS",
}
ETFS = {  # 名称: 6位代码(交易所前缀+代码)
    "红利低波 512890": ("512890", "sh"),
    "电力ETF 159611":  ("159611", "sz"),
    "半导体ETF 512480":("512480", "sh"),
    "通信ETF 515880":  ("515880", "sh"),
    "科创50ETF 588000":("588000", "sh"),
}
STOCKS = {"国航 601111": ("601111", "sh")}

START = (datetime.date.today() - datetime.timedelta(days=20)).strftime("%Y%m%d")
END   = datetime.date.today().strftime("%Y%m%d")

def retry(fn, tries=4):
    last = None
    for i in range(tries):
            last = e; time.sleep(2*(i+1))
    raise last

def get_close_col(df):
    for col in ["收盘", "close", "Close"]:
        if col in df.columns:
            return col
    raise RuntimeError(f"no close column in {list(df.columns)}")
out = {"generated_at_utc": datetime.datetime.utcnow().isoformat()+"Z",
       "source": "yahoo", "trade_date": None, "items": {}}

def get_date_col(df):
    for col in ["日期", "date", "Date"]:
        if col in df.columns:
            return col
    raise RuntimeError(f"no date column in {list(df.columns)}")

def pct_from(df):
def fetch_one(code):
    """返回 (日期str, 收盘float, 涨跌幅%)"""
    close_col = get_close_col(df)
    date_col  = get_date_col(df)
    last = df.iloc[-1]
    close = float(last[close_col])
    date  = str(last[date_col])[:10]
    if "涨跌幅" in df.columns:
        chg = float(last["涨跌幅"])
    else:
        prev = float(df.iloc[-2][close_col])
        chg = round((close / prev - 1) * 100, 2)
    return date, close, chg
    df = yf.download(code, period="10d", interval="1d",
                     progress=False, auto_adjust=False)
    if df is None or len(df) < 2:
        raise RuntimeError("empty or insufficient data")
    closes = df["Close"]
    # 处理 yfinance 可能返回 MultiIndex 列
    if hasattr(closes, "columns"):
        closes = closes.iloc[:, 0]
    close = float(closes.iloc[-1])
    prev  = float(closes.iloc[-2])
    chg   = round((close / prev - 1) * 100, 2)
    date  = str(df.index[-1])[:10]
    return date, round(close, 4), chg

out = {"generated_at_utc": datetime.datetime.utcnow().isoformat()+"Z",
       "source": "akshare", "trade_date": None, "items": {}}

def add(name, fn):
def add(name, code):
    try:
        df = retry(fn)
        if df is None or len(df) == 0:
            raise RuntimeError("empty dataframe")
        d, close, chg = pct_from(df)
        d, close, chg = retry(lambda: fetch_one(code))
        out["items"][name] = {"close": close, "pct": chg, "date": d}
        out["trade_date"] = out["trade_date"] or d
        print(f"OK  {name}: {close} ({chg:+.2f}%) {d}")
        print(f"OK  {name} [{code}]: {close} ({chg:+.2f}%) {d}")
    except Exception as e:
        out["items"][name] = {"error": f"{type(e).__name__}: {e}"}
        print(f"ERR {name}: {e}", file=sys.stderr)
        print(f"ERR {name} [{code}]: {e}", file=sys.stderr)

# 指数: 用新浪接口(非_em),GitHub Actions IP不被封
for name, (code, pre) in INDICES.items():
    add(name, lambda code=code, pre=pre: ak.stock_zh_index_daily(symbol=pre+code))
for name, code in TICKERS.items():
    add(name, code)

# ETF: 先尝试新浪股票接口(treat ETF as stock),再fallback东方财富
for name, (code, pre) in ETFS.items():
    def etf_fn(code=code, pre=pre):
        try:
            return ak.stock_zh_a_daily(symbol=pre+code, adjust="")
        except Exception:
            return ak.fund_etf_hist_em(symbol=code, period="daily",
                                       start_date=START, end_date=END, adjust="")
    add(name, etf_fn)

# 股票: 先尝试新浪接口
for name, (code, pre) in STOCKS.items():
    add(name, lambda code=code, pre=pre: ak.stock_zh_a_daily(symbol=pre+code, adjust=""))

td = out["trade_date"] or END
td = out["trade_date"] or datetime.date.today().strftime("%Y%m%d")
td = td.replace("-", "")
import os; os.makedirs("data", exist_ok=True)
for path in (f"data/{td}.json", "data/latest.json"):
    with open(path, "w", encoding="utf-8") as f:
