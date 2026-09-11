# Scout Board — 프로젝트 컨텍스트

프리미어리그·라리가 팀을 xG 기반 지표로 진단하고, 그 팀에 부족한 역할을 채울 선수를
두 리그 전체에서 찾아주는 스카우팅 대시보드. GitHub Pages로 배포하고,
GitHub Actions가 매일 데이터를 다시 받아 커밋한다.

## 파이프라인

```
scripts/fetch_understat.py   understat.com/getLeagueData/{league}/{season}
                             최근 4시즌 → data/understat_raw.json   (gitignore, 3.7MB)
scripts/model.py             지표·K추정·축소·역할점수 — build와 backtest가 공유한다
scripts/build.py             raw → docs/data.json + docs/index.html
scripts/backtest.py          raw → docs/backtest.json (모델 검증)
site/template.html           사이트 본체 (HTML+CSS+JS 한 파일)
docs/                        Pages가 서빙하는 폴더 — 전부 자동 생성물
.github/workflows/           매일 06:00 KST 크론
```

`docs/index.html`은 빌드 때 `site/template.html`을 head/body로 쪼개 감싼 결과다.
**`docs/` 안의 파일은 직접 고치지 말 것** — 다음 빌드에 덮어써진다. `site/template.html`만 고친다.

## 꼭 지켜야 할 것

- **의존성 없음.** 두 스크립트 모두 파이썬 표준 라이브러리만 쓴다. Actions 러너에
  `pip install`을 넣지 않는다. 새 패키지가 필요하면 먼저 정말 필요한지 다시 생각할 것.
- **템플릿의 데이터 로딩 경로가 두 개다.** `<script id="payload">`에 JSON이 인라인돼
  있으면 그걸 쓰고, 비어 있으면 `fetch('data.json')`한다. 전자는 Claude Artifact 배포용,
  후자는 GitHub Pages용. `boot()` 함수를 고칠 때 둘 다 살아 있어야 한다.
- **`file://`로 열면 안 된다.** CORS 때문에 fetch가 막힌다. 로컬 확인은
  `python -m http.server -d docs 8000`.
- **백분위가 두 종류다.** 헷갈리면 안 된다.
  - `pc` = 같은 포지션 그룹(FW/MF/DF) 안에서의 백분위 → 선수 프로필 막대에 쓴다
  - `pa` = 후보 풀 전체를 한 기준으로 잰 백분위 → **역할 점수(`r`)는 이걸로 계산한다**
  - 역할 점수를 `pc`로 계산하면 0.49 npxG/90인 미드필더가 0.78인 홀란드를 이겨버린다.
- **기준 시즌은 자동 전환.** 현재 시즌이 `SWITCH_AFTER`(15)경기 이상이면 그 시즌,
  아니면 직전 시즌. `FORM`은 항상 현재 시즌. 하드코딩하지 말 것.
- **모델 로직은 `model.py`에만 둔다.** 백테스트는 '실제로 배포되는 모델'을 검증해야
  의미가 있다. 점수 계산을 build.py에 복사해 두면 조용히 갈라진다.
- **`pa`/`r`은 축소값(`_adj`) 기준, 표에 보이는 per-90은 원값.** 둘 다 data.json에 있다.
- **`.github/workflows/`는 원격 도구로 못 쓴다.** Claude Code에서 이 경로를 수정할 때
  거부되면 사용자에게 직접 편집을 요청한다.

## 데이터 사전 (Understat)

| 필드 | 뜻 |
|---|---|
| `npxG` | 논페널티 기대득점 |
| `xA` | 기대도움 |
| `xGChain` | 득점으로 이어진 공격 전개에 관여한 총 xG |
| `xGBuildup` | xGChain − 본인의 슛·키패스 = 순수 빌드업 기여 |
| `ppda` | 상대 진영 패스 허용량 ÷ 우리 수비 액션 → **작을수록 강한 압박** |
| `deep` | 골문 20야드 반경 안으로 들어간 패스 수 |
| `position` | `"F M S"` 같은 문자열. `S`(교체) 빼고 F>M>D>GK 순으로 대표 포지션 결정 |

**"낮을수록 좋은" 지표**(`npxGA90`, `deep_allowed90`, `ppda`)는 `TEAM_METRICS`에서
`False`로 표시돼 백분위가 `100 - p`로 뒤집힌다. 지표를 추가할 때 이 플래그를 빼먹지 말 것.

## 알려진 한계 (사이트 5번 섹션에도 명시돼 있음)

Understat은 슛 기반 데이터라 **태클·인터셉트·패스 성공률·볼터치·선수 나이가 없다.**
그래서 팀 *진단*은 수비까지 다루지만 선수 *추천*은 공격·창조 역할에 한정된다.
이 한계를 사용자에게 숨기거나 없는 지표를 지어내지 말 것.

fbref는 Cloudflare 봇 차단으로 자동 수집이 안 된다 (우회 시도 금지).
수비 지표가 필요하면 사용자가 fbref에서 직접 CSV를 받아오거나,
StatsBomb open-data / worldfootballR 미러(깃허브)를 붙이는 쪽으로 간다.

## 검증

데이터 로직을 건드렸으면 이걸로 확인한다 — 2025/26 기준 정답:

- EPL 1위 Arsenal 85점, 최하위 Wolverhampton Wanderers 20점, 총 승점 1036
- 라리가 1위 Barcelona 94점, 최하위 Real Oviedo 29점, 총 승점 1047
- 팀 득실·승점·npxG 합계는 원본 `history` 배열의 단순 합과 정확히 일치해야 한다
- 백분위 뒤집기: Arsenal 피npxG 0.87 → 97.5, Burnley 2.01 → 2.5,
  Brighton PPDA 8.88(리그 최저) → 97.5
- 모든 `pc`/`pa`/`r` 값은 0–100 범위 안

## 로드맵

작업 계획은 `ROADMAP.md`에 단계별 스펙·공식·완료 조건까지 정리돼 있다.
A~C(분석 엄밀성) → D~G(기능) → H(디자인) 순서로 진행한다.
디자인은 UI 요소가 다 나온 뒤 마지막에 한 번에 한다.

**작업 시 원칙**
- 각 단계의 완료 조건을 통과하기 전에 다음 단계로 넘어가지 않는다
- 백테스트 결과가 나쁘게 나오면 나쁘게 보고한다. 모델을 좋아 보이게 만들지 않는다
- 표본이 부족하면 보정하지 않고 "확인 못 했다"고 표시한다
- 없는 지표를 지어내거나 한계를 숨기지 않는다
