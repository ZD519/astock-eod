# -*- coding: utf-8 -*-
"""
A股 EOD 数据拉取 — 跑在 GitHub Actions 上。
主数据源: Tushare Pro (全A股覆盖，含创业板指/科创50)
备用数据源: Yahoo Finance (Tushare 挂了自动切换)
取不到的标的写 error，绝不编造。
"""
import json, time, datetime, sys, os

TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# --- 监测清单 ---
# 格式: 名称 -> (tushare代码, yahoo代码)
# tushare 指数用 .SH/.SZ，股票/ETF 用6位代码+后缀
ITEMS = {
    "上证":            ("000001.SH", "000001.SS"),
    "深成指":          ("399001.SZ", "399001.SZ"),
    "创业板":          ("399006.SZ", "399006.SZ"),
    "科创50":          ("000688.SH", "000688.SS"),
    "沪深300":         ("000300.SH", "000300.SS"),
    "红利低波 512890": ("512890.SH", "512890.SS"),
    "电力ETF 159611":  ("159611.SZ", "159611.SZ"),
    "半导体ETF 512480":("512480.SH", "512480.SS"),
    "通信ETF 515880":  ("515880.SH", "515880.SS"),
    "科创50ETF 588000":("588000.SH", "588000.SS"),
    "A500ETF 159338":  ("159338.SZ", "159338.SZ"),
    "国航 601111":     ("601111.SH", "601111.SS"),
}

INDICES = {"000001.SH","399001.SZ","399006.SZ","000688.SH","000300.SH"}

out = {"generated_at_utc": datetime.datetime.utcnow().isoformat()+"Z",
       "source": None, "trade_date": None, "items": {}}

# ============ Tushare 拉取 ============
def try_tushare():
    import tushare as ts
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()

    today = datetime.date.today().strftime("%Y%m%d")
    start = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y%m%d")

    ok_count = 0
    for name, (ts_code, _) in ITEMS.items():
        try:
            if ts_code in INDICES:
                df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=today)
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

            out["items"][name] = {"close": close, "pct": chg, "date": date_fmt}
            out["trade_date"] = out["trade_date"] or date_fmt
            ok_count += 1
            print(f"OK  [tushare] {name}: {close} ({chg:+.2f}%) {date_fmt}")
        except Exception as e:
            out["items"][name] = {"error": f"tushare: {e}"}
            print(f"ERR [tushare] {name}: {e}", file=sys.stderr)

    out["source"] = "tushare"
    return ok_count

# ============ Yahoo 备用 ============
def try_yahoo():
    import yfinance as yf

    ok_count = 0
    for name, (_, yf_code) in ITEMS.items():
        if name in out["items"] and "error" not in out["items"][name]:
            ok_count += 1
            continue
        try:
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

            out["items"][name] = {"close": close, "pct": chg, "date": date}
            out["trade_date"] = out["trade_date"] or date
            ok_count += 1
            print(f"OK  [yahoo]   {name}: {close} ({chg:+.2f}%) {date}")
        except Exception as e:
            prev = out["items"].get(name, {})
            if "error" in prev:
                out["items"][name] = {"error": f"tushare+yahoo both failed: {e}"}
            else:
                out["items"][name] = {"error": f"yahoo: {e}"}
            print(f"ERR [yahoo]   {name}: {e}", file=sys.stderr)

    if out["source"] == "tushare":
        out["source"] = "tushare+yahoo_fallback"
    else:
        out["source"] = "yahoo"
    return ok_count

# ============ 主流程 ============
tushare_ok = 0
if TUSHARE_TOKEN:
    try:
        tushare_ok = try_tushare()
        print(f"\n--- Tushare: {tushare_ok}/{len(ITEMS)} OK ---")
    except Exception as e:
        print(f"\n--- Tushare failed entirely: {e} ---", file=sys.stderr)

failed = [n for n, v in out["items"].items() if "error" in v]
not_fetched = [n for n in ITEMS if n not in out["items"]]
need_yahoo = failed + not_fetched

if need_yahoo:
    print(f"\nFalling back to Yahoo for: {need_yahoo}")
    try:
        try_yahoo()
    except Exception as e:
        print(f"Yahoo fallback failed: {e}", file=sys.stderr)

# ============ 输出 ============
td = out["trade_date"] or datetime.date.today().strftime("%Y-%m-%d")
td_clean = td.replace("-", "")
os.makedirs("data", exist_ok=True)
for path in (f"data/{td_clean}.json", "data/latest.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

total_ok = sum(1 for v in out["items"].values() if "error" not in v)
total = len(ITEMS)
print(f"\nDONE: {total_ok}/{total} OK, source={out['source']}")
print(f"WROTE data/{td_clean}.json and data/latest.json")
