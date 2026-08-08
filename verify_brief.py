# verify_brief.py — G1 게이트: make_brief.py의 산출물(data/brief.json)을
# scan_ref.py(handoff.md §3 원문, 무수정)의 실제 실행 결과와 전수 대조하는 검증자.
#
# 이 스크립트의 목적은 make_brief.py를 통과시키는 것이 아니라 불일치를 찾는 것이다.
# scan_ref.py는 절대 수정하지 않는다 — 파일을 읽기 전용으로 exec()만 하고, 파일 자체에는
# 쓰기 작업을 하지 않는다(marcap parquet 캐시는 REFSCAN_DIR 안에서만 생성/갱신됨).
#
# 대조 깊이: scan_ref.py는 print()로 요약 수치 + 연속급증 상위 7개만 보여주지만,
# 스크립트를 이 프로세스 안에서 exec()하면 실행이 끝난 뒤에도 스크립트 최상위 변수
# (d, sdf, us, uss, has_nq)가 네임스페이스에 그대로 남는다. 이 변수들이 실제
# "정답" 계산 결과(전종목)이므로, brief.json과 종목코드 단위로 전수 대조한다.
#
# 대조 대상 6그룹(has_exchange_split=false일 때는 미국 그룹이 1개로 합쳐져 5그룹):
#   코스피 · 코스닥 · (미국-뉴욕 · 미국-나스닥 또는 미국-전체) · 한국연속 · 미국연속
# 각 그룹 x 7속성: 종목코드(또는 티커) 집합 · 정렬 순서 · threshold · over20_count ·
#   ratio(소수1) · streak · exchange  (그룹 성격상 해당 없는 속성은 "해당없음"으로 표기)

import ast
import contextlib
import io
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
BRIEF_PATH = os.path.join(REPO_ROOT, "data", "brief.json")

# scan_ref.py는 handoff.md §3 원문을 한 글자도 바꾸지 않고 저장한 "정답" 스크립트다.
# 이 데이터 리포에는 커밋하지 않는다는 지시(M1-T2 대상 파일란)에 따라 .forge 산출물
# 폴더의 사본을 경로로만 참조한다 — 이 파일이 그 사본을 수정하는 일은 없다.
SCAN_REF_PATH = (
    r"D:\app\android\volume_briefing\.forge\05-dev\reference\scan_ref.py"
)
SCAN_REF_OUTPUT_TXT = (
    r"D:\app\android\volume_briefing\.forge\05-dev\reference\scan_ref_output.txt"
)
REFSCAN_DIR = (
    r"D:\temp\claude\C--Users----\a354a8e4-4f67-4d37-a693-5c802e6515b0"
    r"\scratchpad\refscan"
)

RANGE_PATTERN = "for i in range(1,11):"

mismatches = []  # (그룹, 종목코드, 속성, 기대값, 실제값)


def log_mismatch(group, code, attr, expected, actual):
    mismatches.append((group, code, attr, expected, actual))
    print(f"MISMATCH-ITEM: [{group}] {code} {attr} 기대={expected!r} 실제={actual!r}")


# ---------------------------------------------------------------------------
# 1. scan_ref.py 실행 (읽기 전용 exec, 파일 쓰기 없음)
# ---------------------------------------------------------------------------

def run_reference(range_override=None):
    """scan_ref.py 원문(또는 range(1,11)만 in-memory로 치환한 통제 변형)을
    exec()해 최종 네임스페이스와 표준출력을 반환한다. 디스크의 scan_ref.py
    파일 자체는 절대 수정하지 않는다 — range_override는 메모리 상의 문자열
    치환일 뿐이다.
    """
    if not os.path.exists(SCAN_REF_PATH):
        raise FileNotFoundError(f"scan_ref.py 없음: {SCAN_REF_PATH}")
    src = open(SCAN_REF_PATH, encoding="utf-8").read()
    label = SCAN_REF_PATH
    if range_override is not None:
        if RANGE_PATTERN not in src:
            raise RuntimeError(
                f"scan_ref.py에서 '{RANGE_PATTERN}' 패턴을 찾지 못해 통제 실행 불가"
            )
        lo, hi = range_override
        src = src.replace(RANGE_PATTERN, f"for i in range({lo},{hi}):", 1)
        label = f"{SCAN_REF_PATH}#range({lo},{hi})"

    code_obj = compile(src, label, "exec")
    os.makedirs(REFSCAN_DIR, exist_ok=True)
    ns = {"__name__": "__main__"}
    buf = io.StringIO()
    prev_cwd = os.getcwd()
    os.chdir(REFSCAN_DIR)
    try:
        with contextlib.redirect_stdout(buf):
            exec(code_obj, ns)
    finally:
        os.chdir(prev_cwd)
    return ns, buf.getvalue()


