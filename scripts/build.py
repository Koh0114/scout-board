"""data/fotmob_raw.json  ->  docs/data.json + docs/index.html

기준 시즌은 자동으로 정한다:
  현재 시즌이 15경기 이상 치렀으면 현재 시즌, 아니면 직전 시즌.
  (9월에는 직전 시즌이 기준, 12월쯤 자동으로 현재 시즌으로 넘어간다.)
표준 라이브러리만 사용한다. 지표 규약은 FOTMOB_MIGRATION.md 1·4절.
"""
import json, os, sys
from datetime import timezone, datetime, timedelta

from model import (LEAGUES, POOL_MIN, PLAYER_METRICS, DISPLAY_METRICS, ROLES, POS_GROUPS,
                   PK_XG, MIN_GROUP, season_label, pct_rank, played, season_block,
                   player_rows, pool_rows, excluded_rows, merge_groups, peer_labels,
                   estimate_k, prior_means, apply_shrinkage, score_pool,
                   similar_players, sim_weights, SIM_N,
                   STYLE, team_styles, style_metric, style_euclid, style_neighbors, z_stats,
                   completed, pearson, league_effect)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW = os.path.join(ROOT, "data", "fotmob_raw.json")
STATUS = os.path.join(ROOT, "data", "fotmob_status.json")   # fetch_fotmob.py가 쓴다 (시즌 초에 뺀 시즌)
DOCS = os.path.join(ROOT, "docs")
TEMPLATE = os.path.join(ROOT, "site", "template.html")

SWITCH_AFTER = 15         # 현재 시즌이 이만큼 치르면 기준 시즌을 넘긴다

# (키, 높을수록 좋은가). False면 백분위가 100 - p로 뒤집힌다.
TEAM_METRICS = [("xG90", True), ("xGA90", False), ("xGD90", True),
                ("finishing", True), ("keeping", True),
                ("box90", True), ("press90", True), ("poss", True),
                ("defact_adj", True)]


# --------------------------------------------------------------- load
d = json.load(open(RAW, encoding="utf-8"))
years = sorted({int(k.rsplit("_", 1)[1]) for k in d})
if not years:
    sys.exit("원본에 시즌이 없습니다.")
CUR = years[-1]

SEASON = CUR if played(d, CUR) >= SWITCH_AFTER else CUR - 1
FORM = CUR
print(f"기준 시즌 {season_label(SEASON)} ({played(d, SEASON)}경기) · 최근 폼 {season_label(FORM)} ({played(d, FORM)}경기)")
SKIPPED = (json.load(open(STATUS, encoding="utf-8")).get("skipped") or []) if os.path.exists(STATUS) else []
SEASON_NOTE = None
if SKIPPED:
    new = sorted({x["season"] for x in SKIPPED})
    SEASON_NOTE = (f"새 시즌({', '.join(n.replace('/20', '/') for n in new)}) 지표 파일이 FotMob에 아직 없어 "
                   f"{season_label(SEASON)} 기준으로 만들었고, 최근 폼은 빠져 있습니다.")
    print(f"⚠ 시즌 초: {SEASON_NOTE}")
if FORM == SEASON:
    print("최근 폼 없음 — 기준 시즌과 같은 시즌이라 form_now·now를 비워 둔다")
if f"{list(LEAGUES)[0]}_{SEASON}" not in d:
    sys.exit(f"기준 시즌 {SEASON} 데이터가 원본에 없습니다.")


K_VALUES, K_INFO = estimate_k(d, years, log=print)

# 리그 강도 (ROADMAP C): 매번 다시 추정하지만 보정은 켜지 않는다. 구간이 0을 벗어나면 경고만 남긴다.
LEAGUE = league_effect(d, years, K_VALUES)
print(f"리그 강도 — 리그 간 이적자 {LEAGUE['n']}명 (PL→라리가 {LEAGUE['n_pl_ll']} · 라리가→PL {LEAGUE['n_ll_pl']}) · "
      f"대조군 {LEAGUE['n_ctrl']}명 · 보정 {'켬' if LEAGUE['applied'] else '안 함'}")
