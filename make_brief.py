# brief.json v1 생성 — handoff.md §3 scan.py의 계산 로직을 그대로 재현하되
# print 대신 구조화된 JSON을 만든다. 계산식(20일 평균 shift(1)+min_periods=15,
# ratio 통합/정규장 자동 전환, 임계 에스컬레이션 +5%p, 연속급증 5거래일·캡4,
# 스팩 제외, 거래대금 10억 필터)은 원문에서 바꾸지 않는다.
#
# 원문과 다른 4개 지점(architecture.md §4-2 승인됨):
#   ① 저장소 CSV는 HTTP 재다운로드 대신 체크아웃된 data/*.csv 로컬 스캔
#   ② rp.join(names, on='Code') 대신 Series.map (CSV Name → marcap Name → Code 폴백)
#   ③ 출력은 print 대신 dict → data/.brief.json.tmp → os.replace() 원자 교체
#   ④ 실패 시 종료코드: KR 계산 실패만 sys.exit(1). 미국 없음·오래됨은 실패가 아니라
#      status="partial" + us.available=false
import datetime
import glob
import io
import json
import os
import sys
import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(REPO_ROOT, "data")
US_CSV_PATH = os.path.join(DATA_DIR, "us", "latest.csv")
OUT_PATH = os.path.join(DATA_DIR, "brief.json")
TMP_PATH = os.path.join(DATA_DIR, ".brief.json.tmp")

MARCAP_BASE = "https://raw.githubusercontent.com/FinanceData/marcap/master/data/"
SPAC_KEYWORD = "스팩"
AMOUNT_FILTER = 1_000_000_000  # 거래대금 10억 원 이상만 표시(handoff §3)

# --- marcap parquet sanity 게이트 상수(S7 M-3: 무결성 검사 없이 read_parquet에 바로 넣던 문제 수리) ---
# 실측 근거(2026-08-10, marcap-2025.parquet/marcap-2026.parquet 실제 다운로드분 기준):
#   2025년(완결) 696,524행/242거래일 = 일평균 2,879행, 파일 25,153,419B
#   2026년(1~8월, 146거래일) 420,559행/일평균 2,881행, 파일 15,688,442B
#   단일 거래일치만 잘라 parquet로 저장하면 약 242,781B(2,903행) - 실제 최소 크기 추정 근거
#   raw.githubusercontent.com에 없는 파일을 요청하면 404 + 본문 14바이트 "404: Not Found"
#   (다만 이건 r.ok가 False라 기존 HTTP 실패 경로에서 이미 걸러짐 - 문제는 200인데 내용이 이상한 경우)
PARQUET_MAGIC = b"PAR1"  # parquet 포맷은 파일 시작·끝에 이 4바이트 매직넘버가 있다
MIN_RESPONSE_BYTES = 50_000  # 최소 거래일 1일치(~24만B)의 1/5 - 오류/점검 페이지는 통상 이보다 훨씬 작음
MAX_RESPONSE_BYTES = 150_000_000  # 실측 최대(25MB)의 6배 여유 - 메모리 폭발 방지용 상한
# compute_kr()이 실제로 쓰는 원본 컬럼만(코드 확인함, :159 cols 리스트) - Grp/VolumeTotal/AmountTotal은
# 이 함수가 파생시키는 컬럼이라 원본에 없어도 됨
REQUIRED_MARCAP_COLUMNS = {"Market", "Code", "Date", "Name", "Close", "ChangesRatio", "Volume", "Amount"}
MIN_ROWS_PER_TRADING_DAY = 2000  # 실측 일평균 ~2,880행 대비 30% 여유
COMPLETED_YEAR_MIN_TRADING_DAYS = 200  # KRX 연간 거래일수는 통상 245~250일 - 넉넉한 하한


def _min_trading_days_for_year(y, now):
    """행 수 하한 계산용 최소 거래일수. 이미 끝난 연도는 고정 하한을 쓰고, 당해년도는
    연초~어제까지의 평일수 x 0.7(공휴일·발행지연 여유)로 낮춘다 - 1월에 실행하면 당해년도
    행 수가 적은 게 정상이므로 그 사정을 반영한다."""
    if y < now.year:
        return COMPLETED_YEAR_MIN_TRADING_DAYS
    start = pd.Timestamp(year=y, month=1, day=1)
    end = pd.Timestamp(now.date()) - pd.Timedelta(days=1)
    if end < start:
        return 1
    weekdays = len(pd.bdate_range(start, end))
    return max(1, int(weekdays * 0.7))


