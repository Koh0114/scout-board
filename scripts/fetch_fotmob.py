"""FotMob에서 EPL·라리가 최근 4시즌 데이터를 내려받아 data/fotmob_raw.json 으로 저장.

표준 라이브러리만 사용한다 (pip install 불필요). 비공식 경로다 — 구조가 바뀌면
조용히 넘어가지 않고 어떤 키가 없는지 출력하고 실패한다.

  리그 API  https://www.fotmob.com/api/data/leagues?id={리그ID}[&season=2025/2026]
            → 시즌 ID 목록(stats.seasonStatLinks), 순위표, 경기 목록
  지표 파일 https://data.fotmob.com/stats/{리그ID}/season/{시즌ID}/{지표명}.json
            → 항상 gzip으로 온다. 없는 파일은 S3가 403 AccessDenied를 준다.

저장 구조: {"EPL_2025": {"league_id", "season_id", "season", "table", "xg_table",
            "matches", "stats": {지표명: {"title", "subtitle", "rows": [...]}}}, ...}
"""
import gzip, json, os, sys, time, urllib.error, urllib.parse, urllib.request

LEAGUES = {"EPL": 47, "La_liga": 87}          # model.LEAGUES와 같은 키
N_SEASONS = 4                                  # 백테스트가 연속 시즌쌍 2개를 쓴다
PAUSE = 0.6                                    # 요청 사이 최소 대기(초)
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "fotmob_raw.json")
# 이번 실행에서 뺀 시즌 등 상태 기록 — build.py가 읽어 사이트 안내 문구에 쓴다.
STATUS = os.path.join(os.path.dirname(__file__), "..", "data", "fotmob_status.json")

# 시즌 초 대비: 새 시즌이 시작돼도 FotMob 지표 파일은 한동안 없다 (403/404, 또는
# seasonStatLinks에 아직 안 올라옴). 그때만 현재 시즌을 빼고 직전 시즌까지로 저장한다.
# '시즌 초'는 달력이 아니라 순위표로 판단한다 — 가장 많이 치른 팀이 이 경기 수 이하.
# 그 밖의 경우(지난 시즌 파일 없음, 시즌 중반에 현재 시즌 파일 없음, JSON 아님, 키 없음)는 실패한다.
EARLY_MAX_PLAYED = 6

# 필요한 파일만 받는다. 규약(StatValue/SubStatValue 뜻)은 FOTMOB_MIGRATION.md 1절.
PLAYER_STATS = [
    "mins_played", "goals", "expected_goals", "expected_assists",
    "total_att_assist", "big_chance_created", "total_scoring_att",
    "accurate_pass", "accurate_long_balls", "lbp_total", "won_contest",
    "total_tackle", "interception", "effective_clearance", "ball_recovery",
    "outfielder_block", "poss_won_att_3rd", "defensive_contributions", "rating",
]
# 일부 시즌에 파일이 아예 없는 지표 (2026-11-13 확인). 없으면 null로 두고 계속한다.
#   lbp_total: EPL 2023/24·2024/25, 라리가 2023/24에 없음
# 여기 없는 지표가 빠지면 구조 변경으로 보고 실패한다.
OPTIONAL_STATS = {"lbp_total"}
TEAM_STATS = [
    "expected_goals_team", "expected_goals_conceded_team", "touches_in_opp_box_team",
    "poss_won_att_3rd_team", "possession_percentage_team",
    "total_tackle_team", "interception_team",
]

PLAYER_KEYS = ("ParticiantId", "ParticipantName", "TeamId", "TeamName", "Positions",
               "StatValue", "SubStatValue", "MinutesPlayed", "MatchesPlayed")
TEAM_KEYS = ("TeamId", "ParticipantName", "StatValue", "SubStatValue", "MatchesPlayed")
TABLE_KEYS = ("id", "name", "played", "wins", "draws", "losses", "scoresStr", "pts")
XG_KEYS = ("teamId", "xg", "xgConceded", "played")

_last = [0.0]


class Missing(Exception):
    """지표 파일이 없다 (S3 403/404)."""


