# krx-volume-data

한국(코스피·코스닥)과 미국(S&P500+나스닥100) 시장의 거래량 급증 종목을 매일 계산해 `data/brief.json` 한 파일로 공개 배포하는 GitHub Actions 파이프라인입니다. 서버가 없습니다 — 전부 무료 Actions 실행 + GitHub 저장소 자체를 CDN(raw.githubusercontent.com)으로 씁니다.

이 저장소가 만드는 `data/brief.json`은 안드로이드 앱 **"거래량 브리핑"**(`D:\app\android\volume_briefing`, GitHub `firepigjjanggu/volume_briefing`, private)의 **유일한 데이터 원천**입니다. 앱은 이 저장소의 코드를 실행하지 않고 결과 파일만 무인증 GET으로 읽습니다. 앱 쪽 상세는 그 저장소의 `README.md`·`docs/OPERATIONS.md`를 참고하세요.

## 스크립트 역할과 실행 순서

```
collect.py (평일 22:00 KST)  ──┐
                                ├──▶ data/*.csv, data/us/latest.csv
us_collect.py (화~토 06:20 KST)┘
                                            │
                                            ▼
                                    make_brief.py  ──▶ data/brief.json
                                            │
                                            ▼
                                    verify_brief.py (수동 검증 전용, 워크플로 미포함)
```

| 스크립트 | 역할 | 비고 |
|---|---|---|
| `collect.py` | 네이버 모바일 API에서 코스피·코스닥 **전종목**의 정규장 거래량 + 통합(정규장+시간외+넥스트레이드) 거래량을 수집해 `data/{YYYYMMDD}.csv`로 저장. 장 운영시간(09~20시)에 실행되면 저장하지 않고 즉시 종료. 최근 40일 넘는 CSV는 자동 삭제 | 종목 수 1500 미만이면 수집 실패로 간주하고 저장하지 않음(`sys.exit(1)`) |
| `us_collect.py` | 위키백과(S&P500 목록 + 나스닥100 목록)와 `yfinance`로 60일치 시세를 받아 20일 평균 대비 급증(ratio≥1.2) 종목만 `data/us/latest.csv`로 저장 | 나스닥100 목록은 위키 "Nasdaq-100" 본문 표가 분리 문서로 이동한 이력이 있어 1차로 `List_of_NASDAQ-100_companies` 분리 문서, 실패 시 2차로 `api.nasdaq.com` 폴백을 시도함(둘 다 실패해도 계속 진행, 거래소 구분 없이 NYSE 단일 처리) |
| `make_brief.py` | 위 CSV들 + marcap(FinanceData 공개 parquet)을 조합해 `data/brief.json`을 원자적으로(`.brief.json.tmp` → `os.replace`) 생성. handoff 문서의 `scan.py` 계산 로직(20일 평균 `shift(1)+min_periods=15`, 통합/정규장 자동 전환, 임계 에스컬레이션, 연속급증 캡4 등)을 그대로 재현 | **현재 두 정규 워크플로 어디에도 통합돼 있지 않음**(아래 "워크플로" 절 참조) — 로컬/수동 실행 전용 |
| `verify_brief.py` | G1 게이트 검증 도구. `make_brief.py`가 만든 `data/brief.json`을 원본 계산 로직 사본(`scan_ref.py`)의 실제 실행 결과와 종목코드·정렬순서·threshold·ratio·streak·exchange 단위로 전종목 대조 | **2026-08-11부로 살아 있는 대조는 더 이상 되지 않습니다 — 아래 §수명 종료 참조.** 다른 저장소의 파일을 참조하며, 워크플로에는 포함되지 않음(수동 검증 전용) |

### `verify_brief.py`의 수명 종료 (2026-08-11)

정답 스크립트 `scan_ref.py`는 handoff 원문을 한 글자도 바꾸지 않은 사본이라 `:35`에서
`rp = rp.join(names, on='Code')`로 marcap의 이름을 조인합니다. 그런데 FN-011로 `collect.py`가
CSV에 `Name` 컬럼을 직접 넣기 시작하면서 컬럼이 겹쳐 `ValueError: columns overlap but no
suffix specified: Index(['Name'])`로 실패합니다.

