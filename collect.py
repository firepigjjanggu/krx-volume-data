# KRX 전종목 시세 수집 (네이버) — 정규장 + 통합(시간외·NXT 포함) 거래량을 함께 저장합니다.
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
                items = j if isinstance(j, list) else (j.get("stocks") or j.get("result") or [])
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
        time.sleep(0.2)
    print(market, len(rows), "종목 수집")
    return rows

def fetch_total_volume(code):
    # 통합(정규장+시간외+넥스트레이드) 거래량
    for attempt in range(2):
        try:
            r = S.get(f"https://m.stock.naver.com/api/stock/{code}/integration", timeout=10)
            for t in r.json().get("totalInfos", []):
                if t.get("code") == "accumulatedTradingVolume":
                    v = num(t.get("value"))
                    return v if v == v else None
            return None
        except Exception:
            time.sleep(1)
    return None

date = get_trade_date()
print("거래일:", date, "| 현재:", now.strftime("%Y-%m-%d %H:%M"))

if date == now.strftime("%Y%m%d") and 9 <= now.hour < 20:
    print("장 운영시간(통합 기준 20시 이전) - 저장하지 않고 종료합니다.")
    sys.exit(0)

path = f"data/{date}.csv"
if os.path.exists(path):
    try:
        old = pd.read_csv(path, nrows=1)
        if "VolumeTotal" in old.columns:
            print(date, "이미 통합 데이터로 저장돼 있음. 종료.")
            sys.exit(0)
        print(date, "구버전 데이터 발견 - 통합 거래량 포함해 다시 수집합니다.")
    except Exception:
        pass

rows = fetch_market("KOSPI") + fetch_market("KOSDAQ")
print("총 수집:", len(rows), "종목")
if len(rows) < 1500:
    print("수집 실패(종목 수 부족) - 저장하지 않습니다.")
    sys.exit(1)

print("통합 거래량 수집 시작 (약 10분)...")
ok = 0
t0 = time.time()
for i, row in enumerate(rows):
    tv = fetch_total_volume(row["Code"])
    if tv is not None and tv >= row["Volume"]:
        row["VolumeTotal"] = tv
        ok += 1
    else:
        row["VolumeTotal"] = row["Volume"]
    if i % 500 == 0:
        print(f"  {i}/{len(rows)} ({int(time.time()-t0)}초)")
    time.sleep(0.02)
print(f"통합 거래량 성공 {ok}/{len(rows)} ({int(time.time()-t0)}초 소요)")

df = pd.DataFrame(rows).dropna(subset=["Close", "Volume"])
df["Amount"] = (df["Close"] * df["Volume"]).round()
df["AmountTotal"] = (df["Close"] * df["VolumeTotal"]).round()
df = df[["Code", "Close", "Volume", "VolumeTotal", "Amount", "AmountTotal", "Market", "ChangesRatio"]]
df.to_csv(path, index=False)

up = df[df["VolumeTotal"] > df["Volume"]]
if len(up):
    ratio = (up["VolumeTotal"] / up["Volume"]).median()
    print(f"전체>정규장 종목 {len(up)}개, 중앙값 {ratio:.2f}배")

stems = sorted(f[:8] for f in os.listdir("data") if f.endswith(".csv") and f[:8].isdigit())
with open("data/latest_date.txt", "w") as f:
    f.write(stems[-1])
for s in stems[:-40]:
    os.remove(os.path.join("data", s + ".csv"))
print("저장 완료:", date, len(df), "종목")
