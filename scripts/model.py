"""build.py와 backtest.py가 함께 쓰는 모델 정의 (FotMob).

백테스트는 '실제로 배포되는 모델'을 검증해야 의미가 있다. 점수 계산이
두 스크립트에 따로 있으면 조용히 갈라지므로 여기 한 곳에만 둔다.
표준 라이브러리만 사용한다. 원본 구조와 지표 규약은 FOTMOB_MIGRATION.md 1절.
"""
import sys

LEAGUES = {"EPL": "Premier League", "La_liga": "La Liga"}
POOL_MIN = 600            # 후보 풀 최소 출전 시간(분)

# npxG는 FotMob에 없다. xG − PK_XG × PK골로 근사한다 (실축한 PK는 못 뺀다).
PK_XG = 0.79

# 역할 점수·축소·백분위에 쓰는 per-90 지표.
PLAYER_METRICS = ["npxG90", "xA90", "sh90", "drib90", "chances90", "bigch90",
                  "pass90", "long90", "tkl90", "int90", "rec90", "clr90", "blk90"]
# 표시만 하는 지표 — 시즌에 따라 없을 수 있다(None).
DISPLAY_METRICS = ["xG90", "lbp90", "defc90", "pwa3_90", "rating"]

# 표본 크기 보정(shrinkage). K = 실제값과 사전평균을 50:50으로 섞는 분 수.
# 연속 두 시즌 데이터로 직접 추정하고, 표본이 모자라면 아래 기본값으로 물러선다.
# FotMob 지표용 기본값은 아직 근거가 없는 임시값이다 — 추정치가 항상 우선한다.
DEFAULT_K = {m: 900 for m in PLAYER_METRICS}
K_MIN_SAMPLE = 50         # 이보다 표본이 적으면 기본값 사용
K_MIN_MINS = 600          # 두 시즌 모두 이만큼 뛴 선수만 상관계수 표본에 넣는다

# 출발점 가중치 (FOTMOB_MIGRATION.md 3절). groups = 이 역할을 추천할 포지션 그룹.
# 빌드업은 lbp_total을 뺐다: EPL 23/24·24/25, 라리가 23/24에 파일이 없어서
# 넣으면 시즌마다 역할 정의가 달라지고 백테스트가 성립하지 않는다.
ROLES = {
    "finisher":   {"label": "골 결정력 (Finisher)",   "groups": ["ST", "AM"],
                   "w": {"npxG90": .55, "sh90": .25, "drib90": .20}},
    "creator":    {"label": "찬스 메이킹 (Creator)",  "groups": ["AM", "CM", "FB"],
                   "w": {"xA90": .50, "chances90": .30, "bigch90": .20}},
    "progressor": {"label": "빌드업 (Progressor)",    "groups": ["CM", "CB"],
                   "w": {"pass90": .55, "long90": .45}},
    "ballwinner": {"label": "볼 탈취 (Ball-winner)",  "groups": ["CM", "FB"],
                   "w": {"tkl90": .35, "int90": .35, "rec90": .30}},
    "boxdef":     {"label": "박스 수비 (Box defender)", "groups": ["CB"],
                   "w": {"clr90": .45, "int90": .30, "blk90": .25}},
}
POS_GROUPS = ("CB", "FB", "CM", "AM", "ST")

# ------------------------------------------------------------------ 포지션
# FotMob 코드는 포메이션 안의 자리다: 십의 자리 = 줄, 일의 자리 = 오른쪽(2)→왼쪽(8).
# 2026-11-13 코드별 선수 10명으로 검증 (FOTMOB_MIGRATION.md 2절).
POS_CODE = {11: "GK", 32: "FB", 38: "FB",
            33: "CB", 34: "CB", 35: "CB", 36: "CB", 37: "CB",      # 37 = 스리백 왼쪽
            51: "FB", 59: "FB", 62: "FB", 68: "FB",                # 윙백
            64: "CM", 65: "CM", 66: "CM", 73: "CM", 74: "CM", 75: "CM", 76: "CM", 77: "CM",
            82: "AM", 83: "AM", 84: "AM", 85: "AM", 86: "AM", 87: "AM", 88: "AM",
            103: "AM", 107: "AM",                                  # 스리톱의 좌우 윙어
            95: "ST", 104: "ST", 105: "ST", 106: "ST", 115: "ST", 116: "ST"}