for m, v in LEAGUE["metrics"].items():
    print(f"  {m:<7} 추정 {v['est']:+.4f}  95% CI [{v['lo']:+.4f}, {v['hi']:+.4f}]")
    if v["excludes_zero"]:
        msg = (f"리그 강도 {m}: 95% 신뢰구간 [{v['lo']:+.4f}, {v['hi']:+.4f}]이 0을 포함하지 않는다 "
               f"(n={LEAGUE['n']}) — 보정은 켜지 않았다. 켤지 사람이 검토한다.")
        print(f"⚠ {msg}")
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::warning title=리그 강도::{msg}")


# --------------------------------------------------------------- teams
def team_block(lg, yr):
    blk = season_block(d, lg, yr)
    st = blk.get("stats") or {}
    table = blk.get("table") or []
    if not table:
        return {}

    def by_team(name):
        rows = {r["TeamId"]: r for r in st[name]["rows"]}
        miss = [t["name"] for t in table if t["id"] not in rows]
        if miss:
            sys.exit(f"{lg}_{yr} {name}: 순위표 팀 {miss}이 지표 파일에 없다")
        return rows

    xg, xga = by_team("expected_goals_team"), by_team("expected_goals_conceded_team")
    box, press = by_team("touches_in_opp_box_team"), by_team("poss_won_att_3rd_team")
    poss = by_team("possession_percentage_team")
    tkl, intc = by_team("total_tackle_team"), by_team("interception_team")
    pas, lng, crs = (by_team("accurate_pass_team"), by_team("accurate_long_balls_team"),
                     by_team("accurate_cross_team"))

    # 최근 6경기 — 끝난 경기만, 시간순
    results = {}
    for m in sorted(blk.get("matches") or [], key=lambda m: m["utc"] or ""):
        if not m["finished"] or m["cancelled"] or not m["score"]:
            continue
        hs, as_ = (int(x) for x in m["score"].split(" - "))
        for tid, gf, ga in ((m["home"], hs, as_), (m["away"], as_, hs)):
            results.setdefault(tid, []).append("w" if gf > ga else "d" if gf == ga else "l")

    out = {}
    for t in table:
        tid, n = t["id"], t["played"]
        if n == 0:
            continue
        gf, ga = (int(x) for x in t["scoresStr"].split("-"))
        X, XA = xg[tid], xga[tid]
        nx, nxa = X["MatchesPlayed"] or n, XA["MatchesPlayed"] or n
        defact = ((tkl[tid]["SubStatValue"] + intc[tid]["SubStatValue"])
                  / (tkl[tid]["MatchesPlayed"] or n))
        p = poss[tid]["StatValue"]
        out[t["name"]] = dict(
            id=tid, name=t["name"], league=lg, mp=n,
            rank=t.get("rank"),
            # FotMob TeamColor — 결론 스트립의 띠에만 쓴다 (글자·데이터 마크엔 쓰지 않는다)
            color=X.get("TeamColor"),
            w=t["wins"], d=t["draws"], l=t["losses"], pts=t["pts"],
            gf=gf, ga=ga, gf90=gf / n, ga90=ga / n,
            xG=X["StatValue"], xGA=XA["StatValue"],
            xG90=X["StatValue"] / nx, xGA90=XA["StatValue"] / nxa,
            xGD90=X["StatValue"] / nx - XA["StatValue"] / nxa,
            # 득점 − xG, 피xG − 실점: 같은 파일의 StatValue/SubStatValue끼리 뺀다
            finishing=X["SubStatValue"] - X["StatValue"],
            keeping=XA["StatValue"] - XA["SubStatValue"],
            box90=box[tid]["StatValue"] / (box[tid]["MatchesPlayed"] or n),
            press90=press[tid]["StatValue"],
            poss=p,
            defact90=defact,
            # 스타일 분류용: 경기당 성공 패스·롱볼·크로스 (StatValue가 이미 경기당)
            pass_pg=pas[tid]["StatValue"], long_pg=lng[tid]["StatValue"], cross_pg=crs[tid]["StatValue"],
            # 점유율 보정: 상대가 공을 50% 가진 것으로 환산 (FOTMOB_MIGRATION.md 3절)
            defact_adj=defact * 50 / (100 - p),
            form=results.get(tid, [])[-6:],
        )
    return out