def _sanity_check_marcap(content, y, now):
    """marcap-{y}.parquet로 받은 바이트를 채택 전에 검사한다(S7 M-3).
    통과하면 (DataFrame, None), 실패하면 (None, 사유문자열)을 반환한다 - 예외를 던지지 않고
    호출부의 기존 재시도 루프에 그대로 흡수시킨다(다운로드 실패와 동일하게 취급).
    """
    size = len(content)
    if size < MIN_RESPONSE_BYTES:
        return None, f"응답 크기 {size}B < 최소 {MIN_RESPONSE_BYTES}B(오류/점검 페이지 의심)"
    if size > MAX_RESPONSE_BYTES:
        return None, f"응답 크기 {size}B > 최대 {MAX_RESPONSE_BYTES}B(메모리 보호)"
    if content[:4] != PARQUET_MAGIC or content[-4:] != PARQUET_MAGIC:
        return None, "parquet 매직넘버(PAR1) 불일치 - HTML/점검 페이지 의심"

    try:
        frame = pd.read_parquet(io.BytesIO(content))
    except Exception as e:  # noqa: BLE001 - 파싱 실패도 검사 실패로 흡수해 재시도 루프로 넘김
        return None, f"parquet 파싱 실패: {e}"

    missing = REQUIRED_MARCAP_COLUMNS - set(frame.columns)
    if missing:
        return None, f"필수 컬럼 누락: {sorted(missing)}"

    min_days = _min_trading_days_for_year(y, now)
    min_rows = min_days * MIN_ROWS_PER_TRADING_DAY
    if len(frame) < min_rows:
        return None, f"행 수 {len(frame)} < 최소 {min_rows}({min_days}거래일 x {MIN_ROWS_PER_TRADING_DAY}행/일)"

    dates = pd.to_datetime(frame["Date"])
    out_of_year = int((dates.dt.year != y).sum())
    if out_of_year > len(frame) * 0.01:
        return None, f"Date 연도 불일치 {out_of_year}/{len(frame)}행이 {y}년 밖(파일이 바뀐 것으로 의심)"

    return frame, None


def kst_now():
    """collect.py:8-9 관례 승계 — ZoneInfo(Asia/Seoul), 기기/러너 타임존 무관."""
    return datetime.datetime.now(ZoneInfo("Asia/Seoul"))


def load_marcap(years, now):
    """전년+당해 marcap parquet을 원격에서 읽는다. 받은 바이트/프레임은 _sanity_check_marcap으로
    검사해 통과한 것만 채택한다 - "받긴 받았는데 내용이 이상함"도 다운로드 실패와 동일하게 취급해
    같은 재시도 루프에 흡수시킨다(S7 M-3). 실패 시 3회 재시도(지수 백오프) 후 예외 전파."""
    frames = []
    for y in years:
        last_err = None
        for attempt in range(3):
            try:
                r = requests.get(MARCAP_BASE + f"marcap-{y}.parquet", timeout=180)
                if r.ok:
                    frame, reason = _sanity_check_marcap(r.content, y, now)
                    if frame is not None:
                        frames.append(frame)
                        last_err = None
                        break
                    last_err = Exception(f"marcap-{y}.parquet 검사 실패: {reason}")
                else:
                    last_err = Exception(f"marcap-{y}.parquet HTTP {r.status_code}")
            except Exception as e:  # noqa: BLE001 - 재시도 후 최종 예외 전파
                last_err = e
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
        if last_err is not None and not frames:
            # 해당 연도 완전 실패 + 아직 아무 프레임도 없으면 계속 다음 연도 시도(당해년도가 더 중요)
            print(f"[경고] marcap-{y}.parquet 로드 실패(재시도 3회 소진): {last_err}")
    if not frames:
        raise RuntimeError("marcap parquet을 전년/당해 어느 쪽도 로드하지 못함")
    return pd.concat(frames)


