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
  K는 시험 쌍을 뺀 나머지 완주 시즌쌍으로 추정한다 (23/24→24/25로 추정 → 24/25→25/26 검증,
  그 반대도). 배포용 K는 build.py가 전체 쌍으로 따로 추정한다.
  빌드업·볼 탈취·박스 수비는 대상이 자기 지표라 '원지표를 이기는가'가 성립하지 않는다 —
  '다음 시즌에도 유지되는가(r)'로 판정한다.

결과 → docs/backtest.json.  표준 라이브러리만 사용한다.
"""
import json, os, sys

from model import (ROLES, POSS_ADJ, POOL_MIN, season_label, pearson, spearman,
                   completed, estimate_k, build_pool)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW = os.path.join(ROOT, "data", "fotmob_raw.json")
DOCS = os.path.join(ROOT, "docs")

DEPLOYED = "z_adj"
LABEL = {
    "pct_adj":      "백분위 가중합 · 축소 (이전 모델)",
    "pct_raw":      "백분위 가중합 · 축소 미적용",
    "z_adj":        "z-점수 가중합 · 축소 (현재 모델)",
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
# 원지표와의 r 차이가 두 쌍 모두 이 안쪽이면 '비슷한 수준'이라고 쓴다. 시즌쌍 2개로는
# 이 정도 차이를 우열로 읽을 수 없다. 판정문에 '이겼다'는 쓰지 않는다.
SIMILAR = 0.03
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
            "pct_adj": [a[i]["role_pctw"][rk] for i in ids_],
            "pct_raw": [b[i]["role_pctw"][rk] for i in ids_],
            "z_adj":   [a[i]["role_z"][rk] for i in ids_],
            "z_raw":   [b[i]["role_z"][rk] for i in ids_],
        }
        fn = TARGET[rk][2]
        out["prev"] = [fn(b[i]) for i in ids_] if fn else out["z_raw"]
        if rk in POSS_ROLES:
            out["pct_adj_poss"] = [ap[i]["role_pctw"][rk] for i in ids_]
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
                               x=round(a[i]["role_z"][rk], 3), y=round(v, 3))
                          for i, v in zip(sel, y_all)]
        roles[rk] = res

    # 공격 종합(참고): 다음 시즌 (npxG+xA)/90, ST·AM
    sel = [i for i in ids if a[i]["pos"] in ATTACK_GROUPS]
    y = [nxt[i]["npxG90"] + nxt[i]["xA90"] for i in sel]
    attack = dict(n=len(sel), methods={
        "finisher": corr([a[i]["role_z"]["finisher"] for i in sel], y),
        "creator": corr([a[i]["role_z"]["creator"] for i in sel], y),
        "prev": corr([b[i]["npxG90"] + b[i]["xA90"] for i in sel], y),
    })
    return dict(pair=f"{season_label(y1)}→{season_label(y2)}", n=len(ids), roles=roles, attack=attack)


def rs(results, rk, key, sub="all", block="methods"):
    return [p["roles"][rk][block][key][sub]["r"] for p in results]


def beats(x, y):
    return all(a is not None and b is not None and a > b for a, b in zip(x, y))


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _r2(x):
    return "—" if x is None else f"{x:.2f}"


def retention_word(r):
    """'다음 시즌에도 유지되는가'를 말로. 경계값은 표현을 고르는 용도일 뿐 판정 기준이 아니다."""
    if r is None:
        return "판정 못 함"
    return "잘 유지됨" if r >= 0.75 else "대체로 유지됨" if r >= 0.6 else "일부만 유지됨"


def verdict(results):
    """사람이 읽는 판정문을 만든다. 숫자는 두 시즌쌍 평균 r만 괄호로 쓰고, 쌍별 값은 표(structured
    필드)에 둔다. '이겼다'는 쓰지 않는다 — 시즌쌍 2개로는 작은 차이를 우열로 읽을 수 없다."""
    out = {}
    n_pairs = len(results)
    for rk in ROLES:
        d = {k: rs(results, rk, k) for k in results[0]["roles"][rk]["methods"]}
        rho = [p["roles"][rk]["methods"][DEPLOYED]["all"]["rho"] for p in results]
        retention = TARGET[rk][2] is None
        single = len(ROLES[rk]["w"]) == 1
        m_dep, m_prev = _avg(d[DEPLOYED]), _avg(d["prev"])
        sents = []
        dep = None
        if retention:
            sents.append(f"다음 시즌에도 {retention_word(m_dep)} (평균 r {_r2(m_dep)}).")
        else:
            dep = beats(d[DEPLOYED], d["prev"])
            diff = [x - y for x, y in zip(d[DEPLOYED], d["prev"])]
            base = TARGET[rk][1]
            cmp_ = f"(평균 r {_r2(m_dep)} 대 {_r2(m_prev)})"
            if single:
                sents.append(f"{base}과 같은 지표. 출전 시간 보정 덕분에 조금 더 정확함 {cmp_}.")
            elif all(abs(x) < SIMILAR for x in diff):
                sents.append(f"{base}을 그대로 쓰는 것과 비슷한 수준 {cmp_}.")
            elif all(x < 0 for x in diff):
                sents.append(f"{base}을 그대로 쓰는 쪽이 더 정확함 {cmp_}.")
            else:
                sents.append(f"{base}과 시즌 쌍마다 엇갈림 {cmp_}.")
            mv_m, mv_p = rs(results, rk, DEPLOYED, "movers"), rs(results, rk, "prev", "movers")
            low = [p["pair"] for p, a_, b_ in zip(results, mv_m, mv_p)
                   if a_ is not None and b_ is not None and a_ < b_]
            if low and not single:
                sents.append(f"팀을 옮긴 선수만 보면 {', '.join(low)}에서는 지난 시즌 값이 더 정확했음.")
        z_win, pct_win = beats(d["z_adj"], d["pct_adj"]), beats(d["pct_adj"], d["z_adj"])
        if not single and z_win:
            sents.append(f"이전 모델(백분위 가중합)보다 {n_pairs}개 시즌 쌍 모두에서 정확함.")
        elif not single and pct_win:
            sents.append(f"이전 모델(백분위 가중합)이 {n_pairs}개 시즌 쌍 모두에서 더 정확했음.")
        sh_p, sh_z = beats(d["pct_adj"], d["pct_raw"]), beats(d["z_adj"], d["z_raw"])
        if not single:
            sents.append("출전 시간 보정은 " + ("모든 시즌 쌍에서 도움이 됨." if sh_z else "일관되게 도움이 되지는 않음."))
        v = dict(kind="retention" if retention else "prediction", deployed_r=d[DEPLOYED],
                 deployed_rho=rho, prev_r=d["prev"], deployed_beats_prev=dep, z_beats_pct=z_win,
                 pct_beats_z=pct_win, shrink_helps_pct=sh_p, shrink_helps_z=sh_z)
        if rk in POSS_ROLES:
            pz = beats(d["z_adj_poss"], d["z_adj"])
            mv_p_, mv_z_ = rs(results, rk, "z_adj_poss", "movers"), rs(results, rk, "z_adj", "movers")
            pzm = None if None in mv_p_ + mv_z_ else beats(mv_p_, mv_z_)
            pt = beats(rs(results, rk, "z_adj_poss", block="methods_poss_target"),
                       rs(results, rk, "z_adj", block="methods_poss_target"))
            sents.append("팀 점유율 보정은 " + ("넣으면 더 정확해짐." if pz else "넣으면 오히려 덜 정확해져서 쓰지 않음.")
                         + (" 팀을 옮긴 선수는 표본이 30명 미만이라 따로 판정하지 못함." if pzm is None else ""))
            v.update(poss_helps=pz, poss_helps_movers=pzm, poss_helps_poss_target=pt)
        v["text"] = " ".join(sents)
        out[rk] = v
    att = [p["attack"]["methods"] for p in results]
    best = max((k for k in att[0] if k != "prev"), key=lambda k: sum(m[k]["r"] for m in att))
    win = all(m[best]["r"] > m["prev"]["r"] for m in att)
    mb, mp = _avg([m[best]["r"] for m in att]), _avg([m["prev"]["r"] for m in att])
    out["attack"] = dict(best=best, beats_prev=win, best_r=[m[best]["r"] for m in att],
                         prev_r=[m["prev"]["r"] for m in att], text=(
        "공격 생산 전체((npxG+xA)/90)는 "
        + ("역할 점수가 지난 시즌 값보다 나은 쪽" if win else "지난 시즌 값을 그대로 쓰는 쪽이 더 정확함")
        + f" (평균 r {_r2(mp)} 대 역할 점수 {_r2(mb)})."))
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
    print(f"백테스트 시즌쌍: {[f'{season_label(a)}→{season_label(b)}' for a, b in pairs]}")

    # K 누수 차단: 시험 쌍을 뺀 나머지 쌍으로 K를 추정한다.
    results, k_used = [], {}
    for pr in pairs:
        others = [q for q in pairs if q != pr]
        if not others:
            sys.exit("시험 쌍을 뺀 K 추정용 시즌쌍이 없습니다 — 완주 시즌쌍이 2개 이상 필요합니다.")
        K, k_info = estimate_k(raw, years, pairs=others,
                               log=lambda s, pr=pr: print(f"  [{season_label(pr[0])}→{season_label(pr[1])} 시험용] {s}"))
        k_used[f"{season_label(pr[0])}→{season_label(pr[1])}"] = dict(
            from_pairs=[f"{season_label(a)}→{season_label(b)}" for a, b in others],
            k={m: k_info[m]["K"] for m in k_info})
        results.append(run_pair(raw, pr[0], pr[1], K))
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
                  k_by_pair=k_used,
                  note="K는 시험 쌍을 뺀 나머지 완주 시즌쌍으로 추정했다. 배포용 K는 전체 쌍으로 추정한다."),
        pairs=results, verdict=v,
    )
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "backtest.json"), "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, separators=(",", ":"))
    print("\ndocs/backtest.json 생성 완료")


if __name__ == "__main__":
    main()