즉 **새 형식 CSV가 하루라도 섞인 날부터는 살아 있는 정답을 만들 수 없습니다.** 이때 도구는
저장된 `scan_ref_output.txt`(2026-08-07분 스냅샷)로 폴백하고 입력집합 불일치를 보고합니다 —
이건 데이터 결함이 아니라 "참조를 만들지 못했다"는 정직한 신고입니다.

`scan_ref.py`를 고치면 정답이 정답이 아니게 되므로 고치지 않습니다. 대신 **원천 CSV와 직접
대조**하세요:

1. `python make_brief.py`의 자체점검 5항목이 전부 True이고 값 정합 12조 위반이 0건인지 확인
2. `data/brief.json`의 코스피·코스닥 항목별 `volume_shown`·`close`·종목명이 그날
   `data/{YYYYMMDD}.csv`의 값과 일치하는지, `ratio`가 내림차순인지 확인

2026-08-11 실측으로 20개 전 항목 불일치 0건을 확인했습니다.

## `data/` 구조

```
data/
├── {YYYYMMDD}.csv     # KR 일별 스냅샷(최근 40일 롤링). 컬럼: Code,Name*,Close,Volume,VolumeTotal,Amount,AmountTotal,Market,ChangesRatio
├── us/
│   └── latest.csv      # 최신 미국 세션 급증 종목만. 컬럼: Ticker,Name,Exchange,Date,Close,ChangePct,Volume,Avg20,Ratio,Streak
├── latest_date.txt      # 최신 KR CSV의 날짜 스탬프(YYYYMMDD)
└── brief.json             # 앱이 읽는 유일한 산출물
```

**`Name` 컬럼 주의**: `collect.py`에 `Name` 컬럼이 추가된 것은 커밋 `5178270`부터입니다. 그 이전에 수집된 CSV(실측 확인: `data/20260807.csv`를 포함한 기존 파일들)에는 `Name` 컬럼이 없습니다. `make_brief.py`의 `load_repo_csvs()`가 `CSV Name → marcap Name → Code` 3단 폴백으로 이 혼재를 처리하므로, 신구 CSV가 섞여 있어도 정상 동작합니다.

## marcap 의존과 무결성 게이트

`compute_kr()`은 marcap(`https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-{year}.parquet`, 전년+당해)을 20일 평균 거래량 계산의 기준으로 씁니다. 받은 응답은 그대로 파싱하지 않고 5중 sanity 게이트(`_sanity_check_marcap`, `make_brief.py`)를 통과해야 채택됩니다:

1. 응답 크기가 50,000바이트 미만(오류/점검 페이지 의심) 또는 150,000,000바이트 초과(메모리 보호)면 거부
2. parquet 매직바이트(`PAR1`, 파일 시작·끝 4바이트)가 없으면 거부
3. `pd.read_parquet` 파싱 자체가 실패하면 거부
4. 필수 컬럼(`Market`·`Code`·`Date`·`Name`·`Close`·`ChangesRatio`·`Volume`·`Amount`) 누락 시 거부
5. 행 수가 연간 최소 거래일수 기준 하한 미만이거나, `Date` 연도가 요청 연도와 1% 넘게 다르면 거부

거부되면 다운로드 실패와 동일하게 취급되어 기존 3회 재시도(지수 백오프) 루프로 흡수됩니다 — 예외를 새로 던지지 않습니다. 2026-08-10 S7 보안 감사(M-3)에서 "무결성 검증 없이 바로 파싱하던" 취약점을 수리하며 추가됐습니다(`.forge`(앱 저장소) `05-dev/dev-notes.md` S7 절 참조).

## 로컬에서 재현하는 방법

1. Python 3.12(로컬 실측 3.12.10, 워크플로도 `python-version: '3.12'` 지정)
2. 의존성 설치 — 스크립트별로 워크플로의 `pip install` 목록이 다릅니다(`requirements.txt` 없음, `[확인필요: 통합 여부]`):
   ```
   pip install requests pandas pyarrow          # collect.py, make_brief.py용
   pip install yfinance pandas lxml pyarrow      # us_collect.py용
   ```
   (`pyarrow`는 현재 두 워크플로의 `pip install`에는 없고, 로컬/`pending/workflow-scope` 브랜치에만 반영돼 있습니다 — `make_brief.py`를 실행하려면 반드시 직접 설치해야 합니다.)
