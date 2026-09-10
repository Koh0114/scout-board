# Scout Board

프리미어리그·라리가 팀을 xG 기반 지표로 진단하고, 그 팀에 부족한 역할을 채울 선수를
두 리그 전체에서 찾는 스카우팅 대시보드. 데이터는 GitHub Actions가 **매일 자동으로** 다시 내려받는다.

## 구조

```
scripts/fetch_understat.py   Understat → data/understat_raw.json   (커밋 안 함)
scripts/build.py             raw → docs/data.json + docs/index.html
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
- 워크플로가 Understat에서 6개 시즌-리그(EPL·라리가 × 최근 3시즌)를 받아 `docs/data.json`을 다시 만들고,
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
python scripts/fetch_understat.py   # data/understat_raw.json 생성
python scripts/build.py             # docs/ 갱신
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
| `npxG` | 논페널티 기대득점 — 슛의 위치·상황으로 계산한 "들어갈 법한" 골 수 |
| `xA` | 기대도움 — 내가 준 패스가 슛으로 이어졌을 때 그 슛의 xG 합 |
| `xGChain` | 득점으로 이어진 공격 전개에 관여한 총 xG |
| `xGBuildup` | xGChain에서 본인의 슛·키패스를 뺀 값 = 순수 빌드업 기여 |
| `PPDA` | 상대 진영 패스 허용량 ÷ 우리 수비 액션 → **작을수록 강한 압박** |
| `deep` | 골문 20야드 반경 안으로 들어간 패스 수 |

## 한계

Understat은 슛 기반 데이터라 **태클·인터셉트·패스 성공률·볼터치·선수 나이가 없다.**
그래서 팀 *진단*은 수비까지 다루지만 선수 *추천*은 공격·창조 역할에 한정된다.
수비수 스카우팅이나 영입 현실성(나이·이적료)까지 하려면 FBref(worldfootballR)나
StatsBomb open-data, Transfermarkt를 추가로 붙여야 한다.

데이터 출처: [Understat](https://understat.com)
