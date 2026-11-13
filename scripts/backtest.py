"""백테스트 — 역할 점수가 다음 시즌을 실제로 예측하는가 (FotMob).

완주한 연속 시즌쌍마다 N시즌 역할 점수로 N+1시즌 값을 맞혀 보고, 비교군을 반드시
같이 낸다. 우리 모델이 비교군보다 못하면 그대로 적는다 (FOTMOB_MIGRATION.md 3절).

  예측 대상
    결정력      다음 시즌 npxG/90
    찬스 메이킹 다음 시즌 xA/90
    빌드업·볼 탈취·박스 수비  다음 시즌 '같은 지표' = 역할 지표의 z-점수 가중합(축소 없음)
    공격 종합(참고) 다음 시즌 (npxG+xA)/90 — Understat 시절 비교군을 잇는다
  비교군
    백분위/z-점수 × 축소 적용/미적용, 지난 시즌 원지표 그대로,
    점유율 보정판(볼 탈취·박스 수비) — 기본 모델에는 없다
  표본: 두 시즌 모두 후보 풀(600분+, 팀 경기 절반+)에 든 선수, 역할 대상 그룹 안에서.
  K는 배포 모델과 같은 값(완주 시즌쌍 전체로 추정)을 쓴다 — 시험 쌍의 데이터가 K 추정에
  들어가는 약한 누수가 있다. K는 지표당 숫자 하나라 영향은 작지만 숨기지 않는다.

결과 → docs/backtest.json.  표준 라이브러리만 사용한다.
"""
import json, os, sys

from model import (ROLES, POSS_ADJ, POOL_MIN, season_label, pearson, spearman,
                   completed, estimate_k, build_pool)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW = os.path.join(ROOT, "data", "fotmob_raw.json")
DOCS = os.path.join(ROOT, "docs")

DEPLOYED = "pct_adj"
LABEL = {
    "pct_adj":      "백분위 가중합 · 축소 적용 (현재 모델)",
    "pct_raw":      "백분위 가중합 · 축소 미적용",
    "z_adj":        "z-점수 가중합 · 축소 적용",
    "z_raw":        "z-점수 가중합 · 축소 미적용",
    "prev":         "지난 시즌 원지표 그대로",
    "pct_adj_poss": "백분위 · 축소 · 점유율 보정",
    "z_adj_poss":   "z-점수 · 축소 · 점유율 보정",
}
MODEL_KEYS = ["pct_adj", "pct_raw", "z_adj", "z_raw"]
# 역할별 예측 대상과 '원지표 그대로' 비교군. 세 번째 값이 None이면 대상이 역할 z-합성이고,
# 그 경우 '원지표 그대로'는 'z 축소 미적용'과 같은 값이 된다.
TARGET = {
    "finisher":   ("다음 시즌 npxG/90", "지난 시즌 npxG/90", lambda p: p["npxG90"]),
    "creator":    ("다음 시즌 xA/90", "지난 시즌 xA/90", lambda p: p["xA90"]),
    "progressor": ("다음 시즌 같은 지표 (z-합성)", "지난 시즌 같은 지표", None),
    "ballwinner": ("다음 시즌 같은 지표 (z-합성)", "지난 시즌 같은 지표", None),
    "boxdef":     ("다음 시즌 같은 지표 (z-합성)", "지난 시즌 같은 지표", None),
}
POSS_ROLES = ["ballwinner", "boxdef"]
ATTACK_GROUPS = ["ST", "AM"]


def corr(xs, ys):
    if len(xs) < 30:
        return dict(r=None, rho=None, n=len(xs))
    r, rho = pearson(xs, ys), spearman(xs, ys)
    return dict(r=None if r is None else round(r, 3), rho=None if rho is None else round(rho, 3),
                n=len(xs))


def by_id(pool):
    out = {}
    for p in pool:
        out.setdefault(p["id"], p)
    return out


