# KRX 당일 전종목 시세 수집 — 매일 저녁 GitHub Actions가 자동 실행합니다.
import datetime, os, sys, time
from zoneinfo import ZoneInfo
import pandas as pd
from pykrx import stock

now = datetime.datetime.now(ZoneInfo("Asia/Seoul"))
date = now.strftime("%Y%m%d")

df = None
for attempt in range(5):
    try:
        t = stock.get_market_ohlcv(date, market="ALL")
        if t is not None and len(t) > 500 and t["거래량"].sum() > 0:
            df = t
            break
        print(f"{attempt+1}차 시도: 데이터 없음(휴장이거나 아직 미집계)")
    except Exception as e:
        print(f"{attempt+1}차 시도 오류: {e}")
    time.sleep(480)

if df is None:
    print("오늘 데이터 없음 — 휴장일이면 정상입니다.")
    sys.exit(0)

df = df.reset_index()
tcol = df.columns[0]
kospi = set(stock.get_market_ticker_list(date, market="KOSPI"))
kosdaq = set(stock.get_market_ticker_list(date, market="KOSDAQ"))
df["Market"] = df[tcol].map(lambda x: "KOSPI" if x in kospi else ("KOSDAQ" if x in kosdaq else "OTHER"))
df = df[df["Market"].isin(["KOSPI", "KOSDAQ"])]

out = pd.DataFrame({
    "Code": df[tcol].astype(str).str.zfill(6),
    "Close": df["종가"],
    "Volume": df["거래량"],
    "Amount": df["거래대금"],
    "Market": df["Market"],
})
out["ChangesRatio"] = df["등락률"] if "등락률" in df.columns else float("nan")

os.makedirs("data", exist_ok=True)
out.to_csv(f"data/{date}.csv", index=False)
with open("data/latest_date.txt", "w") as f:
    f.write(date)

files = sorted(f for f in os.listdir("data") if f.endswith(".csv") and f[:8].isdigit())
for f in files[:-40]:
    os.remove(os.path.join("data", f))

print("저장 완료:", date, len(out), "종목")