teams = {lg: team_block(lg, SEASON) for lg in LEAGUES}

# 팀 스타일 (ROADMAP E): 수준(xGD90) 대비 잔차 4개 → 두 리그 40팀에서 공분산을 추정한 마할라노비스 거리.
team_styles({lg: list(tt.values()) for lg, tt in teams.items()})
ALL_TEAMS = [t for tt in teams.values() for t in tt.values()]
STYLE_NEAR = style_neighbors(ALL_TEAMS, style_metric(ALL_TEAMS))
# 사이트 문장용: 잔차 4개의 40팀 안 z (|z|가 큰 것부터 말로 옮긴다)
for k, _, _ in STYLE:
    v = [t[k] for t in ALL_TEAMS]
    mu = sum(v) / len(v)
    sd = (sum((x - mu) ** 2 for x in v) / len(v)) ** 0.5 or 1.0
    for t in ALL_TEAMS:
        t.setdefault("style_z", {})[k] = round((t[k] - mu) / sd, 2)
# "비슷한 스타일이 드문 팀": 1위 거리가 40팀 중 상위 10%(4팀)
d1 = sorted((STYLE_NEAR[(t["league"], t["id"])][0][1] for t in ALL_TEAMS), reverse=True)
STYLE_RARE_CUT = d1[max(1, round(len(d1) * 0.10)) - 1]
for t in ALL_TEAMS:
    for k in [k for k, _, _ in STYLE] + ["long_share", "cross_share", "press_share"]:
        t[k] = round(t[k], 4)
    t["style_near"] = [[b["league"], b["id"], round(dd, 2)] for b, dd in STYLE_NEAR[(t["league"], t["id"])]]
    t["style_rare"] = STYLE_NEAR[(t["league"], t["id"])][0][1] >= STYLE_RARE_CUT
print("비슷한 스타일이 드문 팀(1위 거리 상위 10%): " + ", ".join(
    f"{t['name']} {STYLE_NEAR[(t['league'], t['id'])][0][1]:.2f}" for t in ALL_TEAMS if t["style_rare"]))

# 스타일 안정성: 완주한 연속 시즌에 모두 있는 팀(같은 리그·같은 ID)으로 잔차 4개 각각의 시즌간 r
_done = [y for y in years if completed(d, y)]
_sty = {}
for y in _done:
    tt = {lg: list(team_block(lg, y).values()) for lg in LEAGUES}
    team_styles(tt)
    _sty[y] = {(t["league"], t["id"]): t for l in tt.values() for t in l}
_pairs = [(_sty[y][k], _sty[y + 1][k]) for y in _done if y + 1 in _sty for k in _sty[y] if k in _sty[y + 1]]
STYLE_STAB = dict(n=len(_pairs), seasons=[f"{season_label(y)}→{season_label(y + 1)}" for y in _done if y + 1 in _sty],
                  r={k: round(pearson([a[k] for a, _ in _pairs], [b[k] for _, b in _pairs]), 2) for k, _, _ in STYLE})
print(f"스타일 시즌간 r ({STYLE_STAB['n']}팀·시즌쌍 {STYLE_STAB['seasons']}): {STYLE_STAB['r']}")
players = player_rows(d, SEASON)

form_teams = {}
for lg in LEAGUES:
    for t in team_block(lg, FORM).values():
        form_teams[(lg, t["id"])] = t
form_players = {(r["league"], r["id"]): r for r in player_rows(d, FORM)}

