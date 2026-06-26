# -*- coding: utf-8 -*-
"""
拉取 A 股指数 + 指定标的的当日(最近交易日)EOD收盘与涨跌幅。
跑在 GitHub Actions 的 runner 上(满血外网),不在受限环境里跑。
数据源 AKShare(免费/无token)。取不到的标的写 error,绝不编造。
"""
import json, time, datetime, sys
import akshare as ak

# --- 你的监测清单 ---
INDICES = {  # 名称: (akshare代码, 交易所前缀)
    "上证":   ("000001", "sh"),
    "深成指": ("399001", "sz"),
    "创业板": ("399006", "sz"),
    "科创50": ("000688", "sh"),
    "沪深300":("000300", "sh"),
}
ETFS = {  # 名称: 6位代码
    "红利低波 512890": "512890",
    "电力ETF 159611":  "159611",
    "半导体ETF 512480":"512480",
    "通信ETF 515880":  "515880",
    "科创50ETF 588000":"588000",
}
STOCKS = {"国航 601111": "601111"}

START = (datetime.date.today() - datetime.timedelta(days=20)).strftime("%Y%m%d")
END   = datetime.date.today().strftime("%Y%m%d")

def retry(fn, tries=4):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e; time.sleep(2*(i+1))
    raise last

def pct_from(df):
    """优先用 '涨跌幅' 列, 否则用最后两收盘自算。返回 (日期, 收盘, 涨跌幅%)."""
    last = df.iloc[-1]
    close = float(last.get("收盘", last.get("close")))
    date  = str(last.get("日期", last.get("date")))[:10]
    if "涨跌幅" in df.columns:
        chg = float(last["涨跌幅"])
    else:
        prev = float(df.iloc[-2].get("收盘", df.iloc[-2].get("close")))
        chg = round((close/prev - 1)*100, 2)
    return date, close, chg

out = {"generated_at_utc": datetime.datetime.utcnow().isoformat()+"Z",
       "source": "akshare", "trade_date": None, "items": {}}

def add(name, fn):
    try:
        df = retry(fn)
        if df is None or len(df) == 0:
            raise RuntimeError("empty")
        d, close, chg = pct_from(df)
        out["items"][name] = {"close": close, "pct": chg, "date": d}
        out["trade_date"] = out["trade_date"] or d
        print(f"OK  {name}: {close} ({chg:+.2f}%) {d}")
    except Exception as e:
        out["items"][name] = {"error": f"{type(e).__name__}: {e}"}
        print(f"ERR {name}: {e}", file=sys.stderr)

for name,(code,pre) in INDICES.items():
    add(name, lambda code=code,pre=pre: ak.stock_zh_index_daily_em(symbol=pre+code))
for name,code in ETFS.items():
    add(name, lambda code=code: ak.fund_etf_hist_em(symbol=code, period="daily",
                                start_date=START, end_date=END, adjust=""))
for name,code in STOCKS.items():
    add(name, lambda code=code: ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=START, end_date=END, adjust=""))

td = out["trade_date"] or END
import os; os.makedirs("data", exist_ok=True)
for path in (f"data/{td}.json", "data/latest.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
print("\nWROTE data/%s.json and data/latest.json" % td)