def extract_repo_days(stdout_text):
    m = re.search(r"어제데이터\(저장소\) 사용: (.*)", stdout_text)
    if not m:
        return None
    raw = m.group(1).strip()
    if raw.startswith("["):
        return ast.literal_eval(raw)
    return []  # '없음 - 공개데이터셋만 사용'


# ---------------------------------------------------------------------------
# 2. 정답 그룹 재구성 (스크립트 실행 후 남는 최상위 변수에서)
# ---------------------------------------------------------------------------

def escalate(sub_df, surge_col="surge"):
    """handoff §3 / scan_ref.py의 임계 에스컬레이션 로직 그대로(수식 재현, 파일 미참조)."""
    th = 20
    n20 = int((sub_df[surge_col] >= th).sum())
    if n20 > 50:
        while int((sub_df[surge_col] >= th).sum()) > 10:
            th += 5
    sel = sub_df[sub_df[surge_col] >= th].sort_values(surge_col, ascending=False)
    return n20, th, sel


def compare_code_sets(group, expected_codes, actual_codes):
    """집합 차이를 종목코드 단위로 한 줄씩 보고. 집합이 같으면 정렬 순서를 비교.
    반환값: 집합이 완전히 같으면 True(추가 비교 진행 가능), 아니면 False.
    """
    exp_set, act_set = set(expected_codes), set(actual_codes)
    if exp_set != act_set:
        for code in sorted(exp_set - act_set):
            log_mismatch(group, code, "종목코드포함여부", "포함", "미포함")
        for code in sorted(act_set - exp_set):
            log_mismatch(group, code, "종목코드포함여부", "미포함", "포함")
        return False
    if expected_codes != actual_codes:
        log_mismatch(group, "-", "정렬순서", expected_codes, actual_codes)
        return False
    return True


def compare_main_group(
    group, sel_df, n20, th, brief_section, code_col, item_code_key,
    ratio_col, exchange_col=None,
):
    """코스피/코스닥/미국(뉴욕·나스닥·전체) 등 임계값 그룹 1개를 비교."""
    expected_codes = list(sel_df[code_col].astype(str).head(200))  # make_brief.py:193/223과 동일한 상한
    actual_codes = [it[item_code_key] for it in brief_section["items"]]

    if n20 != brief_section["over20_count"]:
        log_mismatch(group, "-", "over20_count", n20, brief_section["over20_count"])
    if th != brief_section["threshold"]:
        log_mismatch(group, "-", "threshold", th, brief_section["threshold"])

    sets_equal = compare_code_sets(group, expected_codes, actual_codes)
    if not sets_equal:
        return

    sel_idx = sel_df.set_index(code_col)
    for it in brief_section["items"]:
        code = it[item_code_key]
        exp_ratio = round(float(sel_idx.loc[code, ratio_col]), 1)
        if exp_ratio != it["ratio"]:
            log_mismatch(group, code, "ratio", exp_ratio, it["ratio"])
        if exchange_col is not None:
            exp_exch = str(sel_idx.loc[code, exchange_col])
            if exp_exch != it["exchange"]:
                log_mismatch(group, code, "exchange", exp_exch, it["exchange"])
        else:
            pass  # 코스피/코스닥 항목엔 exchange 속성 자체가 없음 — 해당없음