WIDE_MID = {71: "FB", 79: "FB", 72: "AM", 78: "AM"}                # 규칙 3(기본값)


def is_def_code(c):
    return 30 <= c <= 39 or 50 <= c <= 59 or c in (62, 68)


def is_att_code(c):
    return 80 <= c <= 89 or 100 <= c <= 119


def pos_group(positions):
    """(그룹, 규칙). 주 포지션이 71·72·78·79면 다음 코드들을 순서대로 본다.
    71·72·78·79와, 수비도 공격도 아닌 코드(CM 줄 등)는 건너뛰고
    처음 나오는 수비 코드면 FB(rule=def), 공격 코드면 AM(rule=att),
    끝까지 없으면 71·79→FB, 72·78→AM(rule=default)."""
    if not positions:
        return "UNK", "none"
    c = positions[0]
    if c in WIDE_MID:
        for x in positions[1:]:
            if x in WIDE_MID:
                continue
            if is_def_code(x):
                return "FB", "def"
            if is_att_code(x):
                return "AM", "att"
        return WIDE_MID[c], "default"
    return POS_CODE.get(c, "UNK"), "code"


# ------------------------------------------------------------------ 공통
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


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (vx * vy) ** 0.5


def ranks(xs):
    """동점은 평균 순위로 (스피어만용)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(xs, ys):
    return pearson(ranks(xs), ranks(ys))


def season_block(raw, lg, yr):
    return raw.get(f"{lg}_{yr}") or {}


def played(raw, yr):
    """그 시즌 두 리그 중 가장 많이 치른 팀의 경기 수."""
    return max((r["played"] for lg in LEAGUES
                for r in season_block(raw, lg, yr).get("table") or []), default=0)


# ------------------------------------------------------------------ 선수
class _Stat:
    """지표 파일 하나. 없는 선수 처리 규칙을 담는다.
    - 합계형(total=True): 값이 0인 선수는 빠져 있다 → 0
    - per-90형: 최소 출전 기준 + 값 0인 선수가 빠져 있다 →
      파일의 최저 출전 분 이상 뛰었으면 0, 그보다 적게 뛰었으면 결측(None)
    - 평점(zero_ok=False): 없으면 항상 결측."""

    def __init__(self, f_, total, zero_ok=True):
        self.ok = f_ is not None
        self.rows = {r["ParticiantId"]: r for r in (f_["rows"] if f_ else [])}
        self.floor = min((r["MinutesPlayed"] for r in self.rows.values()), default=0)
        self.total, self.zero_ok = total, zero_ok

    def get(self, pid, mins, field="StatValue"):
        if not self.ok:
            return None
        r = self.rows.get(pid)
        if r is not None:
            return f(r[field])
        if not self.zero_ok:
            return None
        return 0.0 if (self.total or mins >= self.floor) else None


def _div(x, n90):
    return None if x is None else x / n90


def player_rows(raw, yr):
    """그 시즌 선수 목록. 모집단은 mins_played 파일, 조인 키는 ParticiantId."""
    rows = []
    for lg in LEAGUES:
        st = season_block(raw, lg, yr).get("stats")
        if not st:
            continue
        S = {
            "goals": _Stat(st["goals"], True), "xg": _Stat(st["expected_goals"], True),
            "xa": _Stat(st["expected_assists"], True),
            "chances": _Stat(st["total_att_assist"], True),
            "bigch": _Stat(st["big_chance_created"], True),
            "sh": _Stat(st["total_scoring_att"], False),
            "pass": _Stat(st["accurate_pass"], False),
            "long": _Stat(st["accurate_long_balls"], False),
            "drib": _Stat(st["won_contest"], False),
            "tkl": _Stat(st["total_tackle"], False), "int": _Stat(st["interception"], False),
            "clr": _Stat(st["effective_clearance"], False),
            "rec": _Stat(st["ball_recovery"], False),
            "blk": _Stat(st["outfielder_block"], False),
            "defc": _Stat(st["defensive_contributions"], False),
            "pwa3": _Stat(st["poss_won_att_3rd"], False),
            "lbp": _Stat(st.get("lbp_total"), False),
            "rating": _Stat(st["rating"], False, zero_ok=False),
        }

        for r in st["mins_played"]["rows"]:
            pid, mins = r["ParticiantId"], f(r["StatValue"])
            if mins <= 0:
                continue
            n90 = mins / 90

            def tot90(key):
                """합계(SubStatValue)에서 per-90을 직접 계산 — 반올림된 per-90을 쓰지 않는다."""
                return _div(S[key].get(pid, mins, "SubStatValue"), n90)

            grp, rule = pos_group(r.get("Positions") or [])
            g = S["goals"].get(pid, mins)
            pkg = S["goals"].get(pid, mins, "SubStatValue")
            xg = S["xg"].get(pid, mins)
            xa = S["xa"].get(pid, mins)
            npxg = max(xg - PK_XG * pkg, 0.0)
            rows.append(dict(
                id=pid, name=r["ParticipantName"], team=r["TeamName"], team_id=r["TeamId"],
                league=lg, pos=grp, pos_rule=rule, pos_codes=r.get("Positions") or [],
                mins=int(mins), games=int(f(r["SubStatValue"])),
                g=int(g), pkg=int(pkg), npg=int(g - pkg),
                a=int(S["xa"].get(pid, mins, "SubStatValue")),
                xG=xg, npxG=npxg, xA=xa,
                npxG90=npxg / n90, xA90=xa / n90, xG90=xg / n90,
                # 합계만 있는 파일
                chances90=S["chances"].get(pid, mins) / n90,
                bigch90=S["bigch"].get(pid, mins) / n90,
                # per-90만 있는 파일 (SubStatValue가 비율이라 합계를 복원할 수 없다)
                sh90=S["sh"].get(pid, mins), pass90=S["pass"].get(pid, mins),
                long90=S["long"].get(pid, mins), drib90=S["drib"].get(pid, mins),
                pwa3_90=S["pwa3"].get(pid, mins),
                # per-90과 합계가 같이 있는 파일 → 합계에서 계산
                tkl90=tot90("tkl"), int90=tot90("int"), clr90=tot90("clr"),
                rec90=tot90("rec"), blk90=tot90("blk"), defc90=tot90("defc"),
                lbp90=tot90("lbp"),
                rating=S["rating"].get(pid, mins),
                fin=(g - pkg) - npxg,
            ))
    return rows


def pool_rows(raw, yr):
    """후보 풀: POOL_MIN분 이상 뛴 필드 플레이어. 역할 지표가 결측이면 멈춘다."""
    pool = [p for p in player_rows(raw, yr) if p["mins"] >= POOL_MIN and p["pos"] in POS_GROUPS]
    bad = [(p["name"], m) for p in pool for m in PLAYER_METRICS if p[m] is None]
    if bad:
        sys.exit(f"{season_label(yr)} 후보 풀에 결측 지표가 있다 — 최소 출전 기준이 "
                 f"{POOL_MIN}분보다 높은 파일? {bad[:10]}")
    return pool


# ------------------------------------------------------------------ 모델
def estimate_k(raw, years, log=None):
    """연속 두 시즌 모두 K_MIN_MINS 이상 뛴 선수로 시즌간 상관계수를 구해
    K ≈ 평균출전분 × (1−r)/r 로 추정한다. 표본이 모자라면 기본값.
    같은 선수가 한 시즌에 두 리그에 있으면 (리그, id)별로 따로 본다."""
    cache = {y: {(p["league"], p["id"]): p for p in player_rows(raw, y)
                 if p["pos"] in POS_GROUPS} for y in years}
    pairs = [(y, y + 1) for y in years if y + 1 in cache]
    rows = []
    for y1, y2 in pairs:
        by_id = {}
        for (_, pid), p in cache[y2].items():
            if p["mins"] >= K_MIN_MINS:
                by_id.setdefault(pid, p)
        for a in cache[y1].values():
            b = by_id.get(a["id"])
            if b and a["mins"] >= K_MIN_MINS:
                rows.append((a, b))
    if log:
        log(f"K 추정 — 연속 시즌쌍 {[f'{season_label(a)}→{season_label(b)}' for a, b in pairs]}, "
            f"양 시즌 {K_MIN_MINS}분+ 선수 {len(rows)}건")

    K, info = {}, {}
    for m in PLAYER_METRICS:
        pr = [(a, b) for a, b in rows if a[m] is not None and b[m] is not None]
        n = len(pr)
        r = pearson([a[m] for a, _ in pr], [b[m] for _, b in pr]) if n >= K_MIN_SAMPLE else None
        if n < K_MIN_SAMPLE or r is None or r <= 0:
            K[m] = DEFAULT_K[m]
            info[m] = dict(K=K[m], source="default", n=n, r=None if r is None else round(r, 3))
            if log:
                log(f"  {m:10s} 표본 {n}명, r={r} → 기본값 K={DEFAULT_K[m]} 사용")
        else:
            mean_mins = sum(a["mins"] for a, _ in pr) / n
            K[m] = round(mean_mins * (1 - r) / r)
            info[m] = dict(K=K[m], source="estimated", n=n, r=round(r, 3),
                           mean_mins=round(mean_mins))
            if log:
                log(f"  {m:10s} 표본 {n}명, r={r:.3f}, 평균 {mean_mins:.0f}분 → 추정 K={K[m]}")
    return K, info


def prior_means(pool):
    """사전평균 = 같은 포지션 그룹의 출전시간 가중 평균."""
    out = {}
    for posg in POS_GROUPS:
        grp = [p for p in pool if p["pos"] == posg]
        tot = sum(p["mins"] for p in grp) or 1
        out[posg] = {m: sum(p[m] * p["mins"] for p in grp) / tot for m in PLAYER_METRICS}
    return out


def apply_shrinkage(pool, K, prior):
    """조정값 = (실제값 × 분 + 사전평균 × K) / (분 + K). p[m + '_adj']에 넣는다."""
    for p in pool:
        for m in PLAYER_METRICS:
            k = K[m]
            p[m + "_adj"] = (p[m] * p["mins"] + prior[p["pos"]][m] * k) / (p["mins"] + k)


def score_pool(pool, suffix="_adj"):
    """포지션 그룹 백분위(pct) · 풀 전체 백분위(pctA) · 역할 점수(role)를 매긴다.
    suffix='_adj'면 축소값, ''이면 원값 기준 — 백테스트 비교군에 쓴다."""
    for posg in POS_GROUPS:
        grp = [p for p in pool if p["pos"] == posg]
        for m in PLAYER_METRICS:
            vals = [x[m + suffix] for x in grp]
            for x in grp:
                x.setdefault("pct", {})[m] = round(pct_rank(vals, x[m + suffix]), 1)
    for m in PLAYER_METRICS:
        vals = [x[m + suffix] for x in pool]
        for x in pool:
            x.setdefault("pctA", {})[m] = round(pct_rank(vals, x[m + suffix]), 1)
    for p in pool:
        p["role"] = {k: round(sum(p["pctA"][m] * w for m, w in r["w"].items()), 1)
                     for k, r in ROLES.items()}
    return pool


def build_pool(raw, yr, K=None):
    """그 시즌의 후보 풀을 만들고 축소·백분위·역할 점수까지 매긴 결과를 낸다.
    K를 주지 않으면 축소 없이(원값 기준) 점수를 매긴다."""
    pool = pool_rows(raw, yr)
    if K is None:
        return score_pool(pool, suffix="")
    apply_shrinkage(pool, K, prior_means(pool))
    return score_pool(pool, suffix="_adj")