def run_pair(raw, y1, y2, K):
    a = by_id(build_pool(raw, y1, K=K))                      # 배포 모델 (축소 적용)
    b = by_id(build_pool(raw, y1, K=None))                   # 축소 미적용
    ap = by_id(build_pool(raw, y1, K=K, poss_adj=True))      # 점유율 보정판
    nxt = by_id(build_pool(raw, y2, K=None))                 # 대상: 다음 시즌 원값
    nxt_poss = by_id(build_pool(raw, y2, K=None, poss_adj=True))
    ids = [i for i in a if i in nxt]

    def preds(rk, ids_):
        out = {
            "pct_adj": [a[i]["role"][rk] for i in ids_],
            "pct_raw": [b[i]["role"][rk] for i in ids_],
            "z_adj":   [a[i]["role_z"][rk] for i in ids_],
            "z_raw":   [b[i]["role_z"][rk] for i in ids_],
        }
        fn = TARGET[rk][2]
        out["prev"] = [fn(b[i]) for i in ids_] if fn else out["z_raw"]
        if rk in POSS_ROLES:
            out["pct_adj_poss"] = [ap[i]["role"][rk] for i in ids_]
            out["z_adj_poss"] = [ap[i]["role_z"][rk] for i in ids_]
        return out

    def target(rk, ids_, poss=False):
        fn = TARGET[rk][2]
        src = nxt_poss if poss else nxt
        return [fn(src[i]) for i in ids_] if fn else [src[i]["role_z"][rk] for i in ids_]

    roles = {}
    for rk, r in ROLES.items():
        sel = [i for i in ids if a[i]["pos"] in r["groups"]]
        mov = [i for i in sel if a[i]["team_id"] != nxt[i]["team_id"] or a[i]["league"] != nxt[i]["league"]]
        res = dict(n=len(sel), n_movers=len(mov), methods={})
        y_all, y_mov = target(rk, sel), target(rk, mov)
        px_all, px_mov = preds(rk, sel), preds(rk, mov)
        for k in px_all:
            res["methods"][k] = dict(all=corr(px_all[k], y_all), movers=corr(px_mov[k], y_mov))
        if rk in POSS_ROLES:            # 대상도 점유율 보정했을 때
            yp_all, yp_mov = target(rk, sel, True), target(rk, mov, True)
            res["methods_poss_target"] = {
                k: dict(all=corr(px_all[k], yp_all), movers=corr(px_mov[k], yp_mov)) for k in px_all}
        res["scatter"] = [dict(n=a[i]["name"], t=nxt[i]["team"], p=a[i]["pos"],
                               x=round(a[i]["role"][rk], 1), y=round(v, 3))
                          for i, v in zip(sel, y_all)]
        roles[rk] = res

    # 공격 종합(참고): 다음 시즌 (npxG+xA)/90, ST·AM
    sel = [i for i in ids if a[i]["pos"] in ATTACK_GROUPS]
    y = [nxt[i]["npxG90"] + nxt[i]["xA90"] for i in sel]
    attack = dict(n=len(sel), methods={
        "finisher_pct_adj": corr([a[i]["role"]["finisher"] for i in sel], y),
        "creator_pct_adj": corr([a[i]["role"]["creator"] for i in sel], y),
        "finisher_z_adj": corr([a[i]["role_z"]["finisher"] for i in sel], y),
        "creator_z_adj": corr([a[i]["role_z"]["creator"] for i in sel], y),
        "prev": corr([b[i]["npxG90"] + b[i]["xA90"] for i in sel], y),
    })
    return dict(pair=f"{season_label(y1)}→{season_label(y2)}", n=len(ids), roles=roles, attack=attack)


def rs(results, rk, key, sub="all", block="methods"):
    return [p["roles"][rk][block][key][sub]["r"] for p in results]


def beats(x, y):
    return all(a is not None and b is not None and a > b for a, b in zip(x, y))


