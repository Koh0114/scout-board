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
                   estimate_k, prior_means, apply_shrinkage, score_pool)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW = os.path.join(ROOT, "data", "fotmob_raw.json")
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
if f"{list(LEAGUES)[0]}_{SEASON}" not in d:
    sys.exit(f"기준 시즌 {SEASON} 데이터가 원본에 없습니다.")


K_VALUES, K_INFO = estimate_k(d, years, log=print)


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
            # 점유율 보정: 상대가 공을 50% 가진 것으로 환산 (FOTMOB_MIGRATION.md 3절)
            defact_adj=defact * 50 / (100 - p),
            form=results.get(tid, [])[-6:],
        )
    return out


teams = {lg: team_block(lg, SEASON) for lg in LEAGUES}
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

for lg, tt in teams.items():
    for name, t in tt.items():
        squad = [p for p in players if p["team_id"] == t["id"] and p["league"] == lg]
        t["squad_n"] = len(squad)
        for posg in POS_GROUPS:
            grp = [p for p in squad if p["pos"] == posg]
            mins = sum(p["mins"] for p in grp) or 1
            t.setdefault("by_pos", {})[posg] = dict(
                n=len(grp),
                npxG90=round(sum(p["npxG"] for p in grp) / (mins / 90), 3),
                xA90=round(sum(p["xA"] for p in grp) / (mins / 90), 3))
        top = sorted(squad, key=lambda p: -(p["npxG"] + p["xA"]))[:6]
        t["top"] = [dict(name=p["name"], pos=p["pos"], mins=p["mins"], g=p["g"], a=p["a"],
                         npxG90=round(p["npxG90"], 2), xA90=round(p["xA90"], 2)) for p in top]
        fn = form_teams.get((lg, t["id"]))
        t["form_now"] = (dict(mp=fn["mp"], xGD90=round(fn["xGD90"], 2), pts=fn["pts"])
                         if fn and FORM != SEASON else None)

for lg, tt in teams.items():
    for posg in POS_GROUPS:
        for m in ("npxG90", "xA90"):
            vals = [t["by_pos"][posg][m] for t in tt.values()]
            for t in tt.values():
                t["by_pos"][posg].setdefault("pct", {})[m] = round(pct_rank(vals, t["by_pos"][posg][m]), 1)


def rnd(x, k=3):
    return None if x is None else round(x, k)


def slim(p):
    o = dict(id=p["id"], n=p["name"], t=p["team"], l=p["league"], p=p["pos"], pg=p["peer"],
             pc_codes=p["pos_codes"], m=p["mins"], gm=p["games"], g=p["g"], pk=p["pkg"],
             a=p["a"], xG=rnd(p["xG"], 2), fin=round(p["fin"], 2))
    for k in PLAYER_METRICS:
        o[k] = round(p[k], 3)                       # 원값 — 표에 표시
        o[k + "_adj"] = round(p[k + "_adj"], 3)     # 축소값 — 백분위·역할 점수 계산에 쓴 값
    for k in DISPLAY_METRICS:
        o[k] = rnd(p[k], 2 if k == "rating" else 3)  # 표시 전용, 없으면 null
    o["pc"] = {k: p["pct"][k] for k in PLAYER_METRICS}
    o["pa"] = {k: p["pctA"][k] for k in PLAYER_METRICS}
    o["r"] = p["role"]
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
              pos_groups=list(POS_GROUPS),
              # pc(그룹 백분위)와 축소 사전평균은 peer_groups 기준. 합쳤으면 groups_merged에 기록
              peer_groups=counts, min_group=MIN_GROUP, groups_merged=MERGES,
              pool_rule=f"{POOL_MIN}분 이상 + 팀 경기 절반 이상 출전",
              npxg_note=f"npxG = xG − {PK_XG} × PK골 (근사, 실축 PK는 빼지 못함)",
              shrink=dict(k=K_INFO, prior={g: {m: round(v, 3) for m, v in pr.items()}
                                           for g, pr in PRIOR.items()})),
    teams={lg: list(tt.values()) for lg, tt in teams.items()},
    players=[slim(p) for p in pool],
    # 7단계: 사이트에서 "출전 경기 부족으로 순위 제외"로 따로 보여준다
    excluded=[dict(id=p["id"], n=p["name"], t=p["team"], l=p["league"], p=p["pos"],
                   m=p["mins"], gm=p["games"], team_mp=p["team_mp"])
              for p in sorted(excluded, key=lambda p: -p["mins"])],
    roles={k: v["label"] for k, v in ROLES.items()},
    role_groups={k: v["groups"] for k, v in ROLES.items()},
)

os.makedirs(DOCS, exist_ok=True)
with open(os.path.join(DOCS, "data.json"), "w", encoding="utf-8") as fp:
    json.dump(out, fp, ensure_ascii=False, separators=(",", ":"))

tpl = open(TEMPLATE, encoding="utf-8").read().replace("/*__DATA__*/", "")
SPLIT = "<header class=\"mast\">"
i = tpl.index(SPLIT)
head_part, body_part = tpl[:i], tpl[i:]
html = ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="description" content="프리미어리그·라리가 팀을 xG 지표로 진단하고 '
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
