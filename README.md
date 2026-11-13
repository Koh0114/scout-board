# Scout Board

프리미어리그·라리가 팀을 xG·압박·수비 지표로 진단하고, 그 팀에 부족한 역할을 채울 선수를
두 리그 전체에서 찾는 스카우팅 대시보드. 데이터는 GitHub Actions가 **매일 자동으로** 다시 내려받는다.

데이터 소스는 **FotMob**이다. 2026년 10월까지는 Understat을 썼는데, 11월 1일부터 Understat이
점검 페이지만 반환하는 장애가 이어져 교체했다. 경위와 FotMob 데이터 규약은 `FOTMOB_MIGRATION.md`.

## 구조

```
scripts/fetch_fotmob.py      FotMob → data/fotmob_raw.json   (커밋 안 함)
scripts/model.py             지표·포지션 그룹·축소·역할 점수 (build와 backtest가 공유)
scripts/build.py             raw → docs/data.json + docs/index.html
scripts/backtest.py          raw → docs/backtest.json (모델 검증)
scripts/fetch_understat.py   예전 수집기. 남겨 두지만 쓰지 않는다
site/template.html           사이트 본체. 디자인·로직은 전부 여기서 고친다
docs/                        GitHub Pages가 서빙하는 폴더 (자동 생성물)
.github/workflows/           매일 06:00 KST 실행되는 갱신 워크플로
```

`docs/index.html`은 같은 폴더의 `data.json`을 fetch해서 그린다. 그래서 데이터만 갈아끼우면
사이트는 그대로 최신이 된다.

## 처음 세팅 (한 번만)

1. GitHub에서 새 저장소를 만든다 (이름 예: `scout-board`, **Public**).
   Public이어야 GitHub Pages와 Actions가 무료로 무제한 돈다.
2. **워크플로 파일을 제자리로 옮긴다.** 보안 정책 때문에 `.github/workflows/`에 직접 못 써서
   `update-data.yml`이 폴더 맨 위에 놓여 있다. 이 폴더에서 터미널(PowerShell 또는 Git Bash)을 열고:
   ```bash
   mkdir -p .github/workflows
   mv update-data.yml .github/workflows/
   ```
   (윈도우 탐색기로는 점으로 시작하는 폴더를 만들기 번거로우니 터미널로 하는 게 빠르다.)
3. 이 폴더에서:
   ```bash
   git init -b main
   git add .
   git commit -m "init: scout board"
   git remote add origin https://github.com/<네아이디>/scout-board.git
   git push -u origin main
   ```
4. 저장소 → **Settings → Pages** → Source `Deploy from a branch`,
   Branch `main` / 폴더 `/docs` → Save.
   1~2분 뒤 `https://<네아이디>.github.io/scout-board/` 에서 열린다.
5. 저장소 → **Settings → Actions → General** → 맨 아래 *Workflow permissions*에서
   **Read and write permissions** 선택 → Save.
   (워크플로가 갱신된 `data.json`을 다시 커밋해야 하므로 필요하다.)
6. **Actions** 탭 → `Update data` → **Run workflow**로 한 번 수동 실행해서 초록불 확인.

## 매일 어떻게 도는가

- 크론은 `0 21 * * *` (UTC) = **매일 06:00 KST**. GitHub 러너가 붐비면 5~40분 늦게 도는 게 정상이다.
- 워크플로가 FotMob에서 8개 시즌-리그(EPL·라리가 × 최근 4시즌)의 지표 파일과 순위표를 받아
  `docs/data.json`·`docs/backtest.json`을 다시 만들고,
  내용이 바뀌었을 때만 커밋한다. Pages는 커밋되면 자동 재배포된다.
- 데이터가 비정상적으로 적게 잡히면(팀 30개 미만, 선수 200명 미만) 커밋을 막고 워크플로가 실패한다.
  → 이전 데이터가 그대로 살아 있으니 사이트는 안 깨진다.
