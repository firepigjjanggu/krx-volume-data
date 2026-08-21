# 적용 대기 중인 워크플로 (MT-001 해소 후 복사)

이 폴더의 `collect.yml`·`us_collect.yml`은 **아직 동작하지 않습니다.** GitHub Actions는
`.github/workflows/` 안의 파일만 실행하므로, 여기 있는 동안은 그냥 텍스트 파일입니다.

## 왜 여기에 두었나

워크플로 파일을 고치려면 GitHub 토큰에 `workflow` 스코프가 필요한데 아직 없습니다(MT-001).
그래서 `.github/workflows/`를 건드린 커밋은 push 자체가 거부됩니다.

준비된 내용을 로컬 브랜치에만 두면 다른 사람이 볼 수 없고, 나중에 cherry-pick 할 때
그 사이 바뀐 커밋들과 충돌할 수 있습니다. 그래서 **push가 되는 경로에 최종본 그대로**
두었습니다. 스코프가 부여되면 복사 한 번으로 끝납니다.

## 지금 상태에서 무엇이 잘못돼 있나

`data/*.csv`는 매일 자동으로 쌓이는데, 그 CSV로 `data/brief.json`을 다시 만드는 단계가
**자동으로 돌지 않습니다.** 앱은 `brief.json`만 읽으므로, 그 파일의 `kr.date`가 5일 이상
묵으면 앱 화면의 한국 섹션이 실제 휴장이 아닌데도 "연휴/휴장" 안내로 덮입니다.

2026-08-16~08-20에 실제로 발생했습니다(`.forge/state.json`의 INC-001). 그때까지는
`python refresh_brief.py`를 사람이 주기적으로 돌려서 막고 있습니다.

## 적용 절차

```
# 1. 스코프 부여 (브라우저 승인 필요 — 사람이 직접)
"D:/Programs/GitHub CLI/gh.exe" auth refresh -h github.com -s workflow

# 2. 부여 확인 — 출력에 workflow 가 보여야 함
"D:/Programs/GitHub CLI/gh.exe" auth status

# 3. 복사
cd D:/projects/krx-volume-data
cp ops/workflows-pending/collect.yml     .github/workflows/collect.yml
cp ops/workflows-pending/us_collect.yml  .github/workflows/us_collect.yml

# 4. 커밋·push
git add .github/workflows
git commit -m "ci: make_brief 스텝 통합 + H-1 공급망 하드닝 (MT-001 해소)"
git push

# 5. 수동 1회 실행
"D:/Programs/GitHub CLI/gh.exe" workflow run collect.yml
"D:/Programs/GitHub CLI/gh.exe" workflow run us_collect.yml

# 6. 실행 결과 확인 (성공인지, 어디서 멈췄는지)
"D:/Programs/GitHub CLI/gh.exe" run list --limit 5
```

## 6번에서 반드시 눈으로 볼 것

| 확인할 것 | 왜 |
|---|---|
| `generated_at`이 실행 시각 근처로 갱신됐는가 | 이게 안 바뀌면 make_brief 스텝이 여전히 안 도는 것 |
| `pip install -r requirements.txt`가 통과했는가 | `yfinance`·`lxml` 고정 버전은 **아직 실환경에서 안 돌려봤습니다**(requirements.txt 주석 참조). 여기서 처음 검증됩니다 |
| `us_collect.py`가 정상 종료했는가 | 위와 같은 이유 |
| 커밋 스텝이 push까지 마쳤는가 | `persist-credentials: false`로 바꿨기 때문에 토큰 전달 방식이 달라졌습니다. 여기서 처음 검증됩니다 |

확인 명령:

```
curl -H "Cache-Control: no-cache" https://raw.githubusercontent.com/firepigjjanggu/krx-volume-data/main/data/brief.json
```

## 이 파일들이 origin과 다른 점

| 항목 | 지금(origin) | 이 폴더 | 이유 |
|---|---|---|---|
| make_brief 실행 | 없음 | 있음 | 이게 없어서 brief.json이 안 갱신됨 (핵심) |
| 브리핑 실패 처리 | 해당 없음 | `continue-on-error` + 마지막에 잡 실패 | 수집 결과는 살리되 실패를 조용히 넘기지 않음 |
| 패키지 설치 | `pip install requests pandas` (버전 없음) | `pip install -r requirements.txt` | H-1 — 버전이 조용히 바뀌지 않게 |
| 액션 버전 | `@v4`, `@v5` (태그) | 커밋 SHA 고정 | H-1 — 태그는 같은 이름으로 다른 커밋을 가리키게 바뀔 수 있음 |
| 체크아웃 토큰 | 기본값(`.git/config`에 저장됨) | `persist-credentials: false` | H-1 — 서드파티 파이썬 코드가 도는 동안 토큰을 두지 않음. push는 마지막 스텝에서 명시적으로 |
| 권한 범위 | 워크플로 전역 `contents: write` | 전역 `{}` + 잡 단위 `contents: write` | H-1 — 최소 권한 |

## 아직 안 한 것

`probe.yml`(1회성 데이터 탐사용, 수동 실행 전용) 삭제도 MT-001에 묶여 있습니다.
스케줄이 없어 저절로 돌지는 않으므로 급하지 않습니다. 위 3번에서 함께 지우려면:

```
git rm .github/workflows/probe.yml
```