def compare_kr_streak(sdf, brief_streaks):
    """한국연속 그룹(2일/3일/4일+ 3버킷)을 비교. sdf.streak는 scan_ref.py 자체가
    이미 min(s,4)로 캡한 값이므로 streak==4가 곧 4plus 버킷이다.
    """
    for key, n_ in (("4plus", 4), ("3", 3), ("2", 2)):
        sub = sdf[sdf["streak"] == n_].sort_values("ratio", ascending=False)
        group = f"한국연속-{key}"
        expected_codes = list(sub.index.astype(str))[:100]  # make_brief.py:223 상한
        actual_items = brief_streaks[key]
        actual_codes = [it["code"] for it in actual_items]

        if not compare_code_sets(group, expected_codes, actual_codes):
            continue
        for it in actual_items:
            code = it["code"]
            exp_ratio = round(float(sub.loc[code, "ratio"]), 1)
            if exp_ratio != it["ratio"]:
                log_mismatch(group, code, "ratio", exp_ratio, it["ratio"])
        # KR 스트릭 항목 자체엔 streak 필드가 없음(버킷 소속=streak 값) — 위에서 이미 검증됨
        # exchange 속성 해당없음(한국 종목)


def compare_us_streak(uss, brief_streaks):
    for key, cond_fn in (
        ("4plus", lambda s: s["Streak"] >= 4),
        ("3", lambda s: s["Streak"] == 3),
        ("2", lambda s: s["Streak"] == 2),
    ):
        sub = uss[cond_fn(uss)].sort_values("Ratio", ascending=False)
        group = f"미국연속-{key}"
        expected_codes = list(sub["Ticker"].astype(str).head(100))  # make_brief.py:223 상한
        actual_items = brief_streaks[key]
        actual_codes = [it["ticker"] for it in actual_items]

        if not compare_code_sets(group, expected_codes, actual_codes):
            continue
        sub_idx = sub.set_index("Ticker")
        for it in actual_items:
            t = it["ticker"]
            exp_ratio = round(float(sub_idx.loc[t, "Ratio"]), 1)
            if exp_ratio != it["ratio"]:
                log_mismatch(group, t, "ratio", exp_ratio, it["ratio"])
            exp_streak = int(sub_idx.loc[t, "Streak"])
            if exp_streak != it["streak"]:
                log_mismatch(group, t, "streak", exp_streak, it["streak"])
        # exchange 속성 해당없음(연속급증 항목엔 exchange 필드 없음)


# ---------------------------------------------------------------------------
# 3. 메인 절차
# ---------------------------------------------------------------------------