- **공개 저장소의 스케줄 워크플로는 60일간 저장소에 아무 활동이 없으면 자동으로 멈춘다.**
  가끔 커밋하거나, 멈췄다는 메일이 오면 Actions 탭에서 다시 켜면 된다.

## 기준 시즌이 자동으로 넘어간다

`build.py`는 현재 시즌이 **15경기 이상** 치렀으면 그 시즌을, 아니면 직전 시즌을 분석 기준으로 쓴다.
그래서 8~11월에는 지난 시즌 기준으로 보다가, 12월쯤 자동으로 새 시즌으로 넘어간다.
바꾸고 싶으면 `scripts/build.py`의 `SWITCH_AFTER` 값을 고치면 된다.

## 로컬에서 돌리기

```bash
python scripts/fetch_fotmob.py      # data/fotmob_raw.json 생성 (약 1분 30초)
python scripts/build.py             # docs/data.json, docs/index.html 갱신
python scripts/backtest.py          # docs/backtest.json 갱신
python -m http.server -d docs 8000  # http://localhost:8000
```
`file://`로 직접 열면 브라우저 CORS 정책 때문에 `data.json` fetch가 막힌다. 꼭 로컬 서버로 열 것.

## 디자인·기능을 고치고 싶으면

`site/template.html` 하나만 고치고 push하면, 워크플로가 `docs/index.html`을 다시 만들어준다.
(`docs/` 안의 파일은 직접 고치지 말 것 — 다음 갱신 때 덮어써진다.)

## Claude Code로 이어서 작업하기

```powershell
cd "$HOME\Documents\scout-board"
claude
```

`CLAUDE.md`에 구조·주의사항·검증 기준이 정리돼 있어서 세션을 새로 열어도 맥락이 이어진다.

## 지표 메모

| 지표 | 뜻 |
|---|---|
| `npxG` | 논페널티 기대득점. FotMob에 없어 `xG − 0.79 × PK골`로 근사한다 (실축 PK는 못 뺀다) |
| `xA` | 기대도움 — 내가 준 패스가 슛으로 이어졌을 때 그 슛의 xG 합 |
| 기회 창출 / 빅찬스 창출 | 슛으로 이어진 패스 수 / 결정적 기회를 만든 패스 수 |
| 성공 패스 / 성공 롱볼 | 90분당 성공 횟수 |
| 태클 / 인터셉트 / 리커버리 / 클리어 / 블록 | 수비 액션 90분당 (합계에서 계산) |
| 박스 침투 (팀) | 상대 페널티 박스 안 터치 수 / 경기 |
| 전방 압박 (팀) | 상대 파이널 서드에서 공을 뺏은 횟수 / 경기 |
| 수비 활동량 (팀) | (태클+인터셉트) / 경기 × 50 / (100 − 점유율) — 공을 덜 가진 팀이 수비 기회가 많은 편향을 보정 |

역할 점수는 출전 시간으로 축소한 지표를 후보 풀 전체 기준 z-점수로 바꿔 가중합하고,
그 풀 안 백분위 순위(0–100)로 보여준다. 역할 정의와 검증 결과는 `CLAUDE.md`와 사이트 6번 섹션.

## 한계

- FotMob은 비공식 경로다. 구조가 바뀌면 갱신이 멈춘다 (워크플로가 실패하고 이전 데이터가 남는다).
- xG 모델이 Understat과 달라 2026년 10월 이전 수치와 직접 비교할 수 없다.
- 볼터치·선수 나이·이적료가 없어 영입 현실성(나이·계약·시장가)은 판단하지 않는다.
  필요하면 Transfermarkt 같은 소스를 추가로 붙여야 한다.
- 출전 경기가 팀 경기의 절반에 못 미친 선수는 FotMob이 90분당 지표를 주지 않아 순위에서 빠진다.
- 경기별 xG 추이는 없다.

데이터 출처: [FotMob](https://www.fotmob.com) (2026년 10월까지는 [Understat](https://understat.com))
