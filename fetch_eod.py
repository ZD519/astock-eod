# -*- coding: utf-8 -*-
"""
A股 EOD 数据拉取 — 跑在 GitHub Actions 上。
策略:
  - Yahoo Finance 当主力(免费/不封云IP, 能拿到绝大多数标的)
  - Tushare Pro 只补 Yahoo 拿不到的(创业板指/科创50指等), index_daily 限频1次/分钟,
    故 Tushare 调用之间 sleep 61s 避开限频。
取不到的标的写 error，绝不编造。
"""
import json, time, datetime, sys, os

TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# 名称 -> (tushare代码, yahoo代码, 类型)   类型: index / etf / stock
ITEMS = {
    "上证":            ("000001.SH", "000001.SS", "index"),
    "深成指":          ("399001.SZ", "399001.SZ", "index"),
    "创业板":          ("399006.SZ", "399006.SZ", "index"),
    "科创50":          ("000688.SH", "000688.SS", "index"),
    "沪深300":         ("000300.SH", "000300.SS", "index"),
    "红利低波 512890": ("512890.SH", "512890.SS", "etf"),
    "电力ETF 159611":  ("159611.SZ", "159611.SZ", "etf"),
    "半导体ETF 512480":("512480.SH", "512480.SS", "etf"),
    "通信ETF 515880":  ("515880.SH", "515880.SS", "etf"),
    "科创50ETF 588000":("588000.SH", "588000.SS", "etf"),
    "A500ETF 159338":  ("159338.SZ", "159338.SZ", "etf"),
    "国航 601111":     ("601111.SH", "601111.SS", "stock"),
}

out = {"generated_at_utc": datetime.datetime.utcnow().isoformat()+"Z",
       "source": "yahoo+tushare", "trade_date": None, "items": {}}

# ============ Yahoo (主力) ============
def yahoo_fetch(yf_code):
    import yfinance as yf
    df = yf.download(yf_code, period="10d", interval="1d",
                     progress=False, auto_adjust=False)
    if df is None or len(df) < 2:
        raise RuntimeError("empty or insufficient data")
    closes = df["Close"]
    if hasattr(closes, "columns"):
        closes = closes.iloc[:, 0]
    close = round(float(closes.iloc[-1]), 4)
    prev = float(closes.iloc[-2])
    chg = round((close / prev - 1) * 100, 2)
    date = str(df.index[-1])[:10]
    return date, close, chg

print("=== 阶段1: Yahoo 拉取全部 ===")
for name, (ts_code, yf_code, typ) in ITEMS.items():
    try:
        d, close, chg = yahoo_fetch(yf_code)
        out["items"][name] = {"close": close, "pct": chg, "date": d, "src": "yahoo"}
        out["trade_date"] = out["trade_date"] or d
        print(f"OK  [yahoo] {name}: {close} ({chg:+.2f}%) {d}")
    except Exception as e:
        out["items"][name] = {"error": f"yahoo: {e}"}
        print(f"ERR [yahoo] {name}: {e}", file=sys.stderr)

# ============ Tushare (补 Yahoo 失败的) ============
failed = [n for n, v in out["items"].items() if "error" in v]
if failed and TUSHARE_TOKEN:
    print(f"\n=== 阶段2: Tushare 补救 {failed} ===")
    import tushare as ts
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
    today = datetime.date.today().strftime("%Y%m%d")
    start = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y%m%d")

    first = True
    for name in failed:
        ts_code, _, typ = ITEMS[name]
        if not first:
            print("  (sleep 61s 避开 index_daily 限频...)")
            time.sleep(61)
        first = False
        try:
            if typ == "index":
                df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=today)
            elif typ == "etf":
                df = pro.fund_daily(ts_code=ts_code, start_date=start, end_date=today)
            else:
                df = pro.daily(ts_code=ts_code, start_date=start, end_date=today)
            if df is None or len(df) == 0:
                raise RuntimeError("empty")
            df = df.sort_values("trade_date")
            last = df.iloc[-1]
            close = round(float(last["close"]), 4)
            chg = round(float(last["pct_chg"]), 2)
            date = str(last["trade_date"])
            date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
            out["items"][name] = {"close": close, "pct": chg, "date": date_fmt, "src": "tushare"}
            out["trade_date"] = out["trade_date"] or date_fmt
            print(f"OK  [tushare] {name}: {close} ({chg:+.2f}%) {date_fmt}")
        except Exception as e:
            out["items"][name] = {"error": f"yahoo+tushare both failed: {e}"}
            print(f"ERR [tushare] {name}: {e}", file=sys.stderr)

# ============ 输出 ============
td = out["trade_date"] or datetime.date.today().strftime("%Y-%m-%d")
td_clean = td.replace("-", "")
os.makedirs("data", exist_ok=True)
for path in (f"data/{td_clean}.json", "data/latest.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

total_ok = sum(1 for v in out["items"].values() if "error" not in v)
print(f"\nDONE: {total_ok}/{len(ITEMS)} OK")
print(f"WROTE data/{td_clean}.json and data/latest.json")