3. `python collect.py` / `python us_collect.py` — 장 마감 후(20시 이후)에만 실제로 저장합니다. 장중에 실행하면 "장 운영시간 - 저장하지 않고 종료합니다"로 즉시 끝납니다.
4. `python make_brief.py` — `data/brief.json` 생성 후 자체점검 5항목(schema_version·kr.date 일치·값 정합 12조·저장소 CSV 스캔 정합·us.available 판정)을 콘솔에 출력합니다.
5. `python verify_brief.py` — **다른 저장소에 대한 하드코딩된 경로 의존이 있습니다.** `SCAN_REF_PATH`가 `D:\app\android\volume_briefing\.forge\05-dev\reference\scan_ref.py`를 직접 가리킵니다(원본 `scan.py` 계산 로직의 무수정 사본, 앱 저장소의 Forge 산출물로 보관됨). 두 저장소가 같은 머신에 함께 체크아웃돼 있어야 이 스크립트가 동작합니다. 그 사본이 없으면 `SCAN_REF_OUTPUT_TXT`(같은 폴더의 `scan_ref_output.txt`) 텍스트 폴백으로 카운트만 비교합니다(전수 대조는 불가).

## 워크플로 (`.github/workflows/`)

| 파일 | 트리거 | 현재 상태(origin 기준) |
|---|---|---|
| `collect.yml` | `cron: 0 13 * * 1-5`(평일 22:00 KST) + `workflow_dispatch` | 정상 자동 실행 중. `pip install requests pandas` → `python collect.py` → 커밋·push만 함(`pyarrow`·`make_brief` 미포함) |
| `us_collect.yml` | `cron: 20 21 * * 1-5` + `workflow_dispatch` | 정상 자동 실행 중. `pip install yfinance pandas lxml` → `python us_collect.py` → 커밋·push만 함 |
| `probe.yml` | `workflow_dispatch`만 | 임시 조사용 1회성 스크립트(네이버 API 필드 탐색). 삭제 대상으로 계획됐으나 아직 origin에 남아 있음 |

**⚠ 이 저장소를 처음 보는 사람이 반드시 알아야 할 사실**: GitHub 계정(`firepigjjanggu`)의 `gh` CLI 토큰에 `workflow` scope가 없어 `.github/workflows/*`를 변경하는 커밋을 push할 수 없습니다(MT-001). 그래서 위 표의 "현재 상태"는 **이미 만들어져 있는 개선분과 다릅니다.** 즉 **`data/*.csv`는 매일 자동 갱신되지만 `data/brief.json`은 자동으로 다시 계산되지 않습니다.**

적용을 기다리는 최종본은 **`ops/workflows-pending/`** 에 있습니다(같은 폴더의 `README.md`에 적용 절차와 첫 실행에서 확인할 항목이 정리돼 있습니다). GitHub Actions는 `.github/workflows/` 안의 파일만 실행하므로, 이 경로에 있는 동안에는 그냥 텍스트 파일이고 push도 허용됩니다.

해소 전까지의 완화책은 **사람이 `python refresh_brief.py`를 최소 5일에 한 번(권장: 거래일마다) 돌리는 것**입니다. 앱 쪽 영향(며칠 후 앱 화면이 "연휴/휴장"으로 덮이는 문제, INC-001)은 앱 저장소의 `docs/OPERATIONS.md`에 정리돼 있습니다.

```
gh auth refresh -h github.com -s workflow                    # 브라우저 승인 필요
cp ops/workflows-pending/collect.yml     .github/workflows/  # cherry-pick 아님 — 복사
cp ops/workflows-pending/us_collect.yml  .github/workflows/
git add .github/workflows && git commit -m "ci: make_brief 스텝 통합 + H-1 공급망 하드닝" && git push
gh workflow run collect.yml && gh workflow run us_collect.yml
```

옛 안내는 로컬 브랜치 `pending/workflow-scope`의 커밋을 `git cherry-pick` 하라고 했지만 **더 이상 그렇게 하지 마세요.** 그 커밋에는 이후 추가된 H-1 공급망 하드닝(버전 고정·액션 SHA 고정·토큰 최소화·권한 축소)이 빠져 있고, 브랜치가 이 PC에만 있어 다른 곳에서는 재현되지 않습니다.

## `[확인필요]` 목록

- `probe.yml` 삭제 재시도 시점 — `workflow` scope 해소에 종속(MT-001)
- 라이선스 — 이 저장소에 `LICENSE` 파일 없음(Glob 실측), public 저장소이나 라이선스 정책 미확정
