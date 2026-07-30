# KRX 일별 전종목 시세 수집 — 최근 7일 중 빠진 날짜를 자동으로 채웁니다.
import datetime, os, time
from zoneinfo import ZoneInfo
import pandas as pd
from pykrx import stock

now = datetime.datetime.now(ZoneInfo("Asia/Seoul"))
os.makedirs("data", exist_ok=True)

def fetch_day(date):
    for attempt in range(3):
        try:
            t = stock.get_market_ohlcv(date, market="ALL")
            if t is not None and len(t) > 500 and t["거래량"].sum() > 0:
                return t
            print(date, f"{attempt+1}차: 데이터 없음")
        except Exception as e:
            print(date, f"{attempt+1}차 오류:", e)
        time.sleep(120)
    return None

saved = []
for back in range(7, -1, -1):
    day = now - datetime.timedelta(days=back)
    if day.weekday() >= 5:            # 토/일 건너뜀
        continue
    if back == 0 and now.hour < 16:   # 오늘 데이터는 장 마감 후에만
        continue
    date = day.strftime("%Y%m%d")
    if os.path.exists(f"data/{date}.csv"):
        continue
    t = fetch_day(date)
    if t is None:
        print(date, "건너뜀 (휴장일이면 정상)")
        continue
    t = t.reset_index()
    tcol = t.columns[0]
    kospi = set(stock.get_market_ticker_list(date, market="KOSPI"))
    kosdaq = set(stock.get_market_ticker_list(date, market="KOSDAQ"))
    t["Market"] = t[tcol].map(lambda x: "KOSPI" if x in kospi else ("KOSDAQ" if x in kosdaq else "OTHER"))
    t = t[t["Market"].isin(["KOSPI", "KOSDAQ"])]
    out = pd.DataFrame({
        "Code": t[tcol].astype(str).str.zfill(6),
        "Close": t["종가"],
        "Volume": t["거래량"],
        "Amount": t["거래대금"],
        "Market": t["Market"],
    })
    out["ChangesRatio"] = t["등락률"] if "등락률" in t.columns else float("nan")
    out.to_csv(f"data/{date}.csv", index=False)
    saved.append(date)
    time.sleep(3)

stems = sorted(f[:8] for f in os.listdir("data") if f.endswith(".csv") and f[:8].isdigit())
if stems:
    with open("data/latest_date.txt", "w") as f:
        f.write(stems[-1])
for s in stems[:-40]:
    os.remove(os.path.join("data", s + ".csv"))
print("새로 저장:", saved if saved else "없음", "| 최신 날짜:", stems[-1] if stems else "없음")
