# KRX 전종목 시세 수집 (네이버 증권 API) — 마지막 거래일 마감 데이터를 저장합니다.
import datetime, os, sys, time
from zoneinfo import ZoneInfo
import requests
import pandas as pd

sys.stdout.reconfigure(line_buffering=True)
KST = ZoneInfo("Asia/Seoul")
now = datetime.datetime.now(KST)
os.makedirs("data", exist_ok=True)

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Accept": "application/json",
    "Referer": "https://m.stock.naver.com/",
})

def get_trade_date():
    try:
        r = S.get("https://m.stock.naver.com/api/index/KOSPI/basic", timeout=15)
        s = str(r.json().get("localTradedAt", ""))[:10]
        if len(s) == 10:
            return s.replace("-", "")
    except Exception as e:
        print("날짜 API 오류:", e)
    d = now
    if d.hour < 9:
        d -= datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d.strftime("%Y%m%d")

def num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return float("nan")

def fetch_market(market):
    rows = []
    for page in range(1, 101):
        items = None
        for attempt in range(3):
            try:
                r = S.get(f"https://m.stock.naver.com/api/stocks/marketValue/{market}",
                          params={"page": page, "pageSize": 100}, timeout=20)
                j = r.json()
                if isinstance(j, list):
                    items = j
                else:
                    items = j.get("stocks") or j.get("result") or []
                break
            except Exception as e:
                print(market, "page", page, "오류:", e)
                time.sleep(20)
        if not items:
            break
        for it in items:
            rows.append({
                "Code": str(it.get("itemCode", "")).zfill(6),
                "Close": num(it.get("closePrice")),
                "Volume": num(it.get("accumulatedTradingVolume")),
                "ChangesRatio": num(it.get("fluctuationsRatio")),
                "Market": market,
            })
        time.sleep(0.3)
    print(market, len(rows), "종목 수집")
    return rows

date = get_trade_date()
print("거래일:", date, "| 현재:", now.strftime("%Y-%m-%d %H:%M"))

if date == now.strftime("%Y%m%d") and 9 <= now.hour < 16:
    print("장중이라 데이터가 미완성 — 저장하지 않고 종료합니다.")
    sys.exit(0)

if os.path.exists(f"data/{date}.csv"):
    print(date, "이미 저장돼 있음. 종료.")
    sys.exit(0)

rows = fetch_market("KOSPI") + fetch_market("KOSDAQ")
print("총 수집:", len(rows), "종목")
if len(rows) < 1500:
    print("수집 실패(종목 수 부족) — 저장하지 않습니다.")
    sys.exit(1)

df = pd.DataFrame(rows).dropna(subset=["Close", "Volume"])
df["Amount"] = (df["Close"] * df["Volume"]).round()
df = df[["Code", "Close", "Volume", "Amount", "Market", "ChangesRatio"]]
df.to_csv(f"data/{date}.csv", index=False)

stems = sorted(f[:8] for f in os.listdir("data") if f.endswith(".csv") and f[:8].