for lg, tt in teams.items():
    for m, higher in TEAM_METRICS:
        vals = [t[m] for t in tt.values()]
        for t in tt.values():
            p = pct_rank(vals, t[m])
            t.setdefault("pct", {})[m] = round(p if higher else 100 - p, 1)

# 후보 풀 = POOL_MIN분 이상 + 팀 경기 절반 이상 출전. 분은 넘었지만 경기가 모자란
# 선수는 per-90 지표가 없어 순위에서 빠진다 — 목록만 data.json에 남긴다.
pool = pool_rows(d, SEASON, rows=players)
excluded = excluded_rows(players)
print(f"후보 풀 {len(pool)}명 · 출전 경기 부족으로 제외 {len(excluded)}명")

# 그룹 인원이 MIN_GROUP 미만이면 자동으로 합친다 (사전평균·그룹 백분위에 쓰는 그룹).
MERGES = merge_groups(pool)
counts = {lab: sum(1 for p in pool if p["peer"] == lab) for lab in peer_labels(pool)}
print(f"그룹 인원 {counts}")
for mg in MERGES:
    print(f"  ! {mg['group']} {mg['n']}명 < {MIN_GROUP} → {mg['into']}({mg['n_into']}명)과 합침 "
          f"→ {mg['result']} {mg['n_result']}명")

# per-90 계산 직후 · 백분위 계산 전에 축소를 적용한다.
# 백분위(pct/pctA)와 역할 점수는 전부 축소값 기준, 원값은 표시용으로 남는다.
PRIOR = prior_means(pool)
apply_shrinkage(pool, K_VALUES, PRIOR)
score_pool(pool, suffix="_adj")

# 유사 선수: 같은 그룹 안 축소값 z-벡터의 가중 코사인 유사도 (같은 팀 제외). 사이트는 계산하지 않고 읽기만 한다.
# 가중치 = 그 그룹 안 시즌간 상관계수 r (K 추정과 같은 표본, 음수면 0).
SIM_W, SIM_WN = sim_weights(d, years, peer_labels(pool), log=print)
print("유사도 가중치 (그룹 안 시즌간 r, 음수 → 0):")
print("  " + " " * 8 + "".join(f"{m[:-2][:6]:>7}" for m in PLAYER_METRICS))
for lab in peer_labels(pool):
    print(f"  {lab:<5}{SIM_WN[lab]:>3}" + "".join(f"{SIM_W[lab][m]:>7.2f}" for m in PLAYER_METRICS))
SIM = similar_players(pool, SIM_W)

VALUE_MINS = 2500

# 라인 평균 — 팀 진단의 포지션별 생산량과 영입 후보의 "현 라인 대비"(대체 가치)가 같이 쓰는 정의.
# 그 팀의 그 포지션 그룹(기본 그룹) 선수 전원의 축소값을 출전 시간으로 가중 평균한다.
# 후보 풀 밖 선수(600분 미만 등)도 같은 K·같은 사전평균으로 축소한다 — 출전이 짧을수록 그룹 평균에 가깝다.
# per-90 파일 자격이 없는 선수는 패스·태클 등이 결측이라 그 지표의 평균에서만 빠진다.
LINE_METRICS = {"npxG90": ["npxG90"], "xA90": ["xA90"], "pass90": ["pass90"],
                "ti90": ["tkl90", "int90"], "clr90": ["clr90"]}
PEER_OF = {p["pos"]: p["peer"] for p in pool}


def shrunk(p, m):
    if p.get(m) is None:
        return None
    if m + "_adj" in p:
        return p[m + "_adj"]
    k, mu = K_VALUES[m], PRIOR[PEER_OF[p["pos"]]][m]
    return (p[m] * p["mins"] + mu * k) / (p["mins"] + k)


def line_avg(grp, parts):
    vals = [(sum(v), p["mins"]) for p in grp
            for v in [[shrunk(p, m) for m in parts]] if None not in v]
    tot = sum(w for _, w in vals)
    return (round(sum(x * w for x, w in vals) / tot, 4) if tot else None), len(vals)


