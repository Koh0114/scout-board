"""build.py와 backtest.py가 함께 쓰는 모델 정의.

백테스트는 '실제로 배포되는 모델'을 검증해야 의미가 있다. 점수 계산이
두 스크립트에 따로 있으면 조용히 갈라지므로 여기 한 곳에만 둔다.
표준 라이브러리만 사용한다.
"""

LEAGUES = {"EPL": "Premier League", "La_liga": "La Liga"}
POOL_MIN = 600            # 후보 풀 최소 출전 시간(분)

PLAYER_METRICS = ["npxG90", "xA90", "sh90", "kp90", "xGChain90", "xGBuildup90"]

# 표본 크기 보정(shrinkage). K = 실제값과 사전평균을 50:50으로 섞는 분 수.
# 연속 두 시즌 데이터로 직접 추정하고, 표본이 모자라면 아래 기본값으로 물러선다.
DEFAULT_K = {"sh90": 400, "kp90": 600, "xGChain90": 700,
             "xGBuildup90": 700, "npxG90": 900, "xA90": 1400}
K_MIN_SAMPLE = 50         # 이보다 표본이 적으면 기본값 사용
K_MIN_MINS = 600          # 두 시즌 모두 이만큼 뛴 선수만 상관계수 표본에 넣는다

ROLES = {
    "finisher":   {"label": "골 결정력 (Finisher)",  "w": {"npxG90": .55, "sh90": .25, "xGChain90": .20}},
    "creator":    {"label": "찬스 메이킹 (Creator)", "w": {"xA90": .50, "kp90": .30, "xGChain90": .20}},
    "progressor": {"label": "빌드업 (Progressor)",   "w": {"xGBuildup90": .60, "xGChain90": .40}},
    "allround":   {"label": "공격 종합 (All-round)", "w": {"npxG90": .30, "xA90": .30, "xGChain90": .25, "sh90": .15}},
}
POSNAME = {"F": "FW", "M": "MF", "D": "DF", "GK": "GK", "S": "SUB"}
POS_GROUPS = ("FW", "MF", "DF")


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def season_label(y):
    return f"{y}/{str(y + 1)[2:]}"


def primary_pos(code):
    toks = [c for c in code.split() if c != "S"] or ["S"]
    order = ["F", "M", "D", "GK", "S"]
    toks.sort(key=lambda x: order.index(x) if x in order else 9)
    return toks[0]


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


def player_rows(raw, yr):
    """그 시즌 선수들의 per-90 지표. understat 선수 id로 묶는다."""
    rows = []
    for lg in LEAGUES:
        for p in (raw.get(f"{lg}_{yr}", {}).get("players") or []):
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


def estimate_k(raw, years, log=None):
    """연속 두 시즌 모두 K_MIN_MINS 이상 뛴 선수로 시즌간 상관계수를 구해
    K ≈ 평균출전분 × (1−r)/r 로 추정한다. 표본이 모자라면 기본값."""
    cache = {y: {p["id"]: p for p in player_rows(raw, y)} for y in years}
    pairs = [(y, y + 1) for y in years if y + 1 in cache]
    rows = []
    for y1, y2 in pairs:
        s2 = cache[y2]
        for pid, a in cache[y1].items():
            b = s2.get(pid)
            if b and a["mins"] >= K_MIN_MINS and b["mins"] >= K_MIN_MINS:
                rows.append((a, b))
    if log:
        log(f"K 추정 — 연속 시즌쌍 {[f'{season_label(a)}→{season_label(b)}' for a, b in pairs]}, "
            f"양 시즌 {K_MIN_MINS}분+ 선수 {len(rows)}건")

    K, info = {}, {}
    for m in PLAYER_METRICS:
        n = len(rows)
        r = pearson([a[m] for a, _ in rows], [b[m] for _, b in rows]) if n >= K_MIN_SAMPLE else None
        if n < K_MIN_SAMPLE:
            K[m] = DEFAULT_K[m]
            info[m] = dict(K=K[m], source="default", n=n, r=None)
            if log:
                log(f"  {m:12s} 표본 {n}명 < {K_MIN_SAMPLE} → 기본값 K={DEFAULT_K[m]} 사용")
        elif r is None or r <= 0:
            K[m] = DEFAULT_K[m]
            info[m] = dict(K=K[m], source="default", n=n, r=(None if r is None else round(r, 3)))
            if log:
                log(f"  {m:12s} 표본 {n}명, r={'계산 불가' if r is None else f'{r:.3f}'} "
                    f"(양수 아님) → 기본값 K={DEFAULT_K[m]} 사용")
        else:
            mean_mins = sum(a["mins"] for a, _ in rows) / n
            K[m] = round(mean_mins * (1 - r) / r)
            info[m] = dict(K=K[m], source="estimated", n=n, r=round(r, 3),
                           mean_mins=round(mean_mins))
            if log:
                log(f"  {m:12s} 표본 {n}명, r={r:.3f}, 평균 {mean_mins:.0f}분 "
                    f"→ 추정 K={K[m]} (기본값 {DEFAULT_K[m]})")
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
    pool = [p for p in player_rows(raw, yr)
            if p["mins"] >= POOL_MIN and p["pos"] in POS_GROUPS]
    if K is None:
        return score_pool(pool, suffix="")
    apply_shrinkage(pool, K, prior_means(pool))
    return score_pool(pool, suffix="_adj")
