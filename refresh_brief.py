# brief.json 수동 갱신 원클릭 도구 (D-021 완화책 자동화)
#
# 왜 필요한가:
#   origin 워크플로에 make_brief 실행 스텝이 없다(MT-001 — 토큰에 workflow 스코프가
#   없어 .github/workflows 를 갱신하지 못함). 그래서 data/*.csv 는 매일 자동으로
#   쌓이는데 data/brief.json 은 갱신되지 않는다. 앱은 brief.json 만 읽으므로
#   kr.date 가 5일 경과하는 순간부터 한국 섹션이 "연휴/휴장" 안내로 덮인다.
#   실제로 2026-08-16~08-20 사이 오표시가 발생했다(8/11 데이터에서 정체).
#
#   MT-001 이 풀릴 때까지는 사람이 주기적으로 이 스크립트를 돌려야 한다.
#   최소 5일에 한 번, 권장은 거래일마다.
#
# 하는 일:
#   ① git pull --rebase  ② make_brief.py 실행  ③ 결과 검증  ④ 커밋·push
#   검증에서 걸리면 커밋하지 않고 사유를 출력한 뒤 종료코드 1로 끝난다.
#
# 사용법:
#   python refresh_brief.py            # 검증 통과 시 커밋·push까지
#   python refresh_brief.py --dry-run  # 재생성·검증만 하고 커밋하지 않음

import argparse
import contextlib
import datetime
import json
import os
import subprocess
import sys

# 표준출력이 cp949(윈도우 기본)면 '—' 같은 문자에서 UnicodeEncodeError로 죽는다.
# verify_brief.py에서 같은 사고를 겪고 고쳤는데 이 파일에서 그대로 재현했다
# (2026-08-20, --dry-run 테스트가 잡아냄). 도구가 자기 출력 문자 때문에 죽으면 안 된다.
with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
BRIEF = os.path.join(HERE, "data", "brief.json")

# 검증 기준값 — 근거는 이 파일 하단 「검증 기준의 근거」 주석 참조
MAX_KR_ELAPSED_DAYS = 5      # 앱이 휴장 안내로 전환하는 경과일(FN-008)
CLOSE_TOLERANCE = 0.51       # 종가 비교 허용 오차(원). 출처가 달라도 종가는 같아야 한다
VOLUME_UNDERSHOOT_PCT = -0.5 # marcap 확정치가 CSV 스냅샷보다 작으면 이상 신호


def run(cmd, **kw):
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=HERE, **kw)


def fail(msg):
    print(f"\n[중단] {msg}", file=sys.stderr)
    sys.exit(1)