def get(url, tries=3):
    """GET → bytes. 헤더는 urllib 기본값 그대로. gzip이면 푼다.
    네트워크 오류·5xx만 재시도하고, 4xx는 바로 실패한다."""
    last = None
    for i in range(tries):
        wait = PAUSE - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=45) as r:
                body, enc = r.read(), r.headers.get("Content-Encoding")
            _last[0] = time.time()
            if enc == "gzip" or body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            return body
        except urllib.error.HTTPError as e:
            _last[0] = time.time()
            head = e.read()[:200]
            if e.code in (403, 404) and "data.fotmob.com" in url:
                raise Missing(f"{url} → {e.code} {head!r}") from None
            if e.code < 500:
                raise RuntimeError(f"{url} → HTTP {e.code}, 응답 앞부분 {head!r}") from None
            last = f"HTTP {e.code} {head!r}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            _last[0] = time.time()
            last = repr(e)
        print(f"  재시도 {i + 1}/{tries} ({last})", file=sys.stderr)
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url} 실패: {last}")


def get_json(url):
    body = get(url)
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError(f"{url} 응답이 JSON이 아님: {body[:200]!r}") from None


def need(obj, path, where):
    """obj에서 path(키 목록)를 따라가 값을 꺼낸다. 없으면 어떤 키가 없는지 말하고 실패."""
    cur = obj
    for i, k in enumerate(path):
        try:
            cur = cur[k]
        except (KeyError, IndexError, TypeError):
            sys.exit(f"구조 변경? {where}: {'.'.join(map(str, path[:i + 1]))} 없음")
    return cur


def check_rows(rows, keys, where):
    for r in rows:
        miss = [k for k in keys if k not in r]
        if miss:
            sys.exit(f"구조 변경? {where}: 행에 {miss} 없음 — {json.dumps(r, ensure_ascii=False)[:200]}")


def league_page(lid, season=None):
    q = f"id={lid}" + (f"&season={urllib.parse.quote(season, safe='')}" if season else "")
    d = get_json(f"https://www.fotmob.com/api/data/leagues?{q}")
    where = f"leagues?{q}"
    data = need(d, ["table", 0, "data"], where)
    sel = need(data, ["selectedSeason"], where)
    if season and sel != season:
        sys.exit(f"{where}: {season}을 요청했는데 {sel}이 왔다")
    table = need(data, ["table", "all"], where)
    xg = need(data, ["table", "xg"], where)
    check_rows(table, TABLE_KEYS, where + " table.all")
    check_rows(xg, XG_KEYS, where + " table.xg")
    matches = []
    for m in need(d, ["fixtures", "allMatches"], where):
        st = need(m, ["status"], where + " fixtures")
        matches.append(dict(
            round=m.get("round"), id=m.get("id"),
            home=int(need(m, ["home", "id"], where)), away=int(need(m, ["away", "id"], where)),
            utc=st.get("utcTime"), finished=bool(st.get("finished")),
            cancelled=bool(st.get("cancelled")), score=st.get("scoreStr")))
    return d, dict(
        season=sel,
        table=[dict({k: r.get(k) for k in TABLE_KEYS}, rank=i + 1) for i, r in enumerate(table)],  # table.all은 순위 순
        xg_table=[{k: r.get(k) for k in XG_KEYS + ("xPoints",)} for r in xg],
        matches=matches)


def stat_file(lid, sid, name, team):
    url = f"https://data.fotmob.com/stats/{lid}/season/{sid}/{name}.json"
    d = get_json(url)
    tl = need(d, ["TopLists", 0], url)
    rows = need(tl, ["StatList"], url)
    check_rows(rows, TEAM_KEYS if team else PLAYER_KEYS, url)
    if not team:                      # 팀색은 팀 파일에만 남긴다 (결론 스트립의 띠). 선수 행에선 용량만 늘린다
        for r in rows:
            r.pop("TeamColor", None)
    return dict(title=tl.get("Title"), subtitle=tl.get("Subtitle"), rows=rows)


