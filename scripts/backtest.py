"""백테스트 — 역할 점수가 실제로 다음 시즌 생산을 예측하는가.

N시즌 역할 점수로 N+1시즌 실제 (npxG+xA)/90 을 얼마나 맞히는지 재고,
비교군을 반드시 같이 낸다. 우리 모델이 비교군보다 못하면 그대로 적는다.
결과 → docs/backtest.json.  표준 라이브러리만 사용한다.
"""
import json, os, sys

from model import (POOL_MIN, ROLES, season_label, pearson, spearman,
                   player_rows, estimate_k, build_pool)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW = os.path.join(ROOT, "data", "understat_raw.json")
DOCS = os.path.join(ROOT, "docs")

# 예측 대상: 다음 시즌의 실제 공격 생산. 역할 중 'allround'가 이 정의에 대응하므로
# 대표 예측자로 미리 정해 둔다 (결과를 보고 제일 좋은 역할을 고르지 않는다).
TARGET_ROLE = "allround"
LABEL = {
    "model_adj":  "역할 점수 — 축소 적용 (현재 모델)",
    "model_raw":  "역할 점수 — 축소 미적용",
    "goals":      "지난 시즌 골 수만",
    "prev_out":   "지난 시즌 (npxG+xA)/90 그대로",
}


def target(p):
    """실제 생산 = (npxG + xA) / 90분."""
    return (p["npxG"] + p["xA"]) / (p["mins"] / 90)


def corr_block(xs, ys):
    r, rho = pearson(xs, ys), spearman(xs, ys)
    return dict(r=None if r is None else round(r, 3),
                rho=None if rho is None else round(rho, 3),
                r2=None if r is None else round(r * r, 3))


def run_pair(raw, y1, y2, K):
    """y1 시즌으로 예측하고 y2 시즌 실제값과 맞춰 본다."""
    # 예측자는 y1 데이터만 본다 (누수 없음). 축소 적용/미적용 두 벌을 만든다.
    adj = {p["id"]: p for p in build_pool(raw, y1, K=K)}
    plain = {p["id"]: p for p in build_pool(raw, y1, K=None)}
    nxt = {p["id"]: p for p in player_rows(raw, y2) if p["mins"] >= POOL_MIN}

    ids = [i for i in adj if i in nxt]
    ids.sort(key=lambda i: adj[i]["name"])
    if len(ids) < 30:
        return dict(pair=f"{season_label(y1)}→{season_label(y2)}", n=len(ids),
                    skipped="표본이 30명 미만이라 상관계수를 내지 않습니다")

    y = [target(nxt[i]) for i in ids]
    preds = {
        "model_adj": [adj[i]["role"][TARGET_ROLE] for i in ids],
        "model_raw": [plain[i]["role"][TARGET_ROLE] for i in ids],
        "goals":     [float(adj[i]["npg"]) for i in ids],
        "prev_out":  [target(adj[i]) for i in ids],
    }
    out = dict(
        pair=f"{season_label(y1)}→{season_label(y2)}", from_season=season_label(y1),
        to_season=season_label(y2), n=len(ids),
        methods={k: corr_block(v, y) for k, v in preds.items()},
        # 역할별로도 남겨 둔다 — 대표 예측자만 보고 판단하지 않도록.
        roles={rk: corr_block([adj[i]["role"][rk] for i in ids], y) for rk in ROLES},
    )
    out["scatter"] = [dict(n=adj[i]["name"], t=nxt[i]["team"], p=adj[i]["pos"],
                           x=round(adj[i]["role"][TARGET_ROLE], 1), y=round(target(nxt[i]), 3))
                      for i in ids]
    return out


def verdict(pairs):
    """모델이 비교군을 이겼는지 — 좋게 포장하지 않는다."""
    done = [p for p in pairs if not p.get("skipped")]
    if not done:
        return dict(ok=False, text="표본이 모자라 판정하지 못했습니다.")

    def rs(key):
        return [p["methods"][key]["r"] for p in done if p["methods"][key]["r"] is not None]

    adj, raw, goals, prev = rs("model_adj"), rs("model_raw"), rs("goals"), rs("prev_out")
    shrink_win = all(a > b for a, b in zip(adj, raw))
    goals_win = all(a > g for a, g in zip(adj, goals))
    prev_win = all(a > p for a, p in zip(adj, prev))
    lines = [
        ("축소가 예측력을 올렸다" if shrink_win else
         "축소가 예측력을 올리지 못했다 — A단계 K를 다시 잡아야 한다")
        + f" (축소 후 r={adj}, 축소 전 r={raw})",
        ("단순 골 수보다 낫다" if goals_win else
         "단순 골 수보다 낫지 않다 — 모델의 근거가 약하다")
        + f" (모델 r={adj}, 골 수 r={goals})",
        ("지난 시즌 (npxG+xA)/90을 그대로 쓴 것보다 낫다" if prev_win else
         "지난 시즌 (npxG+xA)/90을 그대로 쓴 것보다 못하다 — 역할 점수가 이 대상에 대해 "
         "더 나은 예측자라고 말할 수 없다")
        + f" (모델 r={adj}, 지난 생산 r={prev})",
    ]
    return dict(ok=shrink_win and goals_win and prev_win, shrink_beats_raw=shrink_win,
                model_beats_goals=goals_win, model_beats_prev_output=prev_win,
                text=" · ".join(lines))


def main():
    raw = json.load(open(RAW, encoding="utf-8"))
    years = sorted({int(k.rsplit("_", 1)[1]) for k in raw})
    K, k_info = estimate_k(raw, years)

    # 마지막 시즌은 진행 중일 수 있다. 38경기 기준으로 다 치른 시즌만 대상.
    def played(yr):
        return max((len(t.get("history", []))
                    for lg in ("EPL", "La_liga")
                    for t in (raw.get(f"{lg}_{yr}", {}).get("teams") or {}).values()), default=0)

    full = [y for y in years if played(y) >= 30]
    pairs = [(y, y + 1) for y in full if y + 1 in full]
    if not pairs:
        sys.exit("연속된 완주 시즌쌍이 없습니다.")
    print(f"백테스트 시즌쌍: {[f'{season_label(a)}→{season_label(b)}' for a, b in pairs]}")

    results = [run_pair(raw, y1, y2, K) for y1, y2 in pairs]
    for r in results:
        if r.get("skipped"):
            print(f"  {r['pair']}: {r['skipped']} (n={r['n']})")
            continue
        m = r["methods"]
        print(f"  {r['pair']}  n={r['n']}")
        for key in ("model_adj", "model_raw", "goals", "prev_out"):
            print(f"    {LABEL[key]:28s} r={m[key]['r']:.3f}  rho={m[key]['rho']:.3f}  R²={m[key]['r2']:.3f}")

    v = verdict(results)
    print("판정:", v["text"])

    out = dict(
        meta=dict(target="다음 시즌 (npxG+xA)/90", target_role=TARGET_ROLE,
                  role_label=ROLES[TARGET_ROLE]["label"], pool_min=POOL_MIN,
                  labels=LABEL, k={m: k_info[m]["K"] for m in k_info}),
        pairs=results, verdict=v,
    )
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "backtest.json"), "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, separators=(",", ":"))
    print("docs/backtest.json 생성 완료")


if __name__ == "__main__":
    main()
