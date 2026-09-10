"""data/understat_raw.json  ->  docs/data.json + docs/index.html

기준 시즌은 자동으로 정한다:
  현재 시즌이 15경기 이상 치렀으면 현재 시즌, 아니면 직전 시즌.
  (9월에는 직전 시즌이 기준, 12월쯤 자동으로 현재 시즌으로 넘어간다.)
표준 라이브러리만 사용한다.
"""
import json, os, sys
from datetime import date, timezone, datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW = os.path.join(ROOT, "data", "understat_raw.json")
DOCS = os.path.join(ROOT, "docs")
TEMPLATE = os.path.join(ROOT, "site", "template.html")

LEAGUES = {"EPL": "Premier League", "La_liga": "La Liga"}
POOL_MIN = 600            # 후보 풀 최소 출전 시간(분)
SWITCH_AFTER = 15         # 현재 시즌이 이만큼 치르면 기준 시즌을 넘긴다

PLAYER_METRICS = ["npxG90", "xA90", "sh90", "kp90", "xGChain90", "xGBuildup90"]
TEAM_METRICS = [("npxG90", True), ("npxGA90", False), ("npxGD90", True),
                ("deep90", True), ("deep_allowed90", False),
                ("ppda", False), ("ppda_allowed", True),
                ("finishing", True), ("keeping", True)]
ROLES = {
    "finisher":   {"label": "골 결정력 (Finisher)",  "w": {"npxG90": .55, "sh90": .25, "xGChain90": .20}},
    "creator":    {"label": "찬스 메이킹 (Creator)", "w": {"xA90": .50, "kp90": .30, "xGChain90": .20}},
    "progressor": {"label": "빌드업 (Progressor)",   "w": {"xGBuildup90": .60, "xGChain90": .40}},
    "allround":   {"label": "공격 종합 (All-round)", "w": {"npxG90": .30, "xA90": .30, "xGChain90": .25, "sh90": .15}},
}
POSNAME = {"F": "FW", "M": "MF", "D": "DF", "GK": "GK", "S": "SUB"}


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def season_label(y):
    return f"{y}/{str(y + 1)[2:]}"


def pct_rank(vals, v):
    n = len(vals)
    if not n:
        return 50.0
    below = sum(1 for x in vals if x < v)
    equal = sum(1 for x in vals if x == v)
    return 100.0 * (below + 0.5 * equal) / n


def primary_pos(code):
    toks = [c for c in code.split() if c != "S"] or ["S"]
    order = ["F", "M", "D", "GK", "S"]
    toks.sort(key=lambda x: order.index(x) if x in order else 9)
    return toks[0]


# --------------------------------------------------------------- load
d = json.load(open(RAW, encoding="utf-8"))
years = sorted({int(k.rsplit("_", 1)[1]) for k in d})
if not years:
    sys.exit("원본에 시즌이 없습니다.")
CUR = years[-1]


def matches_played(yr):
    n = 0
    for lg in LEAGUES:
        t = d.get(f"{lg}_{yr}", {}).get("teams") or {}
        n = max(n, max((len(v.get("history", [])) for v in t.values()), default=0))
    return n


SEASON = CUR if matches_played(CUR) >= SWITCH_AFTER else CUR - 1
FORM = CUR
print(f"기준 시즌 {season_label(SEASON)} ({matches_played(SEASON)}경기) · 최근 폼 {season_label(FORM)} ({matches_played(FORM)}경기)")
if f"{list(LEAGUES)[0]}_{SEASON}" not in d:
    sys.exit(f"기준 시즌 {SEASON} 데이터가 원본에 없습니다.")


# --------------------------------------------------------------- teams
def team_block(lg, yr):
    out = {}
    for tid, t in (d.get(f"{lg}_{yr}", {}).get("teams") or {}).items():
        h = t.get("history") or []
        n = len(h)
        if n == 0:
            continue
        a = dict(xG=0, xGA=0, npxG=0, npxGA=0, deep=0, deep_allowed=0, scored=0,
                 missed=0, pts=0, xpts=0, pa=0, pd=0, aa=0, ad=0)
        for m in h:
            a["xG"] += m["xG"]; a["xGA"] += m["xGA"]
            a["npxG"] += m["npxG"]; a["npxGA"] += m["npxGA"]
            a["deep"] += m["deep"]; a["deep_allowed"] += m["deep_allowed"]
            a["scored"] += m["scored"]; a["missed"] += m["missed"]
            a["pts"] += m["pts"]; a["xpts"] += m["xpts"]
            a["pa"] += m["ppda"]["att"]; a["pd"] += m["ppda"]["def"]
            a["aa"] += m["ppda_allowed"]["att"]; a["ad"] += m["ppda_allowed"]["def"]
        out[t["title"]] = dict(
            id=tid, name=t["title"], league=lg, mp=n,
            npxG90=a["npxG"] / n, npxGA90=a["npxGA"] / n,
            npxGD90=(a["npxG"] - a["npxGA"]) / n,
            gf90=a["scored"] / n, ga90=a["missed"] / n, gf=a["scored"], ga=a["missed"],
            deep90=a["deep"] / n, deep_allowed90=a["deep_allowed"] / n,
            ppda=a["pa"] / max(a["pd"], 1), ppda_allowed=a["aa"] / max(a["ad"], 1),
            pts=a["pts"], xpts=round(a["xpts"], 1),
            finishing=a["scored"] - a["xG"], keeping=a["xGA"] - a["missed"],
            form=[m["result"] for m in h[-6:]],
            xg_series=[round(m["npxG"], 2) for m in h],
            xga_series=[round(m["npxGA"], 2) for m in h],
        )
    return out