def validate():
    """재생성된 brief.json을 원천 데이터와 대조한다(D-021 절차).

    verify_brief.py의 G1 대조는 scan_ref.py가 FN-011 이후 CSV의 Name 컬럼과
    충돌해 더 이상 살아 있는 참조를 만들지 못한다. 그래서 여기서는 원천 CSV와
    직접 대조한다.
    """
    import pandas as pd

    with open(BRIEF, encoding="utf-8") as f:
        b = json.load(f)

    kr_date = b["kr"]["date"]
    elapsed = (datetime.date.today() - datetime.date.fromisoformat(kr_date)).days
    print(f"\n  kr.date={kr_date} (오늘 기준 {elapsed}일 경과)")
    if elapsed >= MAX_KR_ELAPSED_DAYS:
        fail(
            f"재생성했는데도 kr.date가 {elapsed}일 경과다 — 수집 워크플로가 멈췄을 수 있다. "
            "GitHub Actions 실행 이력을 확인할 것"
        )

    csv_path = os.path.join(HERE, "data", kr_date.replace("-", "") + ".csv")
    if not os.path.exists(csv_path):
        fail(f"kr.date에 해당하는 CSV가 없다: {csv_path}")

    csv = pd.read_csv(csv_path, dtype={"Code": str})
    csv["Code"] = csv["Code"].str.zfill(6)
    idx = csv.set_index("Code")
    uses_repo = bool(b["kr"]["repo_days_used"])
    print(f"  거래량 출처: {'저장소 CSV' if uses_repo else 'marcap 확정치'}"
          f" (repo_days_used={b['kr']['repo_days_used']})")

    problems = []
    for mk in ("kospi", "kosdaq"):
        items = b["kr"][mk]["items"]
        if not items:
            problems.append(f"{mk} 항목이 비어 있다")
            continue
        for it in items:
            if it["code"] not in idx.index:
                problems.append(f"{mk} {it['code']}: CSV에 없는 코드")
                continue
            r = idx.loc[it["code"]]
            # ① 종가는 출처가 달라도 같아야 한다 — 다르면 다른 날/다른 종목을 읽은 것
            if abs(float(r["Close"]) - it["close"]) > CLOSE_TOLERANCE:
                problems.append(f"{mk} {it['code']} 종가 불일치: brief={it['close']} csv={r['Close']}")
            # ② 종목명이 코드 폴백이면 이름 조인이 깨진 것
            if it["name"] == it["code"] or it["name"].isdigit():
                problems.append(f"{mk} {it['code']} 종목명이 코드로 폴백됨")
            # ③ 확정치가 스냅샷보다 작으면 이상 (블록딜 때문에 큰 것은 정상)
            base = int(r["VolumeTotal"]) if pd.notna(r["VolumeTotal"]) else int(r["Volume"])
            if base > 0:
                diff = (it["volume_shown"] - base) / base * 100
                if diff < VOLUME_UNDERSHOOT_PCT:
                    problems.append(
                        f"{mk} {it['code']} 거래량이 원천보다 작다({diff:.2f}%) — "
                        "확정치는 스냅샷 이상이어야 한다"
                    )
        if not all(items[i]["ratio"] >= items[i + 1]["ratio"] for i in range(len(items) - 1)):
            problems.append(f"{mk} ratio 내림차순 위반")
        print(f"  {mk}: {len(items)}건 검사")

    us = b["us"]
    if us.get("available"):
        split = us.get("has_exchange_split")
        groups = [("nyse", "NYSE"), ("nasdaq", "NASDAQ")] if split else [("all", None)]
        for key, exch in groups:
            sec = us.get(key)
            if sec is None:
                problems.append(f"us.{key} 섹션이 없다(has_exchange_split={split})")
                continue
            items = sec["items"]
            missing_streak = [i["ticker"] for i in items if not isinstance(i.get("streak"), int)]
            if missing_streak:
                problems.append(f"us.{key} streak 결손 {len(missing_streak)}건: {missing_streak[:5]}")
            if exch:
                wrong = [i["ticker"] for i in items if i.get("exchange") != exch]
                if wrong:
                    problems.append(f"us.{key} 거래소 태그 불일치 {len(wrong)}건: {wrong[:5]}")
            print(f"  us.{key}: {len(items)}건 검사")
        if split and us.get("all") is not None:
            problems.append("분리 모드인데 us.all이 null이 아니다")

    if problems:
        print("\n[검증 실패]")
        for p in problems:
            print("  -", p)
        fail(f"{len(problems)}건 — 커밋하지 않았다. 원인을 확인할 것")

    print("\n  검증 통과 — 불일치 0건")
    return kr_date


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="재생성·검증만 하고 커밋하지 않음")
    args = ap.parse_args()

    if run(["git", "pull", "--rebase", "origin", "main"]).returncode != 0:
        fail("git pull 실패 — 충돌이나 네트워크 문제를 먼저 해결할 것")

    print("\n=== make_brief.py 실행 (marcap 다운로드 포함, 수 분 소요) ===")
    if run([sys.executable, os.path.join(HERE, "make_brief.py")]).returncode != 0:
        fail("make_brief.py 실패 — 위 로그의 사유를 확인할 것")

    print("\n=== 검증 (D-021 원천 대조) ===")
    kr_date = validate()

    if args.dry_run:
        print("\n--dry-run 이므로 커밋하지 않고 끝낸다.")
        return

    if run(["git", "diff", "--quiet", "--", "data/brief.json"]).returncode == 0:
        print("\nbrief.json 내용 변화 없음 — 커밋할 것이 없다.")
        return

    run(["git", "add", "data/brief.json"])
    msg = (
        f"chore(data): brief.json {kr_date} 데이터로 갱신 (수동 완화책)\n\n"
        "MT-001 해소 전까지의 D-021 완화책 — origin 워크플로에 make_brief 스텝이\n"
        "없어 CSV만 쌓이고 brief.json이 갱신되지 않는다. refresh_brief.py로\n"
        "재생성했고 원천 CSV 대조 검증을 통과했다."
    )
    if run(["git", "commit", "-q", "-m", msg]).returncode != 0:
        fail("커밋 실패")
    if run(["git", "push", "-q"]).returncode != 0:
        fail("push 실패 — 원격 상태를 확인할 것")

    print(f"\n완료: kr.date={kr_date} 로 갱신·push 했다.")
    print("CDN 반영에 수 분 걸린다. 확인:")
    print("  curl -H 'Cache-Control: no-cache' "
          "https://raw.githubusercontent.com/firepigjjanggu/krx-volume-data/main/data/brief.json")


# 검증 기준의 근거
#   - MAX_KR_ELAPSED_DAYS=5: 앱의 연휴 판정 기준과 동일(FN-008, briefing_screen.dart).
#     재생성 후에도 5일 이상이면 브리핑이 아니라 수집이 멈춘 것이므로 구분해서 알린다.
#   - CLOSE_TOLERANCE=0.51: 종가는 정수 원 단위라 반올림 차이만 허용한다. 출처가
#     marcap이든 CSV든 같은 날 같은 종목의 종가는 일치해야 한다(2026-08-20 실측: 20/20 일치).
#   - VOLUME_UNDERSHOOT_PCT=-0.5: 상한을 두지 않는 이유 — 시간외 대량매매(블록딜)가
#     확정치에만 반영되면 스냅샷 대비 수백 %까지 커질 수 있다(2026-08-20 서울보증보험
#     031210: 45만 주 블록딜로 885% 차이, 정상). 반대로 확정치가 스냅샷보다 작으면
#     잘못된 날짜·종목을 읽었다는 신호이므로 그쪽만 막는다.

if __name__ == "__main__":
    main()