def load_repo_csvs(marcap_max, names):
    """체크아웃된 data/*.csv를 스캔해 marcap 최신일보다 새 파일만 사용한다(HTTP 재다운로드 금지).

    폴백 순서: CSV Name → marcap Name → Code. 'Name' 컬럼이 있는 CSV와 없는 CSV가
    섞여도 동작해야 한다(FN-011 이전/이후 혼재 기간).
    """
    repo_frames = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.csv"))):
        stem = os.path.splitext(os.path.basename(path))[0]
        if len(stem) != 8 or not stem.isdigit():
            continue
        try:
            day_ts = pd.Timestamp(stem)
        except ValueError:
            continue
        if day_ts <= marcap_max:
            continue
        t = pd.read_csv(path, dtype={"Code": str})
        t["Date"] = day_ts
        repo_frames.append(t)

    if not repo_frames:
        return None, []

    rp = pd.concat(repo_frames)
    rp["Code"] = rp["Code"].astype(str).str.zfill(6)
    for c in ["VolumeTotal", "AmountTotal"]:
        if c not in rp.columns:
            rp[c] = float("nan")

    marcap_name = names["Name"]
    marcap_grp = names["Grp"]
    csv_name = rp["Name"].replace("", pd.NA) if "Name" in rp.columns else pd.Series(pd.NA, index=rp.index)
    rp["Name"] = csv_name.fillna(rp["Code"].map(marcap_name)).fillna(rp["Code"])
    rp["Grp"] = rp["Code"].map(marcap_grp).fillna(
        rp["Market"].map(lambda m: "코스피" if m == "KOSPI" else "코스닥")
    )

    used = sorted(rp["Date"].dt.strftime("%Y-%m-%d").unique())
    return rp, used


def _escalate(sub, surge_col="surge"):
    """임계값 에스컬레이션: +20% 시작, 50개 초과일 때만 10개 이하까지 +5%p씩(handoff §3)."""
    th = 20
    n20 = int((sub[surge_col] >= th).sum())
    if n20 > 50:
        while int((sub[surge_col] >= th).sum()) > 10:
            th += 5
    sel = sub[sub[surge_col] >= th].sort_values(surge_col, ascending=False)
    return n20, th, sel


def _kr_item(row):
    return {
        "name": str(row["Name"]),
        "code": str(row["Code"]),
        "grp": str(row["Grp"]),
        "close": int(row["Close"]),
        "change_pct": float(row["ChangesRatio"]),
        "volume_shown": int(row["VolShow"]),
        "amount_shown": int(row["AmtShow"]),
        "ratio": round(float(row["ratio"]), 1),
        "integrated": bool(row["integrated"]),
    }


def _kr_streak_item(code, row):
    # info = d.set_index('Code')를 거치므로 Code는 컬럼이 아니라 인덱스로 넘어온다.
    return {
        "name": str(row["Name"]),
        "code": str(code),
        "grp": str(row["Grp"]),
        "ratio": round(float(row["ratio"]), 1),
    }