for lg, tt in teams.items():
    for name, t in tt.items():
        squad = [p for p in players if p["team_id"] == t["id"] and p["league"] == lg]
        t["squad_n"] = len(squad)
        for posg in POS_GROUPS:
            grp = [p for p in squad if p["pos"] == posg]
            bp = dict(n=len(grp), mins=sum(p["mins"] for p in grp))
            for k, parts in LINE_METRICS.items():
                bp[k], n_used = line_avg(grp, parts)
                if k == "pass90":
                    bp["n_el"] = n_used           # per-90 지표에 들어간 인원 (자격 있는 선수)
            # 라인 프로필: 13개 지표 각각의 라인 평균(같은 정의) — 사이트가 그룹 z로 바꿔 후보와 가중 코사인 비교
            bp["prof"] = {m: line_avg(grp, [m])[0] for m in PLAYER_METRICS}
            t.setdefault("by_pos", {})[posg] = bp
        top = sorted(squad, key=lambda p: -(p["npxG"] + p["xA"]))[:6]
        t["top"] = [dict(name=p["name"], pos=p["pos"], mins=p["mins"], g=p["g"], a=p["a"],
                         npxG90=round(p["npxG90"], 2), xA90=round(p["xA90"], 2)) for p in top]
        fn = form_teams.get((lg, t["id"]))
        t["form_now"] = (dict(mp=fn["mp"], xGD90=round(fn["xGD90"], 2), pts=fn["pts"])
                         if fn and FORM != SEASON else None)

for lg, tt in teams.items():
    for posg in POS_GROUPS:
        for m in ("npxG90", "xA90"):
            vals = [t["by_pos"][posg][m] or 0.0 for t in tt.values()]
            for t in tt.values():
                t["by_pos"][posg].setdefault("pct", {})[m] = round(pct_rank(vals, t["by_pos"][posg][m] or 0.0), 1)


def rnd(x, k=3):
    return None if x is None else round(x, k)


def slim(p):
    o = dict(id=p["id"], n=p["name"], t=p["team"], tid=p["team_id"], l=p["league"], p=p["pos"], pg=p["peer"],
             pc_codes=p["pos_codes"], m=p["mins"], gm=p["games"], g=p["g"], pk=p["pkg"],
             a=p["a"], xG=rnd(p["xG"], 2), fin=round(p["fin"], 2))
    for k in PLAYER_METRICS:
        o[k] = round(p[k], 3)                       # 원값 — 표에 표시
        o[k + "_adj"] = round(p[k + "_adj"], 3)     # 축소값 — 백분위·역할 점수 계산에 쓴 값
    for k in DISPLAY_METRICS:
        o[k] = rnd(p[k], 2 if k == "rating" else 3)  # 표시 전용, 없으면 null
    o["pc"] = {k: p["pct"][k] for k in PLAYER_METRICS}
    o["pa"] = {k: p["pctA"][k] for k in PLAYER_METRICS}
    o["r"] = p["role"]                                            # z 합성값의 풀 안 백분위 순위
    o["rz"] = {k: round(v, 3) for k, v in p["role_z"].items()}    # z 합성값 원본
    # 비슷한 선수 SIM_N명: [id, 리그, 유사도]
    o["sim"] = [[q["id"], q["league"], round(s, 3)] for q, s in SIM[(p["league"], p["id"])]]
    fp = form_players.get((p["league"], p["id"]))
    if fp and fp["mins"] >= 90 and FORM != SEASON:
        o["now"] = dict(m=fp["mins"], g=fp["g"], a=fp["a"],
                        npxG90=round(fp["npxG90"], 2), xA90=round(fp["xA90"], 2))
    return o