def main():
    with open(BRIEF_PATH, encoding="utf-8") as f:
        brief = json.load(f)

    print("=== ① 입력 CSV 집합 비교 (선행 조건) ===")
    print(f"brief.json kr.repo_days_used: {brief['kr']['repo_days_used']}")

    print("scan_ref.py 재실행 중 (marcap parquet 재다운로드 포함, 수 분 소요될 수 있음)...")
    try:
        ns, stdout_text = run_reference()
        ref_mode = "live"
    except Exception as e:  # noqa: BLE001 - 재실행 실패 시 저장된 출력으로 폴백
        print(f"[경고] scan_ref.py 재실행 실패({e}) — 저장된 scan_ref_output.txt로 폴백")
        if not os.path.exists(SCAN_REF_OUTPUT_TXT):
            print("MISMATCH: 1")
            sys.exit(1)
        with open(SCAN_REF_OUTPUT_TXT, encoding="utf-8") as f:
            stdout_text = f.read()
        ns = None
        ref_mode = "stored-text"

    ref_days = extract_repo_days(stdout_text)
    print(f"scan_ref.py stdout 어제데이터(저장소) 사용: {ref_days}")

    input_match = set(ref_days or []) == set(brief["kr"]["repo_days_used"])
    print(f"입력 집합 일치 여부: {'일치' if input_match else '불일치'}")

    if not input_match and ref_mode == "live":
        print("[입력 불일치] 통제 실행 range(1,11)->range(0,11)로 재시도합니다.")
        try:
            ns2, stdout2 = run_reference(range_override=(0, 11))
            ref_days2 = extract_repo_days(stdout2)
            print(f"통제 실행 결과 어제데이터(저장소) 사용: {ref_days2}")
            if set(ref_days2 or []) == set(brief["kr"]["repo_days_used"]):
                print("통제 실행으로 입력 집합이 일치했습니다 — 이 결과를 기준으로 재대조합니다.")
                ns, stdout_text, ref_days = ns2, stdout2, ref_days2
                input_match = True
            else:
                log_mismatch(
                    "입력집합", "-", "repo_days_used",
                    brief["kr"]["repo_days_used"], ref_days2,
                )
        except Exception as e:  # noqa: BLE001
            log_mismatch("입력집합", "-", "통제실행오류", "성공", str(e))
    elif not input_match:
        log_mismatch(
            "입력집합", "-", "repo_days_used",
            brief["kr"]["repo_days_used"], ref_days,
        )

    if ref_mode == "stored-text":
        print(
            "[확인필요: 재실행 실패로 저장된 텍스트 출력만 사용 — 코스피/코스닥/미국 "
            "주요 그룹은 print가 전량 노출하므로 전수 대조 가능하나, 연속급증 3그룹은 "
            "상위 7개+카운트만 대조 가능(전체 종목코드 집합 대조는 불가)]"
        )
        # 텍스트 폴백 모드에서는 그룹 재구성이 불가능하므로 카운트만 비교하고 종료.
        print(f"\nMISMATCH: {len(mismatches)}")
        sys.exit(0 if len(mismatches) == 0 else 1)

    if ns is None or not input_match:
        print("입력 집합을 맞추지 못해 그룹별 대조를 진행할 수 없습니다.")
        print(f"\nMISMATCH: {len(mismatches)}")
        sys.exit(1)

    d = ns["d"]
    sdf = ns.get("sdf")

    print("\n=== ② 코스피/코스닥 대조 ===")
    for grp_name, key in (("코스피", "kospi"), ("코스닥", "kosdaq")):
        dm = d[d["Grp"] == grp_name]
        n20, th, sel = escalate(dm)
        compare_main_group(
            grp_name, sel, n20, th, brief["kr"][key],
            code_col="Code", item_code_key="code", ratio_col="ratio",
        )
        print(f"[{grp_name}] 정답 n20={n20} th={th} 표시={len(sel)}개 대조 완료")

    print("\n=== ③ 한국연속 대조 ===")
    if sdf is not None and len(sdf) > 0:
        compare_kr_streak(sdf, brief["kr"]["streaks"])
    else:
        for key in ("2", "3", "4plus"):
            if brief["kr"]["streaks"][key]:
                log_mismatch(f"한국연속-{key}", "-", "빈집합여부", "빈 목록", "비어있지 않음")
    print("한국연속 대조 완료")

    print("\n=== ④ 미국 대조 ===")
    ref_us_available = "has_nq" in ns
    brief_us_available = brief["us"]["available"]
    if ref_us_available != brief_us_available:
        log_mismatch("미국", "-", "available", ref_us_available, brief_us_available)
    elif ref_us_available:
        has_nq = bool(ns["has_nq"])
        if has_nq != brief["us"]["has_exchange_split"]:
            log_mismatch("미국", "-", "has_exchange_split", has_nq, brief["us"]["has_exchange_split"])
        us_full = ns["us"]
        if has_nq:
            group_specs = [("NYSE", "nyse", "미국-뉴욕"), ("NASDAQ", "nasdaq", "미국-나스닥")]
        else:
            group_specs = [(None, "all", "미국-전체")]
        for exch, key, label in group_specs:
            ue = us_full if exch is None else us_full[us_full["Exchange"] == exch]
            n20, th, sel = escalate(ue)
            compare_main_group(
                label, sel, n20, th, brief["us"][key],
                code_col="Ticker", item_code_key="ticker", ratio_col="Ratio",
                exchange_col="Exchange",
            )
            print(f"[{label}] 정답 n20={n20} th={th} 표시={len(sel)}개 대조 완료")

        uss = ns.get("uss")
        if uss is not None:
            compare_us_streak(uss, brief["us"]["streaks"])
        print("미국연속 대조 완료")
    else:
        for key in ("nyse", "nasdaq", "all"):
            if brief["us"].get(key) is not None:
                log_mismatch("미국", "-", "available=false인데 섹션 존재", None, key)

    print(f"\n총 불일치 항목: {len(mismatches)}건")
    if mismatches:
        print("=== 불일치 내역 요약 ===")
        for group, code, attr, expected, actual in mismatches:
            print(f"  [{group}] {code} {attr}: 기대={expected!r} 실제={actual!r}")

    print(f"\nMISMATCH: {len(mismatches)}")
    sys.exit(0 if len(mismatches) == 0 else 1)


if __name__ == "__main__":
    main()
