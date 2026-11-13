# FotMob 이전 스펙

Understat이 2026-11-01부터 점검 페이지만 반환해서 매일 갱신이 12일째 실패 중이다.
데이터 소스를 FotMob으로 바꾼다. 이 문서의 "확인됨" 항목은 2026-11-13에 브라우저에서
직접 호출해 본 결과이고, "미확인" 항목은 작업하면서 먼저 검증해야 한다.

---

## 0. 먼저 할 일 — 접근성 검증

아래 둘은 **브라우저에서만** 확인했다. 파이썬(urllib)과 GitHub Actions 러너에서 열리는지는 모른다.

| 주소 | 용도 | 브라우저 | 파이썬·Actions |
|---|---|---|---|
| `https://data.fotmob.com/stats/...` | 선수·팀 지표 파일 | 확인됨 | 미확인 |
| `https://www.fotmob.com/api/data/leagues?id=87` | 시즌 ID 목록, 순위표, 경기 결과 | 확인됨 | 미확인 |

1. 로컬에서 urllib로 두 주소를 호출해 본다.
2. `workflow_dispatch`로 Actions에서도 호출해 본다.
3. `data.fotmob.com`이 막히면 이 이전 자체가 성립하지 않는다. 멈추고 보고한다.
4. `www.fotmob.com/api`만 막히면 4절의 대체 경로를 쓴다.

차단을 우회하려고 헤더를 위조하거나 봇 검사를 피하는 코드는 쓰지 않는다. 막히면 막힌 것이다.

---

## 1. 지표 파일

```
https://data.fotmob.com/stats/{리그ID}/season/{시즌ID}/{지표명}.json
```

### 시즌 ID (확인됨)

| 시즌 | 프리미어리그 (47) | 라리가 (87) |
|---|---|---|
| 2026/27 | 36781 | 38843 |
| 2025/26 | 27110 | 27233 |
| 2024/25 | 23685 | 23686 |
| 2023/24 | 20720 | 21053 |

새 시즌 ID는 `api/data/leagues?id=` 응답의 `stats.seasonStatLinks`에 있다(`Name`, `TournamentId`).
API가 파이썬에서 안 열리면 이 표를 코드에 넣고 시즌이 바뀔 때 손으로 추가한다.

### 파일 구조 (확인됨)

```json
{"LeagueName": "...", "TopLists": [{"Title": "...", "Subtitle": "...", "StatList": [
  {"ParticiantId": 701154, "ParticipantName": "Kylian Mbappé", "TeamId": 8633,
   "TeamName": "Real Madrid", "Positions": [115, 87], "ParticipantCountryCode": "FRA",
   "MinutesPlayed": 2604, "MatchesPlayed": 31,
   "StatValue": 23.9, "SubStatValue": 25, "Rank": 1}
]}]}
```

- 키 이름이 `ParticiantId`다(오타가 원본). 선수 조인은 이 ID로 한다. 이름으로 조인하지 않는다.
- 상위 N명이 아니라 조건을 넘는 전체 선수가 들어 있다.
- `data.fotmob.com`은 요청 헤더와 상관없이 항상 `Content-Encoding: gzip`으로 응답한다(2026-11-13 확인). `json.loads` 전에 `gzip.decompress`로 푼다.

### 지표별 규약 (라리가 2025/26에서 확인됨)

`StatValue`가 합계인 파일과 90분당인 파일이 섞여 있다. 파일마다 다르니 아래 표를 따른다.