def warn(msg):
    print(f"⚠ {msg}")
    if os.environ.get("GITHUB_ACTIONS"):          # Actions 실행 요약에도 남긴다
        print(f"::warning title=시즌 초::{msg}")


def main():
    out, skipped = {}, []
    for lg, lid in LEAGUES.items():
        latest_raw, latest = league_page(lid)
        links = need(latest_raw, ["stats", "seasonStatLinks"], f"leagues?id={lid}")
        sids = {need(l, ["Name"], "seasonStatLinks"): need(l, ["TournamentId"], "seasonStatLinks")
                for l in links}
        cur = latest["season"]
        cur_played = max((r["played"] for r in latest["table"]), default=0)
        early = cur_played <= EARLY_MAX_PLAYED
        # 최근 N_SEASONS 시즌 창은 현재 시즌을 포함해 고정한다. 현재 시즌을 빼면 창이 그만큼 줄어든다
        # (더 오래된 시즌을 당겨 오지 않는다 — 백테스트에 필요한 완주 3시즌은 그대로 남는다).
        seasons = sorted(set(sids) | {cur}, reverse=True)[:N_SEASONS]

        def skip_current(why):
            if not early:
                sys.exit(f"{lg} {cur}: {why} — 시즌 초가 아니라서({cur_played}경기 치름 > "
                         f"{EARLY_MAX_PLAYED}) 구조 변경이나 차단으로 보고 멈춘다")
            warn(f"{lg} {cur} ({cur_played}경기 치름): {why} — 이 시즌을 빼고 직전 시즌 기준으로 저장한다")
            skipped.append(dict(league=lg, season=cur, played=cur_played, reason=why))

        for season in seasons:
            yr = int(season.split("/")[0])
            key = f"{lg}_{yr}"
            if season == cur and season not in sids:
                skip_current("seasonStatLinks에 아직 없음")
                continue
            page = latest if season == cur else league_page(lid, season)[1]
            sid = sids[season]
            stats = {}
            for name in PLAYER_STATS + TEAM_STATS:
                try:
                    stats[name] = stat_file(lid, sid, name, name in TEAM_STATS)
                except Missing as e:
                    if name in OPTIONAL_STATS:
                        stats[name] = None
                        continue
                    if season != cur:
                        sys.exit(f"{key}: 지표 파일 없음 — {e}")
                    skip_current(f"지표 파일 {name} 없음 (HTTP {str(e).split(' → ')[-1][:3]})")
                    stats = None
                    break
            if stats is None:
                continue
            played = max((r["played"] for r in page["table"]), default=0)
            n_players = len(stats["mins_played"]["rows"])
            absent = [n for n, v in stats.items() if v is None]
            print(f"{key} ({season}, 시즌ID {sid}): 팀 {len(page['table'])}개 · 최다 {played}경기 · "
                  f"선수 {n_players}명 · 지표 {len(stats) - len(absent)}개 · 경기 {len(page['matches'])}"
                  + (f" · 없음: {', '.join(absent)}" if absent else ""))
            out[key] = dict(league_id=lid, season_id=sid, **page, stats=stats)

    # 한 리그라도 현재 시즌을 뺐으면 모든 리그에서 뺀다 — 리그마다 기준 시즌·최근 폼이 갈리지 않게.
    if skipped:
        yrs = {int(x["season"].split("/")[0]) for x in skipped}
        for k in [k for k in out if int(k.rsplit("_", 1)[1]) in yrs]:
            warn(f"{k}: 다른 리그의 새 시즌 파일이 아직 없어 같이 뺀다")
            del out[k]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    with open(STATUS, "w", encoding="utf-8") as f:          # 매번 새로 쓴다 (지난 실행 상태가 남지 않게)
        json.dump(dict(skipped=skipped, seasons=sorted(out)), f, ensure_ascii=False, indent=1)
    print(f"\n저장: {os.path.abspath(OUT)} ({os.path.getsize(OUT) / 1e6:.1f} MB)"
          + (f" · 시즌 초라 뺀 시즌: {sorted({x['season'] for x in skipped})}" if skipped else ""))


if __name__ == "__main__":
    main()
