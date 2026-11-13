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
        table=[{k: r.get(k) for k in TABLE_KEYS} for r in table],
        xg_table=[{k: r.get(k) for k in XG_KEYS + ("xPoints",)} for r in xg],
        matches=matches)


def stat_file(lid, sid, name, team):
    url = f"https://data.fotmob.com/stats/{lid}/season/{sid}/{name}.json"
    d = get_json(url)
    tl = need(d, ["TopLists", 0], url)
    rows = need(tl, ["StatList"], url)
    check_rows(rows, TEAM_KEYS if team else PLAYER_KEYS, url)
    for r in rows:
        r.pop("TeamColor", None)
    return dict(title=tl.get("Title"), subtitle=tl.get("Subtitle"), rows=rows)


def main():
    out = {}
    for lg, lid in LEAGUES.items():
        latest_raw, latest = league_page(lid)
        links = need(latest_raw, ["stats", "seasonStatLinks"], f"leagues?id={lid}")
        sids = {need(l, ["Name"], "seasonStatLinks"): need(l, ["TournamentId"], "seasonStatLinks")
                for l in links}
        seasons = sorted(sids, reverse=True)[:N_SEASONS]
        if latest["season"] not in sids:
            sys.exit(f"{lg}: 최신 시즌 {latest['season']}의 지표 ID가 seasonStatLinks에 없다")
        for season in seasons:
            yr = int(season.split("/")[0])
            key = f"{lg}_{yr}"
            page = latest if season == latest["season"] else league_page(lid, season)[1]
            sid = sids[season]
            stats = {}
            for name in PLAYER_STATS + TEAM_STATS:
                try:
                    stats[name] = stat_file(lid, sid, name, name in TEAM_STATS)
                except Missing as e:
                    if name not in OPTIONAL_STATS:
                        sys.exit(f"{key}: 지표 파일 없음 — {e}")
                    stats[name] = None
            played = max((r["played"] for r in page["table"]), default=0)
            n_players = len(stats["mins_played"]["rows"])
            absent = [n for n, v in stats.items() if v is None]
            print(f"{key} ({season}, 시즌ID {sid}): 팀 {len(page['table'])}개 · 최다 {played}경기 · "
                  f"선수 {n_players}명 · 지표 {len(stats) - len(absent)}개 · 경기 {len(page['matches'])}"
                  + (f" · 없음: {', '.join(absent)}" if absent else ""))
            out[key] = dict(league_id=lid, season_id=sid, **page, stats=stats)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"\n저장: {os.path.abspath(OUT)} ({os.path.getsize(OUT) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