| 지표명 | StatValue | SubStatValue | 최소 출전 | 선수 수 |
|---|---|---|---|---|
| `mins_played` | 출전 분 | 경기 수 | 없음 | 600 |
| `goals` | 골 합계 | PK 골 | 없음 | 286 |
| `expected_goals` | xG 합계 | 골 | 없음 | 497 |
| `expected_assists` | xA 합계 | 도움 | 없음 | 596 |
| `total_att_assist` | 기회 창출 합계 | 90분당 | 없음 | 486 |
| `big_chance_created` | 빅찬스 창출 합계 | 도움 | 없음 | 346 |
| `total_scoring_att` | 슈팅/90 | 전환율 % | 약 346분 | 325 |
| `accurate_pass` | 성공 패스/90 | 성공률 % | 약 346분 | 344 |
| `accurate_long_balls` | 성공 롱볼/90 | 성공률 % | 약 346분 | 322 |
| `lbp_total` | 라인 브레이킹 패스/90 | 합계 | 약 444분 | 297 |
| `won_contest` | 드리블 성공/90 | 성공률 % | 약 346분 | 325 |
| `total_tackle` | 태클/90 | 합계 | 약 346분 | 335 |
| `interception` | 인터셉트/90 | 합계 | 약 346분 | 333 |
| `effective_clearance` | 클리어/90 | 합계 | 약 346분 | 342 |
| `ball_recovery` | 리커버리/90 | 합계 | 약 346분 | 344 |
| `defensive_contributions` | 수비 액션/90 | 합계 | 약 346분 | 344 |
| `poss_won_att_3rd` | 파이널 서드 탈취/90 | 미드필드 탈취/90 | 약 346분 | 307 |
| `rating` | 평균 평점 | 경기 MVP 횟수 | 약 665분 | 313 |

- **합계가 있으면 합계에서 per-90을 직접 계산한다.** 반올림된 per-90 값을 그대로 쓰지 않는다.
- `mins_played`가 선수 모집단이다. 다른 파일에 없는 선수는 그 지표가 0이거나 최소 출전 미달이다.
  후보 풀(600분 이상)은 대부분의 최소 출전 기준 위에 있으므로 "없으면 0"으로 처리해도 된다.
  **평점은 예외다.** 665분 미만은 0이 아니라 결측이다.
- 진행 중인 시즌은 최소 출전 기준이 더 낮다(11경기 시점 태클 파일 최저 195분). 기준을 하드코딩하지 않는다.
- 미확인 지표명: `outfielder_block`, `expected_goalsontarget`, `ontarget_scoring_att`, 골키퍼 계열. 쓰기 전에 값을 찍어 본다.

### 없는 것

| Understat에 있던 것 | FotMob | 처리 |
|---|---|---|
| npxG | 없음 | `xG − 0.79 × PK 골`로 근사. 실축한 PK는 못 뺀다. 근사라고 표시한다 |
| xGChain, xGBuildup | 없음 | 폐기. 빌드업 역할은 패스 지표로 다시 정의 |
| PPDA | 없음 | 팀 `poss_won_att_3rd_team`으로 대체 |
| deep | 없음 | 팀 `touches_in_opp_box_team`으로 대체 |
| xPts | 없음 | 폐기 |
| 경기별 xG 추이 | 경기마다 따로 호출해야 함 | 추이 차트는 뺀다. 380경기를 매일 긁지 않는다 |
| 선수 볼터치, 나이 | 없음 | 그대로 한계로 남긴다 |

---

## 2. 포지션 코드

`Positions[0]`이 주 포지션이다. 아래는 예시 선수를 보고 추정한 **초안**이다.

| 코드 | 추정 | 예시 |
|---|---|---|
| 11 | GK | Sivera, David Soria |
| 32 | 오른쪽 풀백 | Juan Iglesias, Jonny Otto |
| 33~36 | 센터백 | Djené, Lejeune, Eric García |
| 37, 38 | 왼쪽 풀백 | Marcos Alonso, Mojica |
| 51, 59 | 윙백 | Juanlu Sánchez, Oso |
| 64~66 | 수비형 미드필더 | Moncayola, Witsel |
| 73~77 | 중앙 미드필더 | Tchouaméni, Luis Milla |
| 71, 72, 78, 79 | 측면 미드필더 | Pépé, Giuliano Simeone, Moleiro |
| 82~88 | 공격형 미드필더·윙어 | Antony(83), Fornals(85), Vinícius(87) |
| 103, 107 | 세컨드 스트라이커·측면 공격수 | Iago Aspas, Bryan Zaragoza |
| 104~106, 115 | 스트라이커 | Sørloth, Borja Iglesias, Oyarzabal |
| 1, 2, 3 | 세부 위치 없음 (DF/MF/FW로 추정) | 출전 적은 유스 선수 |