KST = timezone(timedelta(hours=9))
out = dict(
    meta=dict(source="FotMob", season=season_label(SEASON),
              form_season=season_label(FORM), leagues=LEAGUES, pool_min=POOL_MIN,
              generated=datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
              season_note=SEASON_NOTE,
              sim_w={lab: {m: round(SIM_W[lab][m], 2) for m in PLAYER_METRICS} for lab in SIM_W},
              sim_w_n=SIM_WN,
              value_mins=VALUE_MINS,
              # 13개 지표 그룹 z 기준(축소값) — 유사 선수·현 라인 유형 비교가 같은 기준을 쓴다
              zstats={lab: {m: [round(mu, 4), round(sd, 4)] for m, (mu, sd) in st.items()}
                      for lab, st in z_stats(pool).items()},
              style=[dict(k=k, raw=raw, label=lab) for k, raw, lab in STYLE],
              style_rare_cut=round(STYLE_RARE_CUT, 2), style_stab=STYLE_STAB,
              league_adj=LEAGUE,      # 대체 가치: (후보 − 현 라인) × VALUE_MINS/90 — 주전 한 시즌 가정
              pos_groups=list(POS_GROUPS),
              # pc(그룹 백분위)와 축소 사전평균은 peer_groups 기준. 합쳤으면 groups_merged에 기록
              peer_groups=counts, min_group=MIN_GROUP, groups_merged=MERGES,
              pool_rule=f"{POOL_MIN}분 이상 + 팀 경기 절반 이상 출전",
              role_score="축소값의 풀 전체 z-점수 가중합(rz) → 풀 안 백분위 순위(r)",
              npxg_note=f"npxG = xG − {PK_XG} × PK골 (근사, 실축 PK는 빼지 못함)",
              shrink=dict(k=K_INFO, prior={g: {m: round(v, 3) for m, v in pr.items()}
                                           for g, pr in PRIOR.items()})),
    teams={lg: list(tt.values()) for lg, tt in teams.items()},
    players=[slim(p) for p in pool],
    # 7단계: 사이트에서 "출전 경기 부족으로 순위 제외"로 따로 보여준다
    excluded=[dict(id=p["id"], n=p["name"], t=p["team"], tid=p["team_id"], l=p["league"], p=p["pos"],
                   m=p["mins"], gm=p["games"], team_mp=p["team_mp"])
              for p in sorted(excluded, key=lambda p: -p["mins"])],
    roles={k: v["label"] for k, v in ROLES.items()},
    role_groups={k: v["groups"] for k, v in ROLES.items()},
    role_weights={k: v["w"] for k, v in ROLES.items()},
)

os.makedirs(DOCS, exist_ok=True)
with open(os.path.join(DOCS, "data.json"), "w", encoding="utf-8") as fp:
    json.dump(out, fp, ensure_ascii=False, separators=(",", ":"))

tpl = open(TEMPLATE, encoding="utf-8").read().replace("/*__DATA__*/", "")
SPLIT = "<!--BODY-->"                  # template.html에서 이 표지 앞은 <head>, 뒤는 <body>
i = tpl.index(SPLIT)
head_part, body_part = tpl[:i], tpl[i:]
html = ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="description" content="프리미어리그·라리가 팀을 FotMob 지표로 진단하고 '
        '필요한 자원을 찾는 스카우팅 대시보드">\n'
        '<link rel="icon" href="data:image/svg+xml,'
        '%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 32 32%27%3E'
        '%3Ccircle cx=%2716%27 cy=%2716%27 r=%2714%27 fill=%27%23111%27/%3E'
        '%3Cpath d=%27M16 7l6 4.4-2.3 7H12.3L10 11.4z%27 fill=%27%23fff%27/%3E%3C/svg%3E">\n'
        '<style>body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n'
        + head_part + "</head>\n<body>\n" + body_part + "\n</body>\n</html>\n")
with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as fp:
    fp.write(html)

print(f"팀 {sum(len(v) for v in out['teams'].values())}개 · 후보 {len(out['players'])}명")
print("docs/data.json, docs/index.html 생성 완료")