def verdict(results):
    """두 쌍 모두에서 이겨야 이긴 것으로 친다. 좋게 포장하지 않는다."""
    out = {}
    for rk in ROLES:
        d = {k: rs(results, rk, k) for k in results[0]["roles"][rk]["methods"]}
        best = max(MODEL_KEYS, key=lambda k: sum(x or 0 for x in d[k]))
        same = TARGET[rk][2] is None
        lines = []
        dep = beats(d[DEPLOYED], d["prev"])
        lines.append(("현재 모델이 지난 시즌 원지표 그대로를 이겼다" if dep else
                      "현재 모델이 지난 시즌 원지표 그대로를 못 이겼다")
                     + f" (모델 r={d[DEPLOYED]}, 원지표 r={d['prev']})")
        if same:
            # 대상이 역할 지표 자체라 '원지표 그대로' = 'z 축소 미적용'. 이 둘의 차이는 축소 효과뿐이다.
            gain = [round(x - y, 3) for x, y in zip(d["z_adj"], d["prev"])]
            lines.append("이 역할은 예측 대상이 역할 지표 자체라 '원지표 그대로'가 z 축소 미적용과 같다 — "
                         f"z 축소 적용과의 차이 {gain}는 축소 효과일 뿐, 모델이 원지표를 이긴 것이 아니다")
        elif best != DEPLOYED:
            bw = beats(d[best], d["prev"])
            lines.append(f"가장 나은 변형({LABEL[best]})은 " + ("이겼다" if bw else "못 이겼다")
                         + f" (r={d[best]})")
        z_win, pct_win = beats(d["z_adj"], d["pct_adj"]), beats(d["pct_adj"], d["z_adj"])
        lines.append(("z-점수가 백분위보다 두 쌍 모두 낫다" if z_win else
                      "백분위가 z-점수보다 두 쌍 모두 낫다" if pct_win else
                      "z-점수와 백분위가 쌍마다 엇갈린다")
                     + f" (z r={d['z_adj']}, 백분위 r={d['pct_adj']})")
        sh_p, sh_z = beats(d["pct_adj"], d["pct_raw"]), beats(d["z_adj"], d["z_raw"])
        lines.append(f"축소: 백분위 {'도움' if sh_p else '도움 안 됨'} · z {'도움' if sh_z else '도움 안 됨'}")
        v = dict(deployed_beats_prev=dep, z_beats_pct=z_win, pct_beats_z=pct_win,
                 shrink_helps_pct=sh_p, shrink_helps_z=sh_z, best_variant=best)
        if rk in POSS_ROLES:
            pz = beats(d["z_adj_poss"], d["z_adj"])
            mv_p, mv_z = rs(results, rk, "z_adj_poss", "movers"), rs(results, rk, "z_adj", "movers")
            pzm = None if None in mv_p + mv_z else beats(mv_p, mv_z)
            pt = beats(rs(results, rk, "z_adj_poss", block="methods_poss_target"),
                       rs(results, rk, "z_adj", block="methods_poss_target"))
            mv_txt = "표본 30명 미만이라 판정 못 함" if pzm is None else ("개선" if pzm else "개선 없음")
            lines.append(f"점유율 보정(z 기준): 원 대상 {'개선' if pz else '개선 없음'} · "
                         f"이적 선수 {mv_txt} · 보정한 대상 {'개선' if pt else '개선 없음'}")
            v.update(poss_helps=pz, poss_helps_movers=pzm, poss_helps_poss_target=pt)
        v["text"] = " · ".join(lines)
        out[rk] = v
    att = [p["attack"]["methods"] for p in results]
    best = max((k for k in att[0] if k != "prev"), key=lambda k: sum(m[k]["r"] for m in att))
    win = all(m[best]["r"] > m["prev"]["r"] for m in att)
    out["attack"] = dict(best=best, beats_prev=win, text=(
        f"공격 종합(참고): 가장 나은 역할 점수({best})가 지난 시즌 (npxG+xA)/90 그대로를 "
        + ("이겼다" if win else "못 이겼다")
        + f" (역할 r={[m[best]['r'] for m in att]}, 원지표 r={[m['prev']['r'] for m in att]})"))
    return out