**첫 작업으로 코드별 선수 10명씩 출력해서 이 표를 검증한다.** 틀린 줄은 고친다.

수비수를 추천하게 되므로 그룹을 FW/MF/DF 3개에서 5개로 늘린다:
**CB / FB(풀백·윙백) / CM(수비형·중앙) / AM(공격형·측면) / ST**.
그룹별 인원이 60명 아래로 떨어지면 백분위가 거칠어지니 그 그룹은 합친다.

---

## 3. 역할 재정의

아래 가중치는 **출발점**이다. 백테스트로 검증해서 바꾼다.

| 역할 | 지표 | 대상 그룹 |
|---|---|---|
| 결정력 | npxG/90 0.55, 슈팅/90 0.25, 드리블/90 0.20 | ST, AM |
| 찬스 메이킹 | xA/90 0.50, 기회 창출/90 0.30, 빅찬스 창출/90 0.20 | AM, CM, FB |
| 빌드업 | 라인 브레이킹 패스/90 0.45, 성공 패스/90 0.30, 성공 롱볼/90 0.25 | CM, CB |
| 볼 탈취 (신규) | 태클/90 0.35, 인터셉트/90 0.35, 리커버리/90 0.30 | CM, FB |
| 박스 수비 (신규) | 클리어/90 0.45, 인터셉트/90 0.30, 블록/90 0.25 | CB |

### 지난 백테스트에서 배운 것 — 반드시 반영

Understat 기준 백테스트에서 "지난 시즌 (npxG+xA)/90 그대로"(r 0.843, 0.854)가
역할 점수(r 0.817, 0.813)를 두 번 다 이겼다. 백분위 변환과 가중 평균이 정보를 깎았다는 뜻이다.

- 백분위 대신 **z-점수 가중합**으로 역할 점수를 만들어 비교한다. 더 나은 쪽을 쓴다.
- 비교군에 "지난 시즌 원지표 그대로"를 계속 둔다. 이걸 못 이기는 역할 점수는 못 이긴다고 쓴다.
- 수비 역할은 예측 대상을 따로 잡는다. "다음 시즌 같은 지표"를 맞히는지로 본다.
  공격 생산량으로 수비 역할을 검증하면 의미가 없다(이전 빌드업 r이 0.2였던 이유).
- 수비 지표는 팀 점유율에 끌려간다(공을 못 가진 팀 선수가 태클이 많다).
  **점유율 보정**을 넣는다: `지표 × 50 / (100 − 팀 점유율)`. 보정 전후를 백테스트로 비교한다.

---

## 4. 팀 데이터

### 지표 파일 (팀)

`{지표명}_team.json`. 구조는 선수 파일과 같고 `ParticipantName`이 팀 이름, `TeamId`가 키다.

확인됨: `possession_percentage_team`(Barcelona 68.8), `expected_goals_conceded_team`
(StatValue 합계, SubStatValue 실점. Arsenal 28.1 / 27, Burnley 74.9 / 75).

팀 진단 지표를 아래로 바꾼다:

| 진단 항목 | 지표 |
|---|---|
| 기회 창출 | `expected_goals_team` ÷ 경기 |
| 기회 억제 | `expected_goals_conceded_team` ÷ 경기 (낮을수록 좋음) |
| 결정력 | 득점 − xG |
| 실점 억제 | 피xG − 실점 |
| 박스 침투 | `touches_in_opp_box_team` |
| 전방 압박 | `poss_won_att_3rd_team` |
| 점유 | `possession_percentage_team` |
| 수비 활동량 | `total_tackle_team` + `interception_team` (점유율 보정) |

