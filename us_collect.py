# 미국 주요종목(S&P500+나스닥100) 거래량 스캔 - 마감 후 실행
import datetime, io, os, sys, time
from zoneinfo import ZoneInfo
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
    nq = None
    try:
        # 1차: 나스닥100 구성종목이 분리된 문서(위키 Nasdaq-100 본문은 표가 빠졌음 - FN-023)
        html = requests.get(
            "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies", headers=ua, timeout=60
        ).text
        tabs = pd.read_html(io.StringIO(html))
        for t in tabs:
            cols = [str(c).lower() for c in t.columns]
            if any("ticker" in c or "symbol" in c for c in cols) and len(t) >= 90:
                nq = t; break
    except Exception as e:
        print("나스닥100 목록(1차 위키) 실패:", e)

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
    else:
        # 2차 폴백: api.nasdaq.com (Actions 러너 IP 차단 가능성 있음 - 실패해도 계속 진행)
        try:
            r = requests.get(
                "https://api.nasdaq.com/api/quote/list-type/nasdaq100",
                headers={**ua, "Accept": "application/json"},
                timeout=60,
            )
            rows = r.json()["data"]["rows"]
            for row in rows:
                sym = str(row["symbol"]).replace(".", "-")
                if sym in tickers:
                    tickers[sym]["Exchange"] = "NASDAQ"
                else:
                    tickers[sym] = {"Name": str(row.get("companyName", sym)), "Exchange": "NASDAQ"}
            print("나스닥100 병합 후:", len(tickers))
        except Exception as e:
            print("나스닥100 목록(2차 api.nasdaq.com) 실패:", e)
            print("[경고] 나스닥100 목록을 두 소스 모두에서 가져오지 못함 - 거래소 구분 없이 진행(NYSE 단일)")
    if len(tickers) < 300:
        sys.exit("종목 목록 수집 실패")
    return tickers

tickers = get_universe()
syms = sorted(tickers.keys())
data = yf.download(syms, period="60d", interval="1d", group_by="ticker",
                   auto_adjust=False, threads=True, progress=False)

# 미국 동부시간 기준, 마지막으로 "마감이 끝난" 날짜 계산 (시간외 임시봉 차단)
et = datetime.datetime.now(ZoneInfo("America/New_York"))
cut_date = et.date()
if (et.hour, et.minute) < (16, 30):
    cut_date -= datetime.timedelta(days=1)
cut = pd.Timestamp(cut_date)
print("마감 기준 컷오프:", cut_date, "(ET 현재", et.strftime("%m-%d %H:%M"), ")")

frames = {}
last_dates = []
for s in syms:
    try:
        d = data[s].dropna(subset=["Close", "Volume"])
    except Exception:
        continue
    if getattr(d.index, "tz", None) is not None:
        d.index = d.index.tz_localize(None)
    d = d[(d["Volume"] > 0) & (d.index <= cut)]
    if len(d) < 25:
        continue
    frames[s] = d
    last_dates.append(d.index[-1])

# 다수결로 기준 세션 확정
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