def fmt(c):
    return "   —  " if c["r"] is None else f"{c['r']:6.3f}"


def main():
    raw = json.load(open(RAW, encoding="utf-8"))
    years = sorted({int(k.rsplit("_", 1)[1]) for k in raw})
    full = [y for y in years if completed(raw, y)]
    pairs = [(y, y + 1) for y in full if y + 1 in full]
    if not pairs:
        sys.exit("연속된 완주 시즌쌍이 없습니다.")
    K, k_info = estimate_k(raw, years)
    print(f"백테스트 시즌쌍: {[f'{season_label(a)}→{season_label(b)}' for a, b in pairs]}")

    results = [run_pair(raw, y1, y2, K) for y1, y2 in pairs]
    head = "".join(f"{p['pair']:>22s}" for p in results)
    sub = "".join(f"{'전체':>11s}{'이적':>9s}" for _ in results)
    for rk, r in ROLES.items():
        print(f"\n■ {r['label']}  [{', '.join(r['groups'])}]  대상: {TARGET[rk][0]}")
        print(f"  {'':32s}{head}\n  {'':32s}{sub}")
        for k in results[0]["roles"][rk]["methods"]:
            row = "".join(f"{fmt(p['roles'][rk]['methods'][k]['all']):>12s}"
                          f"{fmt(p['roles'][rk]['methods'][k]['movers']):>10s}" for p in results)
            print(f"  {LABEL[k]:32s}{row}")
        print("  n = " + ", ".join(f"{p['roles'][rk]['n']} (이적 {p['roles'][rk]['n_movers']})" for p in results))
        if rk in POSS_ROLES:
            print("  · 대상도 점유율 보정했을 때")
            for k in ("z_adj", "z_adj_poss", "pct_adj", "pct_adj_poss"):
                row = "".join(f"{fmt(p['roles'][rk]['methods_poss_target'][k]['all']):>12s}"
                              f"{fmt(p['roles'][rk]['methods_poss_target'][k]['movers']):>10s}" for p in results)
                print(f"  {LABEL[k]:32s}{row}")

    print("\n■ 공격 종합(참고) — 다음 시즌 (npxG+xA)/90, ST·AM")
    for k in results[0]["attack"]["methods"]:
        print(f"  {k:32s}" + "".join(f"{fmt(p['attack']['methods'][k]):>12s}" for p in results))
    print("  n = " + ", ".join(str(p["attack"]["n"]) for p in results))

    v = verdict(results)
    print("\n판정")
    for rk in ROLES:
        print(f"  {ROLES[rk]['label']}: {v[rk]['text']}")
    print(f"  {v['attack']['text']}")

    out = dict(
        meta=dict(source="FotMob", pool_min=POOL_MIN, pool_rule="600분 이상 + 팀 경기 절반 이상 출전",
                  pairs=[p["pair"] for p in results], deployed=DEPLOYED, labels=LABEL,
                  targets={k: dict(target=t[0], baseline=t[1]) for k, t in TARGET.items()},
                  roles={k: dict(label=r["label"], groups=r["groups"]) for k, r in ROLES.items()},
                  k={m: k_info[m]["K"] for m in k_info},
                  note="K는 배포 모델과 같은 값(완주 시즌쌍 전체로 추정) — 시험 쌍 데이터가 "
                       "K 추정에 들어가는 약한 누수가 있다."),
        pairs=results, verdict=v,
    )
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "backtest.json"), "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, separators=(",", ":"))
    print("\ndocs/backtest.json 생성 완료")


if __name__ == "__main__":
    main()