def player_block(lg, yr):
    rows = []
    for p in (d.get(f"{lg}_{yr}", {}).get("players") or []):
        mins = f(p["time"])
        if mins <= 0:
            continue
        n90 = mins / 90
        rows.append(dict(
            id=p["id"], name=p["player_name"], team=p["team_title"], league=lg,
            pos=POSNAME[primary_pos(p["position"])],
            mins=int(mins), games=int(f(p["games"])),
            g=int(f(p["goals"])), npg=int(f(p["npg"])), a=int(f(p["assists"])),
            npxG90=f(p["npxG"]) / n90, xA90=f(p["xA"]) / n90,
            sh90=f(p["shots"]) / n90, kp90=f(p["key_passes"]) / n90,
            xGChain90=f(p["xGChain"]) / n90, xGBuildup90=f(p["xGBuildup"]) / n90,
            fin=f(p["npg"]) - f(p["npxG"]),
            npxG=f(p["npxG"]), xA=f(p["xA"]),
        ))
    return rows


teams = {lg: team_block(lg, SEASON) for lg in LEAGUES}
players = [r for lg in LEAGUES for r in player_block(lg, SEASON)]

form_teams = {}
for lg in LEAGUES:
    form_teams.update(team_block(lg, FORM))
form_players = {(r["league"], r["name"]): r for lg in LEAGUES for r in player_block(lg, FORM)}

for lg, tt in teams.items():
    for m, higher in TEAM_METRICS:
        vals = [t[m] for t in tt.values()]
        for t in tt.values():
            p = pct_rank(vals, t[m])
            t.setdefault("pct", {})[m] = round(p if higher else 100 - p, 1)

pool = [p for p in players if p["mins"] >= POOL_MIN and p["pos"] in ("FW", "MF", "DF")]
for posg in ("FW", "MF", "DF"):
    grp = [p for p in pool if p["pos"] == posg]
    for m in PLAYER_METRICS:
        vals = [x[m] for x in grp]
        for x in grp:
            x.setdefault("pct", {})[m] = round(pct_rank(vals, x[m]), 1)
for m in PLAYER_METRICS:
    vals = [x[m] for x in pool]
    for x in pool:
        x.setdefault("pctA", {})[m] = round(pct_rank(vals, x[m]), 1)
for p in pool:
    p["role"] = {k: round(sum(p["pctA"][m] * w for m, w in r["w"].items()), 1)
                 for k, r in ROLES.items()}

for lg, tt in teams.items():
    for name, t in tt.items():
        squad = [p for p in players if p["team"] == name and p["league"] == lg]
        t["squad_n"] = len(squad)
        for posg in ("FW", "MF", "DF"):
            grp = [p for p in squad if p["pos"] == posg]
            mins = sum(p["mins"] for p in grp) or 1
            t.setdefault("by_pos", {})[posg] = dict(
                n=len(grp),
                npxG90=round(sum(p["npxG"] for p in grp) / (mins / 90), 3),
                xA90=round(sum(p["xA"] for p in grp) / (mins / 90), 3))
        top = sorted(squad, key=lambda p: -(p["npxG"] + p["xA"]))[:6]
        t["top"] = [dict(name=p["name"], pos=p["pos"], mins=p["mins"], g=p["g"], a=p["a"],
                         npxG90=round(p["npxG90"], 2), xA90=round(p["xA90"], 2)) for p in top]
        fn = form_teams.get(name)
        t["form_now"] = (dict(mp=fn["mp"], npxGD90=round(fn["npxGD90"], 2), pts=fn["pts"])
                         if fn and FORM != SEASON else None)

for lg, tt in teams.items():
    for posg in ("FW", "MF", "DF"):
        for m in ("npxG90", "xA90"):
            vals = [t["by_pos"][posg][m] for t in tt.values()]
            for t in tt.values():
                t["by_pos"][posg].setdefault("pct", {})[m] = round(pct_rank(vals, t["by_pos"][posg][m]), 1)


def slim(p):
    o = dict(n=p["name"], t=p["team"], l=p["league"], p=p["pos"], m=p["mins"],
             gm=p["games"], g=p["g"], a=p["a"], fin=round(p["fin"], 2))
    for k in PLAYER_METRICS:
        o[k] = round(p[k], 3)
    o["pc"] = {k: p["pct"][k] for k in PLAYER_METRICS}
    o["pa"] = {k: p["pctA"][k] for k in PLAYER_METRICS}
    o["r"] = p["role"]
    fp = form_players.get((p["league"], p["name"]))
    if fp and fp["mins"] >= 90 and FORM != SEASON:
        o["now"] = dict(m=fp["mins"], g=fp["g"], a=fp["a"],
                        npxG90=round(fp["npxG90"], 2), xA90=round(fp["xA90"], 2))
    return o


KST = timezone(timedelta(hours=9))
out = dict(
    meta=dict(source="Understat", season=season_label(SEASON),
              form_season=season_label(FORM), leagues=LEAGUES, pool_min=POOL_MIN,
              generated=datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")),
    teams={lg: list(tt.values()) for lg, tt in teams.items()},
    players=[slim(p) for p in pool],
    roles={k: v["label"] for k, v in ROLES.items()},
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
