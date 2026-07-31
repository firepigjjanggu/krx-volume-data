# 미국 주요종목(S&P500+나스닥100) 거래량 스캔 - 마감 후 실행
import datetime, io, os, sys, time
import pandas as pd
import yfinance as yf

sys.stdout.reconfigure(line_buffering=True)
os.makedirs("data/us", exist_ok=True)

def get_universe():
    import requests
    ua = {"User-Agent": "Mozilla/5.0 (krx-volume-data bot)"}
    tickers = {}
    try:
        html = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=ua, timeout=60).text
        sp = pd.read_html(io.StringIO(html))[0]
        for _, r in sp.iterrows():
            tickers[str(r["Symbol"]).replace(".", "-")] = {"Name": str(r["Security"]), "Exchange": "NYSE"}
        print("S&P500:", len(sp))
    except Exception as e:
        print("S&P500 목록 실패:", e)
    try:
        html = requests.get("https://en.wikipedia.org/wiki/Nasdaq-100", headers=ua, timeout=60).text
        tabs = pd.read_html(io.StringIO(html))
        nq = None
        for t in tabs:
            cols = [str(c).lower() for c in t.columns]
            if any("ticker" in c or "symbol" in c for c in cols) and len(t) >= 90:
                nq = t; break
        if nq is not None:
            tcol = [c for c in nq.columns if "icker" in str(c) or "ymbol" in str(c)][0]
            ncol = [c for c in nq.columns if "ompany" in str(c)][0]
            for _, r in nq.iterrows():
                sym = str(r[tcol]).replace(".", "-")
                if sym in tickers:
                    tickers[sym]["Exchange"] = "NASDAQ"
                else:
                    tickers[sym] = {"Name": str(r[ncol]), "Exchange": "NASDAQ"}
            print("나스닥100 병합 후:", len(tickers))
    except Exception as e:
        print("나스닥100 목록 실패:", e)
    if len(tickers) < 300:
        sys.exit("종목 목록 수집 실패")
    return tickers

tickers = get_universe()
syms = sorted(tickers.keys())
data = yf.download(syms, period="60d", interval="1d", group_by="ticker",
                   auto_adjust=False, threads=True, progress=False)

rows = []
for s in syms:
    try:
        d = data[s].dropna(subset=["Close", "Volume"])
    except Exception:
        continue
    if len(d) < 25:
        continue
    d = d[d["Volume"] > 0]
    if len(d) < 25:
        continue
    vol = d["Volume"]
    avg20 = vol.shift(1).rolling(20, min_periods=15).mean()
    ratio = vol / avg20
    last = d.index[-1]
    lastdate = str(last.date())
    close = float(d["Close"].iloc[-1])
    prev = float(d["Close"].iloc[-2])
    surged = (ratio >= 1.2)
    streak = 0
    for i in range(len(d) - 1, -1, -1):
        v = surged.iloc[i]
        if pd.notna(v) and v:
            streak += 1
        else:
            break
    r = float(ratio.iloc[-1]) if pd.notna(ratio.iloc[-1]) else 0
    if close * float(vol.iloc[-1]) < 5_000_000:
        continue
    rows.append({
        "Ticker": s, "Name": tickers[s]["Name"], "Exchange": tickers[s]["Exchange"],
        "Date": lastdate, "Close": round(close, 2),
        "ChangePct": round((close / prev - 1) * 100, 2),
        "Volume": int(vol.iloc[-1]), "Avg20": int(avg20.iloc[-1]) if pd.notna(avg20.iloc[-1]) else 0,
        "Ratio": round(r, 2), "Streak": min(streak, 9),
    })

df = pd.DataFrame(rows)
if len(df) < 200:
    sys.exit("미국 데이터 부족: " + str(len(df)))
maxd = df["Date"].max()
df = df[df["Date"] == maxd]
df = df[df["Ratio"] >= 1.2].sort_values("Ratio", ascending=False)
df.to_csv("data/us/latest.csv", index=False)
print("미국 저장 완료:", maxd, len(df), "종목 (전체 유니버스 중 급증만)")
