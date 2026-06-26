# -*- coding: utf-8 -*-
"""
A股 EOD 数据拉取 — 跑在 GitHub Actions 上。 v2（数据准确性版）
关键改进:
  1) 涨跌幅不再信 Yahoo 自算。yfinance 在"前一根bar缺失"时会算错(实测沪深300报 -1.48%、
     真实 -3.03%)。改为用【上一交易日我们自己归档的收盘价】(data/上一个YYYYMMDD.json)自算:
         pct = (今日close / 上一交易日close - 1) * 100
     收盘价可靠、且由我们掌控,故自算的 pct 也可靠。每条标 pct_src 注明出处。
     归档里没有该标的(首次运行/上次缺失)才退回 Yahoo/Tushare 自算值, 标 selfcalc_unverified。
  2) Yahoo 拉取加重试(最多3次), 降低 创业板/科创50 等指数的偶发空数据。
  3) Tushare 仅补 Yahoo 完全失败的(index_daily 限频1次/小时, 故调用间 sleep 61s)。
取不到的写 error, 绝不编造。
"""
import json, time, datetime, sys, os, glob

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
    "创业板ETF 159915": ("159915.SZ", "159915.SZ", "etf"),   # 仅作"创业板指"代理, 不计入持仓表
    "国航 601111":     ("601111.SH", "601111.SS", "stock"),
}

# 指数 Yahoo+Tushare 都取不到时, 用对应 ETF 代理其【涨跌幅】(点位仍缺, 只代理 pct)
INDEX_PROXY = {
    "创业板": "创业板ETF 159915",
    "科创50": "科创50ETF 588000",
}

out = {"generated_at_utc": datetime.datetime.utcnow().isoformat() + "Z",
       "source": "yahoo+tushare", "trade_date": None, "items": {}}


# ============ 上一交易日归档(用于自算 pct) ============
def load_prev_archive(td_clean):
    """取 data/ 下文件名日期【严格小于今日】的最近一个, 返回 (prev_date, {name: close})。"""
    best = None
    for p in glob.glob("data/*.json"):
        base = os.path.basename(p)
        if base == "latest.json":
            continue
        ds = base[:-5]  # 去掉 .json
        if not (len(ds) == 8 and ds.isdigit()):
            continue
        if ds < td_clean and (best is None or ds > best):
            best = ds
    if best is None:
        return None, {}
    try:
        with open("data/%s.json" % best, encoding="utf-8") as f:
            prev = json.load(f)
        closes = {}
        for name, v in prev.get("items", {}).items():
            if isinstance(v, dict) and isinstance(v.get("close"), (int, float)):
                closes[name] = v["close"]
        pd = prev.get("trade_date") or "%s-%s-%s" % (best[:4], best[4:6], best[6:8])
        return pd, closes
    except Exception as e:
        print("WARN load prev archive %s: %s" % (best, e), file=sys.stderr)
        return None, {}


# ============ Yahoo (主力, 带重试) ============
def yahoo_fetch(yf_code, tries=3):
    import yfinance as yf
    last_err = None
    for _ in range(tries):
        try:
            df = yf.download(yf_code, period="15d", interval="1d",
                             progress=False, auto_adjust=False)
            if df is not None and len(df) >= 1:
                closes = df["Close"]
                if hasattr(closes, "columns"):
                    closes = closes.iloc[:, 0]
                close = round(float(closes.iloc[-1]), 4)
                date = str(df.index[-1])[:10]
                yh_pct = None  # Yahoo 自算, 仅作归档缺失时的兜底
                if len(closes) >= 2:
                    prev = float(closes.iloc[-2])
                    if prev:
                        yh_pct = round((close / prev - 1) * 100, 2)
                return date, close, yh_pct
        except Exception as e:
            last_err = e
        time.sleep(2)
    raise RuntimeError("empty after %d tries (%s)" % (tries, last_err))


