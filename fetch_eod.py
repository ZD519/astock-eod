# -*- coding: utf-8 -*-
import json, time, datetime, sys
import yfinance as yf

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
    "A500ETF 159338":  "159338.SZ",
    "国航 601111":     "601111.SS",
}

def retry(fn, tries=4):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e; time.sleep(2*(i+1))
    raise last

out = {"generated_at_utc": datetime.datetime.utcnow().isoformat()+"Z",
       "source": "yahoo", "trade_date": None, "items": {}}

def fetch_one(code):
    df = yf.download(code, period="10d", interval="1d",
                     progress=False, auto_adjust=False)
    if df is None or len(df) < 2:
        raise RuntimeError("empty or insufficient data")
    closes = df["Close"]
    if hasattr(closes, "columns"):
        closes = closes.iloc[:, 0]
    close = float(closes.iloc[-1])
    prev  = float(closes.iloc[-2])
    chg   = round((close / prev - 1) * 100, 2)
    date  = str(df.index[-1])[:10]
    return date, round(close, 4), chg

def add(name, code):
    try:
        d, close, chg = retry(lambda: fetch_one(code))
        out["items"][name] = {"close": close, "pct": chg, "date": d}
        out["trade_date"] = out["trade_date"] or d
        print(f"OK  {name} [{code}]: {close} ({chg:+.2f}%) {d}")
    except Exception as e:
        out["items"][name] = {"error": f"{type(e).__name__}: {e}"}
        print(f"ERR {name} [{code}]: {e}", file=sys.stderr)

for name, code in TICKERS.items():
    add(name, code)

td = out["trade_date"] or datetime.date.today().strftime("%Y-%m-%d")
td = td.replace("-", "")
import os; os.makedirs("data", exist_ok=True)
for path in (f"data/{td}.json", "data/latest.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
print("\nWROTE data/%s.json and data/latest.json" % td)