"낮을수록 좋은" 지표는 백분위를 뒤집는다. 기존 `TEAM_METRICS`의 불리언 플래그 방식을 유지한다.

### 순위·승점·최근 6경기

지표 파일에 없다. `api/data/leagues?id=` 응답의 순위표와 경기 목록에서 가져온다.
이 API가 파이썬에서 막히면 `openfootball/football.json`(깃허브, 공개 도메인 경기 결과)에서
승점과 최근 6경기를 계산한다. 그 경우 팀 이름 매핑표가 필요하다.

---

## 5. 구현 순서

1. **0절 접근성 검증.** 결과를 보고한 뒤 진행.
2. `scripts/fetch_fotmob.py` — 2개 리그 × 4시즌 × 필요한 지표만 받아 `data/fotmob_raw.json`으로.
   요청 사이 0.5초 이상 쉰다. 표준 라이브러리만 쓴다.
3. 포지션 코드 표 검증(2절).
4. `scripts/model.py`·`build.py`를 FotMob 필드로 교체. `data.json` 키 구조는 가능한 한 유지해서
   `site/template.html` 수정을 줄인다.
5. 보정 계수 K를 FotMob 데이터로 다시 추정.
6. `scripts/backtest.py` 재실행(3절의 비교 포함).
7. `site/template.html` — 지표 사전, 역할 칩, 방법론 문단, 출처 표기를 FotMob 기준으로 수정.
   경기별 추이 차트 제거. 수비 역할 추가.
8. 워크플로에서 `fetch_understat.py` 호출을 `fetch_fotmob.py`로 교체.
   `fetch_understat.py` 파일은 지우지 않고 남겨 둔다.
9. `CLAUDE.md`·`ROADMAP.md`·`README.md` 갱신. "수비 지표는 FBref에서"는 삭제한다
   (FBref는 2026년 1월에 고급 지표를 내렸다).

### 유지할 안전장치

- 데이터가 비정상적으로 적으면(팀 30개 미만, 선수 200명 미만) 커밋하지 않는다.
- 응답이 JSON이 아니면 그 자리에서 실패한다. 빈 데이터로 덮어쓰지 않는다.
- 기준 시즌 자동 전환(`SWITCH_AFTER = 15`)은 그대로.

---

## 6. 검증 기준값 (FotMob, 2026-11-13 확인)

| 항목 | 값 |
|---|---|
| 라리가 25/26 Mbappé | xG 23.9, 25골, 2604분, 31경기 |
| 라리가 25/26 Lamine Yamal | xG 12.8, 16골(PK 3), xA 10.1, 11도움, 2268분, 28경기 |
| 라리가 25/26 Yamal 세부 | 슈팅 4.6/90, 드리블 5.3/90, 태클 1.4/90(합계 35), 평점 8.33 |
| 라리가 25/26 Barcelona 점유율 | 68.8% |
| PL 25/26 피xG 최저 / 최고 | Arsenal 28.1 (27실점) / Burnley 74.9 (75실점) |
| 라리가 25/26 `mins_played` 선수 수 | 600 |

빌드 결과가 이 값과 다르면 조인이나 per-90 계산이 틀린 것이다.

---

## 7. 주의

- 비공식 경로다. FotMob이 구조를 바꾸면 끊긴다. 수집 스크립트가 구조 변화를 만나면
  조용히 넘어가지 말고 어떤 키가 없는지 출력하고 실패한다.
- 하루 한 번, 필요한 파일만 받는다. 사이트 하단에 출처를 FotMob으로 표기한다.
- xG 모델이 Understat과 다르다. 두 소스의 값을 한 표에 섞지 않는다.
  이전 백테스트 수치(r 0.81 등)는 Understat 기준이므로 새 결과와 직접 비교하지 않는다.