print("=== 阶段1: Yahoo 拉取全部(带重试) ===")
for name, (ts_code, yf_code, typ) in ITEMS.items():
    try:
        d, close, yh_pct = yahoo_fetch(yf_code)
        out["items"][name] = {"close": close, "pct": None, "_selfpct": yh_pct,
                              "date": d, "src": "yahoo"}
        out["trade_date"] = out["trade_date"] or d
        print("OK  [yahoo] %s: %s (selfpct=%s) %s" % (name, close, yh_pct, d))
    except Exception as e:
        out["items"][name] = {"error": "yahoo: %s" % e}
        print("ERR [yahoo] %s: %s" % (name, e), file=sys.stderr)

# ============ Tushare (补 Yahoo 完全失败的) ============
failed = [n for n, v in out["items"].items() if "error" in v]
if failed and TUSHARE_TOKEN:
    print("\n=== 阶段2: Tushare 补救 %s ===" % failed)
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
            row = df.iloc[-1]
            close = round(float(row["close"]), 4)
            date = str(row["trade_date"])
            date_fmt = "%s-%s-%s" % (date[:4], date[4:6], date[6:8])
            ts_pct = round(float(row["pct_chg"]), 2)
            out["items"][name] = {"close": close, "pct": None, "_selfpct": ts_pct,
                                  "date": date_fmt, "src": "tushare"}
            out["trade_date"] = out["trade_date"] or date_fmt
            print("OK  [tushare] %s: %s (selfpct=%s) %s" % (name, close, ts_pct, date_fmt))
        except Exception as e:
            out["items"][name] = {"error": "yahoo+tushare both failed: %s" % e}
            print("ERR [tushare] %s: %s" % (name, e), file=sys.stderr)

# ============ 阶段3: 用上一交易日归档自算 pct(权威) ============
td = out["trade_date"] or datetime.date.today().strftime("%Y-%m-%d")
td_clean = td.replace("-", "")
prev_date, prev_closes = load_prev_archive(td_clean)
print("\n=== 阶段3: 用归档(%s)自算涨跌幅 ===" % prev_date)
for name, v in out["items"].items():
    if "error" in v:
        continue
    c = v["close"]
    if name in prev_closes and prev_closes[name]:
        pc = prev_closes[name]
        v["pct"] = round((c / pc - 1) * 100, 2)
        v["prev_close"] = pc
        v["prev_date"] = prev_date
        v["pct_src"] = "archive(%s)" % prev_date
    else:
        v["pct"] = v.get("_selfpct")          # 归档缺该标的 → 退回自算
        v["pct_src"] = "selfcalc_unverified"
    v.pop("_selfpct", None)
    print("  %s: close=%s pct=%s (%s)" % (name, c, v["pct"], v["pct_src"]))

# ============ 阶段4: 指数代理(Yahoo+Tushare都失败的指数, 用ETF代理涨跌幅) ============
print("\n=== 阶段4: 指数 ETF 代理 ===")
for idx_name, etf_name in INDEX_PROXY.items():
    iv = out["items"].get(idx_name, {})
    ev = out["items"].get(etf_name, {})
    if "error" in iv and "error" not in ev and ev.get("pct") is not None:
        out["items"][idx_name] = {
            "close": None,                       # 指数点位拿不到
            "pct": ev["pct"],                    # 涨跌幅用 ETF 代理
            "proxy": etf_name,
            "date": ev.get("date"),
            "src": "proxy",
            "pct_src": "proxy_etf(%s)" % etf_name,
            "note": "指数点位未取到; 涨跌幅用 %s 代理(约值)" % etf_name,
        }
        out["trade_date"] = out["trade_date"] or ev.get("date")
        print("  PROXY %s: pct=%s 来自 %s (点位缺)" % (idx_name, ev["pct"], etf_name))
    elif "error" in iv:
        print("  %s 仍缺且代理不可用(%s)" % (idx_name, etf_name))

# ============ 输出 ============
os.makedirs("data", exist_ok=True)
for path in ("data/%s.json" % td_clean, "data/latest.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

total_ok = sum(1 for v in out["items"].values() if "error" not in v)
arch = sum(1 for v in out["items"].values()
           if isinstance(v, dict) and str(v.get("pct_src", "")).startswith("archive"))
print("\nDONE: %d/%d OK; %d 个pct由归档校验, 其余 selfcalc_unverified" %
      (total_ok, len(ITEMS), arch))
print("WROTE data/%s.json and data/latest.json" % td_clean)
