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
# 결정력은 npxG/90 하나다: 슈팅·드리블을 섞은 가중합이 '지난 시즌 npxG/90 그대로'보다
# 두 시즌쌍 모두 낮았다(921daf3). 가중치는 백테스트로 고르지 않는다 — 시즌쌍 2개로
# 고르고 같은 2개로 검증하면 과적합이다.
ROLES = {
    "finisher":   {"label": "골 결정력 (Finisher)",   "groups": ["ST", "AM"],
                   "w": {"npxG90": 1.0}},
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

# 그룹 인원이 MIN_GROUP 미만이면 백분위가 거칠어지므로 이웃 그룹과 합친다.
# 합쳐도 모자라면 반복한다. 합친 그룹은 'AM+ST'처럼 이름 붙는다.
MIN_GROUP = 60
MERGE_PARTNER = {"ST": "AM", "AM": "ST", "FB": "CB", "CB": "FB", "CM": "AM"}

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


def completed(raw, yr):
    """두 리그 모두 모든 팀이 2×(팀 수−1)경기를 다 치렀으면 완주 시즌."""
    for lg in LEAGUES:
        t = season_block(raw, lg, yr).get("table") or []
        if not t or any(r["played"] < 2 * (len(t) - 1) for r in t):
            return False
    return True


# 점유율 보정 대상 (FOTMOB_MIGRATION.md 3절): 지표 × 50 / (100 − 팀 점유율).
# 공을 덜 가진 팀 선수일수록 수비 기회가 많다는 편향을 상대 점유율 50%로 환산한다.
# 선수 지표에는 기본 모델에서 쓰지 않고 백테스트 비교군으로만 둔다 (build_pool 참고).
# 팀 진단의 defact_adj는 build.py에서 따로 쓴다.
POSS_ADJ = ["tkl90", "int90", "rec90", "clr90", "blk90"]


def poss_adjust(p, m):
    return p[m] * 50 / (100 - p["poss"])


# ------------------------------------------------------------------ 선수
class _Stat:
    """지표 파일 하나. 없는 선수 처리 규칙을 담는다 (2026-11-13 8개 리그-시즌 전수 확인).
    - 합계형(total=True): 값이 0인 선수만 빠져 있다 → 0
    - per-90형: 팀 경기의 절반 이상 출전한 선수만 들어 있고, 그중 값이 0인 선수는 빠진다 →
      자격(eligible)이 있으면 0, 없으면 결측(None). 출전 '분' 기준이 아니다.
    - 평점(zero_ok=False): 기준이 따로 있어 없으면 항상 결측."""

    def __init__(self, f_, total, zero_ok=True):
        self.ok = f_ is not None
        self.rows = {r["ParticiantId"]: r for r in (f_["rows"] if f_ else [])}
        self.total, self.zero_ok = total, zero_ok

    def get(self, pid, eligible, field="StatValue"):
        if not self.ok:
            return None
        r = self.rows.get(pid)
        if r is not None:
            return f(r[field])
        if not self.zero_ok:
            return None
        return 0.0 if (self.total or eligible) else None


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
        # 소속팀 점유율. 시즌 중 이적한 선수는 FotMob이 준 TeamId(마지막 팀) 기준이다.
        team_poss = {r["TeamId"]: f(r["StatValue"])
                     for r in st["possession_percentage_team"]["rows"]}
        # per-90 파일 자격 판정용 팀 경기 수. 순위표가 아니라 같은 시점에 만들어진
        # 팀 지표 파일의 MatchesPlayed를 쓴다 (진행 중 시즌은 갱신 시점이 다르다).
        team_mp = {r["TeamId"]: int(r["MatchesPlayed"])
                   for r in st["possession_percentage_team"]["rows"]}
        league_mp = max(team_mp.values(), default=0)

        for r in st["mins_played"]["rows"]:
            pid, mins = r["ParticiantId"], f(r["StatValue"])
            if mins <= 0:
                continue
            n90 = mins / 90
            games = int(f(r["SubStatValue"]))
            el = games * 2 >= team_mp.get(r["TeamId"], league_mp)   # per-90 파일 자격

            def tot90(key):
                """합계(SubStatValue)에서 per-90을 직접 계산 — 반올림된 per-90을 쓰지 않는다."""
                return _div(S[key].get(pid, el, "SubStatValue"), n90)

            grp, rule = pos_group(r.get("Positions") or [])
            g = S["goals"].get(pid, el)
            pkg = S["goals"].get(pid, el, "SubStatValue")
            xg = S["xg"].get(pid, el)
            xa = S["xa"].get(pid, el)
            npxg = max(xg - PK_XG * pkg, 0.0)
            rows.append(dict(
                id=pid, name=r["ParticipantName"], team=r["TeamName"], team_id=r["TeamId"],
                league=lg, pos=grp, pos_rule=rule, pos_codes=r.get("Positions") or [],
                poss=team_poss.get(r["TeamId"], 50.0), eligible=el,
                team_mp=team_mp.get(r["TeamId"], league_mp),
                mins=int(mins), games=games,
                g=int(g), pkg=int(pkg), npg=int(g - pkg),
                a=int(S["xa"].get(pid, el, "SubStatValue")),
                xG=xg, npxG=npxg, xA=xa,
                npxG90=npxg / n90, xA90=xa / n90, xG90=xg / n90,
                # 합계만 있는 파일
                chances90=S["chances"].get(pid, el) / n90,
                bigch90=S["bigch"].get(pid, el) / n90,
                # per-90만 있는 파일 (SubStatValue가 비율이라 합계를 복원할 수 없다)
                sh90=S["sh"].get(pid, el), pass90=S["pass"].get(pid, el),
                long90=S["long"].get(pid, el), drib90=S["drib"].get(pid, el),
                pwa3_90=S["pwa3"].get(pid, el),
                # per-90과 합계가 같이 있는 파일 → 합계에서 계산
                tkl90=tot90("tkl"), int90=tot90("int"), clr90=tot90("clr"),
                rec90=tot90("rec"), blk90=tot90("blk"), defc90=tot90("defc"),
                lbp90=tot90("lbp"),
                rating=S["rating"].get(pid, el),
                fin=(g - pkg) - npxg,
            ))
    return rows


def in_pool(p):
    """후보 풀 조건: POOL_MIN분 이상 + 팀 경기 절반 이상 출전(per-90 파일 자격) + 필드 플레이어."""
    return p["mins"] >= POOL_MIN and p["eligible"] and p["pos"] in POS_GROUPS


def excluded_rows(rows):
    """POOL_MIN분은 넘었지만 출전 경기가 팀 경기의 절반 미만이라 풀에서 빠진 필드 플레이어."""
    return [p for p in rows if p["mins"] >= POOL_MIN and not p["eligible"] and p["pos"] in POS_GROUPS]


def pool_rows(raw, yr, rows=None):
    """후보 풀. 역할 지표가 결측이면 멈춘다 (자격 판정이 어긋났다는 뜻)."""
    pool = [p for p in (rows if rows is not None else player_rows(raw, yr)) if in_pool(p)]
    bad = [(p["name"], m) for p in pool for m in PLAYER_METRICS if p[m] is None]
    if bad:
        sys.exit(f"{season_label(yr)} 후보 풀에 결측 지표가 있다 — per-90 파일 자격 판정이 "
                 f"원본과 어긋났다: {bad[:10]}")
    return pool


def merge_groups(pool):
    """인원이 MIN_GROUP 미만인 그룹을 MERGE_PARTNER와 합친다. 각 선수에 p['peer']
    (사전평균·그룹 백분위를 계산할 그룹 이름)를 넣고, 합친 기록 목록을 돌려준다."""
    members = {g: [g] for g in POS_GROUPS}            # 그룹 이름 → 원래 그룹들
    base_n = {g: sum(1 for p in pool if p["pos"] == g) for g in POS_GROUPS}

    def n(label):
        return sum(base_n[b] for b in members[label])

    merges = []
    while len(members) > 1:
        small = [l for l in members if n(l) < MIN_GROUP]
        if not small:
            break
        lab = min(small, key=n)
        partner = next(MERGE_PARTNER[b] for b in members[lab] if MERGE_PARTNER[b] not in members[lab])
        into = next(l for l in members if partner in members[l])
        bases = sorted(members[lab] + members[into], key=POS_GROUPS.index)
        new = "+".join(bases)
        merges.append(dict(group=lab, n=n(lab), into=into, n_into=n(into), result=new,
                           n_result=n(lab) + n(into)))
        del members[lab], members[into]
        members[new] = bases
    label_of = {b: l for l, bs in members.items() for b in bs}
    for p in pool:
        p["peer"] = label_of[p["pos"]]
    return merges


def peer_labels(pool):
    return sorted({p["peer"] for p in pool}, key=lambda l: POS_GROUPS.index(l.split("+")[0]))


# ------------------------------------------------------------------ 모델
def pair_rows(raw, years, pairs=None):
    """시즌간 상관계수 표본: 완주한 연속 시즌쌍에서 두 시즌 모두 후보 풀(in_pool)에 든 선수의
    (앞 시즌 행, 뒤 시즌 행) 목록. K 추정과 유사도 가중치가 같은 표본을 쓴다."""
    years = [y for y in years if completed(raw, y)]
    if pairs is None:
        pairs = [(y, y + 1) for y in years if y + 1 in years]
    cache = {y: [p for p in player_rows(raw, y) if in_pool(p)]
             for y in {y for pr in pairs for y in pr}}
    rows = []
    for y1, y2 in pairs:
        by_id = {}
        for p in cache[y2]:
            by_id.setdefault(p["id"], p)
        for a in cache[y1]:
            b = by_id.get(a["id"])
            if b:
                rows.append((a, b))
    return pairs, rows


def estimate_k(raw, years, log=None, pairs=None):
    """연속 두 시즌 모두 K_MIN_MINS 이상 뛴 선수로 시즌간 상관계수를 구해
    K ≈ 평균출전분 × (1−r)/r 로 추정한다. 표본이 모자라면 기본값.
    두 시즌 모두 완주한 쌍만 쓴다 — 진행 중인 시즌은 출전 분이 짧아 r을 깎는다.
    표본은 두 시즌 모두 후보 풀 조건(in_pool)을 만족한 선수로, 모든 지표가 같은 표본이다.
    pairs를 주면 그 쌍만 쓴다 (백테스트에서 시험 쌍을 빼고 추정할 때)."""
    pairs, rows = pair_rows(raw, years, pairs)
    if log:
        log(f"K 추정 — 완주 시즌쌍 {[f'{season_label(a)}→{season_label(b)}' for a, b in pairs]}, "
            f"양 시즌 후보 풀 선수 {len(rows)}건")

    K, info = {}, {}
    for m in PLAYER_METRICS:
        pr = rows                     # in_pool이면 역할 지표에 결측이 없다
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
    """사전평균 = 같은 그룹(합친 그룹이면 합친 그룹)의 출전시간 가중 평균."""
    out = {}
    for lab in peer_labels(pool):
        grp = [p for p in pool if p["peer"] == lab]
        tot = sum(p["mins"] for p in grp) or 1
        out[lab] = {m: sum(p[m] * p["mins"] for p in grp) / tot for m in PLAYER_METRICS}
    return out


def apply_shrinkage(pool, K, prior):
    """조정값 = (실제값 × 분 + 사전평균 × K) / (분 + K). p[m + '_adj']에 넣는다."""
    for p in pool:
        for m in PLAYER_METRICS:
            k = K[m]
            p[m + "_adj"] = (p[m] * p["mins"] + prior[p["peer"]][m] * k) / (p["mins"] + k)


def score_pool(pool, suffix="_adj"):
    """그룹 백분위(pct) · 풀 전체 백분위(pctA) · 역할 점수를 매긴다.
    role_z    = 풀 전체 z-점수의 가중합 (배포 모델의 점수)
    role      = role_z의 풀 안 백분위 순위 0–100 (사이트 표시용, 순위는 role_z와 같다)
    role_pctw = 풀 전체 백분위의 가중합 (이전 모델, 백테스트 비교용)
    백분위 가중합은 두 시즌쌍 모두 z-점수보다 예측력이 낮았다(921daf3).
    suffix='_adj'면 축소값, ''이면 원값 기준 — 백테스트 비교군에 쓴다."""
    for lab in peer_labels(pool):
        grp = [p for p in pool if p["peer"] == lab]
        for m in PLAYER_METRICS:
            vals = [x[m + suffix] for x in grp]
            for x in grp:
                x.setdefault("pct", {})[m] = round(pct_rank(vals, x[m + suffix]), 1)
    zs = {}
    for m in PLAYER_METRICS:
        vals = [x[m + suffix] for x in pool]
        mu = sum(vals) / len(vals)
        sd = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        zs[m] = (mu, sd)
        for x in pool:
            x.setdefault("pctA", {})[m] = round(pct_rank(vals, x[m + suffix]), 1)
    for p in pool:
        p["role_pctw"] = {k: round(sum(p["pctA"][m] * w for m, w in r["w"].items()), 1)
                          for k, r in ROLES.items()}
        p["role_z"] = {k: sum((p[m + suffix] - zs[m][0]) / zs[m][1] * w for m, w in r["w"].items())
                       for k, r in ROLES.items()}
    for k in ROLES:
        vals = [p["role_z"][k] for p in pool]
        for p in pool:
            p.setdefault("role", {})[k] = round(pct_rank(vals, p["role_z"][k]), 1)
    return pool


SIM_N = 8                 # 선수당 "비슷한 선수" 수


def sim_weights(raw, years, labels, log=None):
    """유사도 지표 가중치 = 그 포지션 그룹 안에서의 시즌간 상관계수 r (음수면 0).
    표본은 K 추정과 같다(pair_rows). 그룹은 앞 시즌 포지션 기준이고, 합친 그룹(예: AM+ST)은
    구성 그룹 선수를 모두 쓴다. 그룹 표본이 K_MIN_SAMPLE 미만이면 그 그룹은 전체 표본의 r을 쓴다.
    반환: {그룹 이름: {지표: 가중치}}, {그룹 이름: 표본 수}"""
    _, rows = pair_rows(raw, years)

    def rs(pr):
        return {m: max(pearson([a[m] for a, _ in pr], [b[m] for _, b in pr]) or 0.0, 0.0)
                for m in PLAYER_METRICS}
    allr = rs(rows) if len(rows) >= K_MIN_SAMPLE else {m: 1.0 for m in PLAYER_METRICS}
    W, N = {}, {}
    for lab in labels:
        bases = lab.split("+")
        pr = [(a, b) for a, b in rows if a["pos"] in bases]
        N[lab] = len(pr)
        W[lab] = rs(pr) if len(pr) >= K_MIN_SAMPLE else dict(allr)
        if log and len(pr) < K_MIN_SAMPLE:
            log(f"  유사도 가중치 {lab}: 표본 {len(pr)}명 < {K_MIN_SAMPLE} → 전체 표본 r 사용")
    return W, N


def similar_players(pool, weights=None, n=SIM_N, suffix="_adj"):
    """같은 포지션 그룹(peer, 합쳤으면 합친 그룹) 안에서 축소된 13개 지표를 그룹 안 z-점수로
    바꾸고, 지표별 가중치 w(그룹 안 시즌간 상관계수, sim_weights)로 가중한 코사인 유사도:
        Σ w·a·b / (√Σ w·a² · √Σ w·b²)
    시즌마다 크게 바뀌는(=선수보다 운·팀 사정이 크게 작용하는) 지표일수록 덜 반영된다.
    weights가 없으면 가중치 1. 같은 팀(같은 리그·같은 팀 ID) 선수와 자기 자신은 뺀다.
    반환: {(league, id): [(상대 선수 행, 유사도), ...]} 유사도 높은 순 n명."""
    out = {}
    for lab in peer_labels(pool):
        grp = [p for p in pool if p["peer"] == lab]
        w = [weights[lab][m] if weights else 1.0 for m in PLAYER_METRICS]
        stats = {}
        for m in PLAYER_METRICS:
            vals = [p[m + suffix] for p in grp]
            mu = sum(vals) / len(vals)
            stats[m] = (mu, (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0)
        vec = {id(p): [(p[m + suffix] - stats[m][0]) / stats[m][1] for m in PLAYER_METRICS] for p in grp}
        norm = {k: sum(wi * x * x for wi, x in zip(w, v)) ** 0.5 or 1.0 for k, v in vec.items()}
        for p in grp:
            a = vec[id(p)]
            cands = []
            for q in grp:
                if q is p or q["id"] == p["id"]:
                    continue
                if q["team_id"] == p["team_id"] and q["league"] == p["league"]:
                    continue
                b = vec[id(q)]
                dot = sum(wi * x * y for wi, x, y in zip(w, a, b))
                cands.append((q, dot / (norm[id(p)] * norm[id(q)])))
            cands.sort(key=lambda t: -t[1])
            out[(p["league"], p["id"])] = cands[:n]
    return out


def build_pool(raw, yr, K=None, poss_adj=False):
    """그 시즌의 후보 풀을 만들고 그룹 병합·축소·백분위·역할 점수까지 매긴다.
    K를 주지 않으면 축소 없이(원값 기준) 점수를 매긴다.
    poss_adj=True면 POSS_ADJ 지표를 점유율 보정한 값으로 바꿔 계산한다 — 기본 모델에는
    쓰지 않는다 (2026-11-13 이적 선수 r 비교에서 개선 근거가 없었다). 백테스트 비교용."""
    pool = pool_rows(raw, yr)
    if poss_adj:
        for p in pool:
            for m in POSS_ADJ:
                p[m] = poss_adjust(p, m)
    merge_groups(pool)
    if K is None:
        return score_pool(pool, suffix="")
    apply_shrinkage(pool, K, prior_means(pool))
    return score_pool(pool, suffix="_adj")


# ------------------------------------------------------------------ 팀 스타일 (ROADMAP E)
# 군집(k-means, 실루엣 0.17)과 단순 비율(리그 순위와 |rho| 0.5~0.64)은 둘 다 팀 수준을 재고 있었다(2026-11-14).
# 그래서 각 스타일 값을 리그별로 경기당 xG 차이에 회귀한 잔차 = "수준 대비" 값으로 쓴다.
STYLE = [("poss_res",  "poss",        "수준 대비 점유"),          # 점유율 %
         ("long_res",  "long_share",  "수준 대비 롱볼 비중"),      # 성공 롱볼 / 성공 패스
         ("cross_res", "cross_share", "수준 대비 크로스 비중"),    # 성공 크로스 / 상대 박스 터치
         ("press_res", "press_share", "수준 대비 압박 높이")]      # 파이널 서드 탈취 / (태클+인터셉트)


def team_styles(teams):
    """teams: {league: [team dict]} — 비율과 리그별 xGD90 회귀 잔차를 각 팀 dict에 넣는다."""
    for lg, tt in teams.items():
        for t in tt:
            t["long_share"] = t["long_pg"] / t["pass_pg"]
            t["cross_share"] = t["cross_pg"] / t["box90"]
            t["press_share"] = t["press90"] / t["defact90"]
        xs = [t["xGD90"] for t in tt]
        mx = sum(xs) / len(xs)
        sxx = sum((x - mx) ** 2 for x in xs) or 1.0
        for key, raw, _ in STYLE:
            ys = [t[raw] for t in tt]
            my = sum(ys) / len(ys)
            b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
            for t in tt:
                t[key] = t[raw] - (my + b * (t["xGD90"] - mx))
    return teams


def _inv(A):
    """가우스-조르단 역행렬 (작은 정방행렬)."""
    n = len(A)
    M = [list(r) + [float(i == j) for j in range(n)] for i, r in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        d = M[c][c]
        M[c] = [v / d for v in M[c]]
        for r in range(n):
            if r != c:
                f_ = M[r][c]
                M[r] = [a - f_ * b for a, b in zip(M[r], M[c])]
    return [r[n:] for r in M]


def style_metric(team_list):
    """마할라노비스 거리 함수. 공분산은 주어진 팀들에서 추정(모집단 공분산)."""
    V = [[t[k] for k, _, _ in STYLE] for t in team_list]
    n, d = len(V), len(STYLE)
    mu = [sum(v[j] for v in V) / n for j in range(d)]
    C = [[sum((v[i] - mu[i]) * (v[j] - mu[j]) for v in V) / n for j in range(d)] for i in range(d)]
    Ci = _inv(C)

    def dist(a, b):
        x = [a[k] - b[k] for k, _, _ in STYLE]
        return max(sum(x[i] * Ci[i][j] * x[j] for i in range(d) for j in range(d)), 0.0) ** 0.5
    return dist


def style_euclid(team_list):
    """비교용: 4개 잔차를 z-점수로 바꾼 유클리드 거리."""
    sd = {}
    for k, _, _ in STYLE:
        v = [t[k] for t in team_list]
        mu = sum(v) / len(v)
        sd[k] = (sum((x - mu) ** 2 for x in v) / len(v)) ** 0.5 or 1.0
    return lambda a, b: sum(((a[k] - b[k]) / sd[k]) ** 2 for k, _, _ in STYLE) ** 0.5


def style_neighbors(team_list, dist, n=3):
    """{(league, id): [(팀, 거리), ...]} 두 리그 전체에서 가까운 n팀."""
    out = {}
    for a in team_list:
        c = sorted(((b, dist(a, b)) for b in team_list if b is not a), key=lambda x: x[1])
        out[(a["league"], a["id"])] = c[:n]
    return out


# ------------------------------------------------------------------ 현 라인과의 유형 비교
def z_stats(pool, suffix="_adj"):
    """그룹(peer)별 지표 평균·표준편차 — similar_players와 같은 z 기준. {peer: {m: (mu, sd)}}"""
    out = {}
    for lab in peer_labels(pool):
        grp = [p for p in pool if p["peer"] == lab]
        out[lab] = {}
        for m in PLAYER_METRICS:
            v = [p[m + suffix] for p in grp]
            mu = sum(v) / len(v)
            out[lab][m] = (mu, (sum((x - mu) ** 2 for x in v) / len(v)) ** 0.5 or 1.0)
    return out


def wcos(a, b, w):
    """가중 코사인: Σ w·a·b / (√Σ w·a² · √Σ w·b²)"""
    num = sum(wi * x * y for wi, x, y in zip(w, a, b))
    na = sum(wi * x * x for wi, x in zip(w, a)) ** 0.5
    nb = sum(wi * y * y for wi, y in zip(w, b)) ** 0.5
    return num / (na * nb) if na and nb else None


# ------------------------------------------------------------------ 리그 강도 (ROADMAP C)
# 리그 간 이적자의 변화 − 대조군(출발 리그에 남은 같은 포지션·같은 시즌쌍 선수) 평균 변화 = 차이의 차이.
# 라리가→PL은 부호를 뒤집어 "라리가 대비 PL 난이도" 계수 하나로 묶는다 (양수 = PL이 더 어렵다).
# 보정은 자동으로 켜지 않는다 — 추정치와 신뢰구간만 기록한다 (켤지는 사람이 정한다, CLAUDE.md).
LEAGUE_MIN_N = 15         # 이적자가 이보다 적으면 추정 자체를 끈다
LEAGUE_BOOT = 2000
LEAGUE_SEED = 20261114    # 데이터가 같으면 숫자도 같게


def league_effect(raw, years, K):
    import random
    done = [y for y in years if completed(raw, y)]
    pairs = [(y, y + 1) for y in done if y + 1 in done]
    pools = {y: {p["id"]: p for p in build_pool(raw, y, K)} for y in {y for pr in pairs for y in pr}}

    def role_avg(p, pos):
        rs = [r for r, R in ROLES.items() if pos in R["groups"]]
        return sum(p["role_z"][r] for r in rs) / len(rs)

    movers, ctrl = [], []
    for y1, y2 in pairs:
        for pid, a in pools[y1].items():
            b = pools[y2].get(pid)
            if not b:
                continue
            row = dict(fr=a["league"], key=(a["league"], a["pos"], y1),
                       d={"npxG90": b["npxG90_adj"] - a["npxG90_adj"], "xA90": b["xA90_adj"] - a["xA90_adj"],
                          "role_z": role_avg(b, a["pos"]) - role_avg(a, a["pos"])})
            (movers if a["league"] != b["league"] else ctrl).append(row)
    first = list(LEAGUES)[0]                       # EPL
    out = dict(n=len(movers), n_pl_ll=sum(1 for r in movers if r["fr"] == first),
               n_ll_pl=sum(1 for r in movers if r["fr"] != first), n_ctrl=len(ctrl),
               pairs=[f"{season_label(a)}→{season_label(b)}" for a, b in pairs],
               min_n=LEAGUE_MIN_N, boot=LEAGUE_BOOT, metrics={}, applied=False)
    if len(movers) < LEAGUE_MIN_N:
        out["off"] = True
        return out

    def est(mv, ct, m):
        cm = {}
        for r in ct:
            cm.setdefault(r["key"], []).append(r["d"][m])
        cm = {k: sum(v) / len(v) for k, v in cm.items()}
        x = [(r["d"][m] - cm[r["key"]]) * (1 if r["fr"] == first else -1) for r in mv if r["key"] in cm]
        return sum(x) / len(x) if x else 0.0

    rnd = random.Random(LEAGUE_SEED)
    for m in ("npxG90", "xA90", "role_z"):
        bs = sorted(est([rnd.choice(movers) for _ in movers], [rnd.choice(ctrl) for _ in ctrl], m)
                    for _ in range(LEAGUE_BOOT))
        lo, hi = bs[int(0.025 * LEAGUE_BOOT)], bs[int(0.975 * LEAGUE_BOOT) - 1]
        out["metrics"][m] = dict(est=round(est(movers, ctrl, m), 4), lo=round(lo, 4), hi=round(hi, 4),
                                 excludes_zero=not (lo <= 0 <= hi))
    return out
