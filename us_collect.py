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

frames = {}
last_dates = []
for s in syms:
    try:
        d = data[s].dropna(subset=["Close", "Volume"])
    except Exception:
        continue
    d = d[d["Volume"] > 0]
    if len(d) < 25:
        continue
    frames[s] = d
    last_dates.append(d.index[-1])

# 다수결로 "진짜 마감된 세션" 결정 (시간외 반쪽 데이터 제거)
target = pd.Series(last_dates).mode()[0]
print("기준 세션:", target.date(), "| 전체", len(frames), "종목")

rows = []
for s, d in frames.items():
    if target not in d.index:
        continue
    pos = d.index.get_loc(target)
    if pos < 21:
        continue
    vol = d["Volume"]
    avg20 = vol.shift(1).rolling(20, min_periods=15).mean()
    ratio = vol / avg20
    close = float(d["Close"].iloc[pos])
    prev = float(d["Close"].iloc[pos - 1])
    surged = (ratio >= 1.2)
    streak = 0
    for i in range(pos, -1, -1):
        v = surged.iloc[i]
        if pd.notna(v) and v:
            streak += 1
        else:
            break
    r = float(ratio.iloc[pos]) if pd.notna(ratio.iloc[pos]) else 0
    if close * float(vol.iloc[pos]) < 5_000_000:
        continue
    rows.append({
        "Ticker": s, "Name": tickers[s]["Name"], "Exchange": tickers[s]["Exchange"],
        "Date": str(target.date()), "Close": round(close, 2),
        "ChangePct": round((close / prev - 1) * 100, 2),
        "Volume": int(vol.iloc[pos]), "Avg20": int(avg20.iloc[pos]) if pd.notna(avg20.iloc[pos]) else 0,
        "Ratio": round(r, 2), "Streak": min(streak, 9),
    })

df = pd.DataFrame(rows)
if len(df) < 200:
    sys.exit("미국 데이터 부족: " + str(len(df)))
df = df[df["Ratio"] >= 1.2].sort_values("Ratio", ascending=False)
df.to_csv("data/us/latest.csv", index=False)
print("미국 저장 완료:", target.date(), len(df), "종목 (급증만)")