def compute_kr():
    """handoff §3 scan.py의 한국 계산 블록을 그대로 재현한다."""
    now = kst_now()
    frames_years = (now.year - 1, now.year)
    df = load_marcap(frames_years, now)
    df = df[df["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])].copy()
    df["Code"] = df["Code"].astype(str).str.zfill(6)
    df["Grp"] = df["Market"].map(lambda m: "코스피" if m == "KOSPI" else "코스닥")
    names = df.sort_values("Date").groupby("Code")[["Name", "Grp"]].last()
    df["VolumeTotal"] = float("nan")
    df["AmountTotal"] = float("nan")
    mmax = df["Date"].max()

    rp, used = load_repo_csvs(mmax, names)
    source = "marcap_only"
    if rp is not None:
        source = "repo+marcap"
        cols = ["Code", "Name", "Grp", "Date", "Close", "ChangesRatio", "Volume", "Amount", "VolumeTotal", "AmountTotal"]
        df = pd.concat([df[cols], rp[cols]])
        df = df.drop_duplicates(["Code", "Date"], keep="last")
    print("어제데이터(저장소) 사용:", used if used else "없음 - 공개데이터셋만 사용")

    df = df.sort_values(["Code", "Date"])
    g = df.groupby("Code")
    df["avg20"] = g["Volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
    df["avg20T"] = g["VolumeTotal"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
    useT = df["avg20T"].notna() & df["VolumeTotal"].notna()
    df["integrated"] = useT
    df["ratio"] = (df["Volume"] / df["avg20"]).where(~useT, df["VolumeTotal"] / df["avg20T"])
    df["surged"] = df["ratio"] >= 1.2

    latest = df["Date"].max()
    dates = sorted(df["Date"].unique())
    d = df[df["Date"] == latest].copy()
    d["VolShow"] = d["VolumeTotal"].fillna(d["Volume"])
    d["AmtShow"] = d["AmountTotal"].fillna(d["Amount"])
    d = d[(d["avg20"] > 0) & (~d["Name"].str.contains(SPAC_KEYWORD)) & (d["AmtShow"] >= AMOUNT_FILTER) & d["ratio"].notna()]
    d["surge"] = (d["ratio"] - 1) * 100

    nT = int((df[df["Date"] == latest]["VolumeTotal"].notna()).sum())
    print("한국 기준일:", pd.Timestamp(latest).date(), "| 통합거래량 반영 종목:", nT)

    groups = {}
    for grp in ["코스피", "코스닥"]:
        dm = d[d["Grp"] == grp]
        n20, th, sel = _escalate(dm)
        print(f"[{grp}] +20% 이상 {n20}개 / 적용기준 +{th}% -> {len(sel)}개")
        key = "kospi" if grp == "코스피" else "kosdaq"
        groups[key] = {
            "over20_count": n20,
            "threshold": th,
            "items": [_kr_item(r) for _, r in sel.head(200).iterrows()],
        }

    # 연속급증(한국): 최근 5거래일 역방향 연속, 2일 이상만, 4일 이상은 4로 캡
    last5 = dates[-5:]
    piv = df[df["Date"].isin(last5)].pivot_table(index="Code", columns="Date", values="surged", aggfunc="first")
    piv = piv.reindex(columns=last5).fillna(False).astype(bool)
    info = d.set_index("Code")
    streak = {}
    for code in piv.index.intersection(info.index):
        s = 0
        for day in last5[::-1]:
            if piv.at[code, day]:
                s += 1
            else:
                break
        if s >= 2:
            streak[code] = min(s, 4)
    sdf = info.loc[list(streak.keys())].copy()
    sdf["streak"] = pd.Series(streak)
    print(
        "[한국 연속급증] "
        f"2일:{int((sdf.streak == 2).sum())}개 "
        f"3일:{int((sdf.streak == 3).sum())}개 "
        f"4일+:{int((sdf.streak == 4).sum())}개"
    )
    streak_key = {4: "4plus", 3: "3", 2: "2"}
    streaks_out = {"2": [], "3": [], "4plus": []}
    for n_ in [4, 3, 2]:
        sub = sdf[sdf.streak == n_].sort_values("ratio", ascending=False)
        streaks_out[streak_key[n_]] = [_kr_streak_item(code, r) for code, r in sub.head(100).iterrows()]

    return {
        "date": pd.Timestamp(latest).strftime("%Y-%m-%d"),
        "integrated_count": nT,
        "source": source,
        "repo_days_used": used,
        "kospi": groups["kospi"],
        "kosdaq": groups["kosdaq"],
        "streaks": streaks_out,
    }


def _us_item(row):
    return {
        "name": str(row["Name"]),
        "ticker": str(row["Ticker"]),
        "exchange": str(row["Exchange"]),
        "close": float(row["Close"]),
        "change_pct": float(row["ChangePct"]),
        "volume": int(row["Volume"]),
        "ratio": round(float(row["Ratio"]), 1),
        "streak": int(row["Streak"]),
    }


def _us_streak_item(row):
    return {
        "name": str(row["Name"]),
        "ticker": str(row["Ticker"]),
        "ratio": round(float(row["Ratio"]), 1),
        "streak": int(row["Streak"]),
    }


def _us_empty_streaks():
    return {"2": [], "3": [], "4plus": []}


def compute_us(today):
    """handoff §3 scan.py의 미국 계산 블록을 재현한다. us/latest.csv는 저장소 로컬 파일을
    그대로 읽는다(HTTP 재다운로드 아님 — 이 파일 자체가 이미 커밋된 산출물).
    실패(파일 없음·오래됨·파싱 오류)는 앱 실패가 아니라 status=partial로 흡수한다.
    """
    empty = {
        "available": False,
        "omit_reason": "missing",
        "session": None,
        "age_days": None,
        "has_exchange_split": None,
        "nyse": None,
        "nasdaq": None,
        "all": None,
        "streaks": _us_empty_streaks(),
    }

    if not os.path.exists(US_CSV_PATH):
        print("[미국] 데이터 없음 - 미국 섹션 생략")
        return empty, [{"scope": "us", "code": "US_MISSING", "message": "미국 데이터가 없습니다"}]

    try:
        us_raw = pd.read_csv(US_CSV_PATH)
        sess = str(us_raw["Date"].max())
        age = (today - datetime.date.fromisoformat(sess)).days
        if age > 4:
            print(f"[미국] 데이터가 오래됨({sess}) - 미국 섹션 생략")
            out = dict(empty)
            out["omit_reason"] = "stale"
            out["session"] = sess
            out["age_days"] = age
            out["has_exchange_split"] = None
            return out, [{"scope": "us", "code": "US_STALE", "message": f"미국 데이터가 오래됨({sess})"}]

        us = us_raw[us_raw["Date"] == sess].copy()
        us["surge"] = (us["Ratio"] - 1) * 100
        print("미국 세션일:", sess)
        has_nq = bool((us["Exchange"] == "NASDAQ").any())
        if not has_nq:
            print("(거래소 분류 없음 - 통합 표시)")

        result = {
            "available": True,
            "omit_reason": None,
            "session": sess,
            "age_days": age,
            "has_exchange_split": has_nq,
            "nyse": None,
            "nasdaq": None,
            "all": None,
            "streaks": _us_empty_streaks(),
        }

        if has_nq:
            group_specs = [("NYSE", "nyse", "미국-뉴욕"), ("NASDAQ", "nasdaq", "미국-나스닥")]
        else:
            group_specs = [(None, "all", "미국 전체")]

        for exch, key, label in group_specs:
            ue = us if exch is None else us[us["Exchange"] == exch]
            n20, th, sel = _escalate(ue)
            print(f"[{label}] +20% 이상 {n20}개 / 적용기준 +{th}% -> {len(sel)}개")
            result[key] = {
                "over20_count": n20,
                "threshold": th,
                "items": [_us_item(r) for _, r in sel.head(200).iterrows()],
            }

        uss = us[us["Streak"] >= 2]
        print(
            "[미국 연속급증] "
            f"2일:{int((uss.Streak == 2).sum())}개 "
            f"3일:{int((uss.Streak == 3).sum())}개 "
            f"4일+:{int((uss.Streak >= 4).sum())}개"
        )
        streaks_out = _us_empty_streaks()
        for n_, key in [(4, "4plus"), (3, "3"), (2, "2")]:
            sub = uss[uss.Streak >= 4] if n_ == 4 else uss[uss.Streak == n_]
            sub = sub.sort_values("Ratio", ascending=False)
            streaks_out[key] = [_us_streak_item(r) for _, r in sub.head(100).iterrows()]
        result["streaks"] = streaks_out
        return result, []
    except Exception as e:  # noqa: BLE001 - 미국 실패는 앱 전체 실패가 아님(architecture §4-1)
        print("[미국] 오류로 섹션 생략:", e)
        out = dict(empty)
        out["omit_reason"] = "error"
        return out, [{"scope": "us", "code": "US_ERROR", "message": f"미국 데이터 처리 오류: {e}"[:300]}]


def build_notes(kr, us):
    """handoff §4 하단 참고 2줄 + 조건부 문구(architecture §4-2 반영 #5)."""
    notes = [
        "한국 거래량은 정규장+시간외+넥스트레이드 통합 기준이며, 통합 이력이 쌓이는 중이라 "
        "일부 종목은 정규장 기준 자동 적용(약 한 달 내 완전 전환). 스팩·거래대금 10억 미만 제외."
    ]
    us_note = "미국은 S&P500+나스닥100 대상."
    if kr["source"] == "marcap_only":
        us_note += " 한국 데이터는 저장소 CSV 없이 공개 데이터셋만 사용했습니다(2거래일 이상 전 데이터일 수 있음)."
    notes.append(us_note)

    if us["available"]:
        if us["has_exchange_split"] is False:
            # scan.py의 print('(거래소 분류 없음 - 통합 표시)')와 동일 취지(반영 #5 ①)
            notes.append("나스닥100 분류 정보가 아직 반영되지 않아 거래소 구분 없이 통합 표시했습니다.")
        if us["has_exchange_split"] is True:
            # exchange는 실제 상장 거래소가 아니라 지수 구성 기준임을 밝힘(반영 #5 ②)
            # D-006: 거래소 라벨이 실제로 화면에 나뉘어 보일 때만 그 의미를 설명 - split=false면 노이즈
            notes.append(
                "미국 종목의 거래소 표기는 실제 상장 거래소가 아니라 지수 구성 기준입니다"
                "(S&P500 편입=NYSE, 나스닥100 편입=NASDAQ으로 표시)."
            )
    return notes[:10]


def _validate(brief):
    """api-spec.md §A-3 값 정합 12조 위반 건수를 센다(로그·자체점검용, 렌더는 막지 않음)."""
    violations = []
    kr = brief["kr"]
    us = brief["us"]

    if us["available"] is False and brief["status"] != "partial":
        violations.append("규칙1: us.available=false인데 status!=partial")
    if us["available"] is True:
        if not (us["omit_reason"] is None and us["session"] is not None and us["has_exchange_split"] is not None):
            violations.append("규칙2: us.available=true인데 omit_reason/session/has_exchange_split 위반")
    if us["has_exchange_split"] is True:
        if not (us["nyse"] is not None and us["nasdaq"] is not None and us["all"] is None):
            violations.append("규칙3: has_exchange_split=true 구조 위반")
    if us["has_exchange_split"] is False:
        if not (us["all"] is not None and us["nyse"] is None and us["nasdaq"] is None):
            violations.append("규칙4: has_exchange_split=false 구조 위반")
    if us["available"] is False:
        if not (
            us["nyse"] is None
            and us["nasdaq"] is None
            and us["all"] is None
            and us["has_exchange_split"] is None
            and all(len(us["streaks"][k]) == 0 for k in ("2", "3", "4plus"))
        ):
            violations.append("규칙5: us.available=false 구조 위반")

    for grp_name, key in [("코스피", "kospi"), ("코스닥", "kosdaq")]:
        section = kr[key]
        if section["over20_count"] < len(section["items"]):
            violations.append(f"규칙9: kr.{key}.over20_count < items.length")
        if not (section["threshold"] >= 20 and section["threshold"] % 5 == 0):
            violations.append(f"규칙8: kr.{key}.threshold 규격 위반")
        for it in section["items"]:
            if it["grp"] != grp_name:
                violations.append(f"규칙6: kr.{key} 항목 grp 불일치({it['code']})")
            if not (it["ratio"] >= 1.2 and (it["ratio"] - 1) * 100 >= section["threshold"] - 1e-9):
                violations.append(f"규칙7: kr.{key} 항목 ratio/threshold 불일치({it['code']})")

    for key in ["nyse", "nasdaq", "all"]:
        section = us.get(key)
        if section is None:
            continue
        if section["over20_count"] < len(section["items"]):
            violations.append(f"규칙9: us.{key}.over20_count < items.length")
        if not (section["threshold"] >= 20 and section["threshold"] % 5 == 0):
            violations.append(f"규칙8: us.{key}.threshold 규격 위반")
        for it in section["items"]:
            if not (it["ratio"] >= 1.2 and (it["ratio"] - 1) * 100 >= section["threshold"] - 1e-9):
                violations.append(f"규칙7: us.{key} 항목 ratio/threshold 불일치({it['ticker']})")

    if brief["generated_at"][:10] < kr["date"]:
        violations.append("규칙11: generated_at < kr.date")
    if brief["errors"] and brief["status"] != "partial":
        violations.append("규칙12: errors 비어있지 않은데 status!=partial")

    return violations


def main():
    now = kst_now()
    try:
        kr = compute_kr()
    except Exception as e:  # noqa: BLE001 - KR 실패는 파이프라인 실패(architecture §4-2 ④)
        print(f"[FATAL] 한국 계산 실패: {e}", file=sys.stderr)
        sys.exit(1)

    us, us_errors = compute_us(now.date())
    status = "ok"
    if (not us["available"]) or us_errors:
        status = "partial"

    brief = {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "status": status,
        "errors": us_errors[:5],
        "kr": kr,
        "us": us,
        "notes": build_notes(kr, us),
    }

    # ---- 자체점검 5항목 (인수조건) ----
    latest_stem_path = os.path.join(DATA_DIR, "latest_date.txt")
    with open(latest_stem_path, encoding="utf-8") as f:
        latest_stem = f.read().strip()
    latest_stem_fmt = f"{latest_stem[0:4]}-{latest_stem[4:6]}-{latest_stem[6:8]}"
    check1 = brief["schema_version"] == 1
    check2 = brief["kr"]["date"] == latest_stem_fmt
    violations = _validate(brief)
    check3 = len(violations) == 0
    # ④ 저장소 CSV 스캔이 "써야 할 날"을 빠뜨리지 않았는지 본다.
    # 목록이 비는 것 자체는 정상일 수 있다 — marcap 원격이 최신 거래일까지 이미
    # 발행하면 load_repo_csvs()의 `day_ts <= marcap_max: continue`가 전부 걸러내고,
    # 그때는 marcap 쪽이 오히려 확정치라 더 정확하다(2026-08-10 실측: 선정 종목 동일,
    # 거래량만 소폭 상향). 그래서 "비어 있으면 무조건 실패"로 두면 정상 실행에서
    # 거짓 경보가 나고, 경보가 일상이 되면 진짜 고장을 놓친다.
    # 진짜 고장은 "저장소 스캔이 깨져서 최신일이 계산에서 빠지는 것"인데, 그건
    # kr.date가 최신 CSV 날짜와 어긋나는 형태로 드러나므로 check2로 판별한다.
    repo_days_used = brief["kr"]["repo_days_used"]
    check4 = len(repo_days_used) > 0 or check2
    # handoff §3 규칙: age<=4 이면 available=true(omit_reason=None), age>4 이면 stale
    if us["age_days"] is None:
        check5 = us["available"] is False and us["omit_reason"] in ("missing", "error")
    else:
        check5 = (us["age_days"] <= 4) == (us["available"] is True)

    print("\n=== 자체점검 5항목 ===")
    print(f"① schema_version==1: {check1}")
    print(f"② kr.date({brief['kr']['date']}) == data/ 최신 CSV stem({latest_stem_fmt}): {check2}")
    print(f"③ api-spec §A-3 값 정합 12조 위반 0건: {check3} (위반 {len(violations)}건){' ' + str(violations) if violations else ''}")
    print(
        f"④ 저장소 CSV 스캔 정합(비어 있으면 marcap이 최신일을 이미 포함해야 함): "
        f"{check4} (repo_days_used={repo_days_used})"
    )
    print(f"⑤ us.available 판정이 handoff §3 age<=4 규칙과 동일: {check5} (age_days={us['age_days']}, available={us['available']})")

    if not (check1 and check2 and check3 and check4 and check5):
        # 여기서 brief.json 을 쓰지 않고 종료코드 1로 끝내는 것이 핵심이다.
        #
        # 왜 경고만 찍고 넘어가면 안 되는가: 워크플로의 "브리핑 생성 실패 확인"
        # 스텝은 이 스크립트의 종료코드만 본다. 예전처럼 stderr 경고만 내고 0으로
        # 끝나면, check2 실패(= kr.date 가 최신 CSV 와 어긋남 — INC-001 과 똑같은
        # 신호)가 그대로 커밋·push 되고 잡은 성공으로 표시된다. 재발 방지 게이트가
        # 실제로는 아무것도 막지 못하는 상태였다.
        #
        # 왜 쓰기 전에 검사하는가: 검사가 끝난 뒤에 쓰면, 실패해도 이미 파일이
        # 바뀐 뒤라 커밋 스텝(git add -A data)이 잘못된 값을 그대로 올린다.
        # 쓰지 않으면 직전 brief.json 이 남는다 — 낡았을지언정 틀리지는 않다.
        print("[실패] 자체점검 항목 중 실패가 있습니다. brief.json 을 쓰지 않고 종료합니다.",
              file=sys.stderr)
        print("       위 5항목 로그에서 False 인 항목을 확인할 것.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(TMP_PATH), exist_ok=True)
    with open(TMP_PATH, "w", encoding="utf-8") as f:
        json.dump(brief, f, ensure_ascii=False, indent=2)
    os.replace(TMP_PATH, OUT_PATH)
    print("brief.json 원자적 쓰기 완료:", OUT_PATH)


if __name__ == "__main__":
    main()
