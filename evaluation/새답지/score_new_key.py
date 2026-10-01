# -*- coding: utf-8 -*-
"""새 답지(0/1/2 등급)로 방식별 지표를 계산하고, 재채점 일치율과 사람 검수 시트를 만든다.

지표: nDCG@5(이득 2^g-1) · P@5(1점 이상) · P@5(2점만) · Hit@1(1순위 2점) · 위반률(0점 비율) · 더 나은 대안 없음.
(무작위 기준선은 풀 구성상 공정하지 않아 뺐다)
"""
import json, sys, os, math, random, glob
from pathlib import Path
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO)); os.chdir(REPO)
import agent.data as D
D.PLANS_CSV = D.BASELINE_PLANS_CSV
D.BENEFITS_CSV = D.BASELINE_DIR / "통신요금제_혜택상세_최종.csv"
from agent.data import all_plans
from experiments.Calc_precision_recall import load_wide_rows

K = 5
key = load_wide_rows(str(REPO / "data/eval/test_cases_정답지.xlsx"))
mapping = json.loads((HERE / "pool_mapping.json").read_text(encoding="utf-8"))
P = {str(p["plan_id"]): p for p in all_plans()}
res = pd.read_csv(HERE / "answer_key_results_baseline.csv", dtype={"top5_ids": str})

def load_grades(files):
    g = {}
    for f in files:
        for r in json.loads(Path(f).read_text(encoding="utf-8")):
            pid = mapping.get(r["qid"], {}).get(r["code"])
            if pid is not None:
                g[(r["qid"], pid)] = (int(r["grade"]), str(r.get("reason", "")))
    return g

G = load_grades(sorted(glob.glob(str(HERE / "채점원본" / "grades_*.json"))))
expected = sum(len(v) for v in mapping.values())
missing = [(q, c) for q, cs in mapping.items() for c, pid in cs.items() if (q, pid) not in G]
print(f"채점 {len(G)} / 후보 {expected} · 빠진 후보 {len(missing)}")

# 방식별 Top-5
lists = {}
for (qid, method), g in res.groupby(["qid", "method"]):
    lists[(qid, method)] = [i for i in str(g["top5_ids"].iloc[0] if g["top5_ids"].notna().iloc[0] else "").split("|") if i in P]
methods = list(dict.fromkeys(res["method"]))
ungraded = sum((q, r) not in G for (q, m), rec in lists.items() for r in rec)
print("채점 안 된 추천", ungraded)

def ndcg(grades_list, pool_grades):
    gain = lambda g: 2 ** g - 1
    dcg = sum(gain(g) / math.log2(i + 2) for i, g in enumerate(grades_list[:K]))
    ideal = sorted(pool_grades, reverse=True)[:K]
    idcg = sum(gain(g) / math.log2(i + 2) for i, g in enumerate(ideal))
    return None if idcg == 0 else dcg / idcg

nondom = res.set_index(["qid", "method"])["nondom"].to_dict()
rows = []
for qid in mapping:
    pool_g = [G[(qid, pid)][0] for pid in mapping[qid].values() if (qid, pid) in G]
    for m in methods:
        rec = lists.get((qid, m), [])
        gl = [G.get((qid, r), (0, ""))[0] for r in rec]
        rows.append({"qid": qid, "level": key[qid]["level"], "method": m, "n_rec": len(rec),
                     "ndcg5": ndcg(gl, pool_g), "p5_any": sum(g >= 1 for g in gl) / K, "p5_good": sum(g == 2 for g in gl) / K,
                     "hit1": (gl[0] == 2) if gl else False, "viol": (sum(g == 0 for g in gl) / len(gl)) if gl else None,
                     "nondom": nondom.get((qid, m)), "pool_has_good": any(g == 2 for g in pool_g)})
df = pd.DataFrame(rows)
df.to_csv(HERE / "new_key_scores.csv", index=False, encoding="utf-8-sig")

agg = df.groupby("method", sort=False).agg(nDCG5=("ndcg5", "mean"), P5_1점이상=("p5_any", "mean"), P5_2점=("p5_good", "mean"),
                                           Hit1=("hit1", "mean"), 위반률=("viol", "mean"), 대안없음=("nondom", "mean"),
                                           추천없음=("n_rec", lambda s: int((s == 0).sum())))
for c in ("nDCG5", "P5_1점이상", "P5_2점", "Hit1", "위반률", "대안없음"):
    agg[c] = (agg[c] * 100).round(1)
print(agg.to_string())
lvl = df.pivot_table(index="method", columns="level", values="ndcg5", aggfunc="mean", sort=False).mul(100).round(1)
print("\n레벨별 nDCG@5"); print(lvl.to_string())

w = df.pivot(index="qid", columns="method", values="ndcg5").dropna()
g_ = lambda s: [c for c in w.columns if c.startswith(s)][0]
pairs = []
for a, b in [("④'", "③'"), ("④ ", "③ "), ("④'", "②"), ("④'", "①")]:
    d = (w[g_(a)] - w[g_(b)]).to_numpy(); r = np.random.default_rng(0)
    bs = [r.choice(d, len(d)).mean() for _ in range(2000)]
    pairs.append({"비교": f"{g_(a)} vs {g_(b)}", "차이": round(100 * d.mean(), 1), "CI_low": round(100 * np.percentile(bs, 2.5), 1),
                  "CI_high": round(100 * np.percentile(bs, 97.5), 1), "승": int((d > 1e-9).sum()), "패": int((d < -1e-9).sum()), "문항": len(d)})
print("\nnDCG@5 쌍 비교"); print(pd.DataFrame(pairs).to_string(index=False))

dist = pd.Series([v[0] for v in G.values()]).value_counts().sort_index()
print("\n등급 분포", dist.to_dict(), "· 2점이 하나도 없는 문항", int((~df.drop_duplicates('qid').pool_has_good).sum()))

# 재채점 일치율
R = load_grades(sorted(glob.glob(str(HERE / "채점원본" / "recheck_*.json"))))
both = [(G[k][0], R[k][0]) for k in R if k in G]
agree = {}
if both:
    a = np.array(both)
    exact = float((a[:, 0] == a[:, 1]).mean()); within1 = float((abs(a[:, 0] - a[:, 1]) <= 1).mean())
    # 이차 가중 kappa
    O = np.zeros((3, 3))
    for x, y in both: O[x, y] += 1
    O /= O.sum(); E = np.outer(O.sum(1), O.sum(0)); Wt = np.array([[(i - j) ** 2 / 4 for j in range(3)] for i in range(3)])
    kappa = 1 - (Wt * O).sum() / (Wt * E).sum()
    agree = {"n": len(both), "exact": round(100 * exact, 1), "within1": round(100 * within1, 1), "weighted_kappa": round(float(kappa), 3)}
    print("\n재채점 일치:", agree)

# 새 답지(사람이 읽는 표) + 사람 검수 시트
key_rows = []
for qid, cs in mapping.items():
    for code, pid in cs.items():
        g, why = G.get((qid, pid), (None, ""))
        p = P[pid]
        key_rows.append({"qid": qid, "level": key[qid]["level"], "question": key[qid]["question"], "code": code, "plan_id": pid,
                         "plan_name": p["plan_name"], "carrier_type": p.get("carrier_type"), "월청구액": p.get("discounted_fee"),
                         "data_gb": p.get("data_gb"), "등급": g, "근거": why})
kdf = pd.DataFrame(key_rows).sort_values(["qid", "등급", "월청구액"], ascending=[True, False, True])
kdf.to_csv(HERE / "new_answer_key.csv", index=False, encoding="utf-8-sig")
sample_q = sorted(random.Random("human-check").sample(list(mapping), 10))
hs = kdf[kdf.qid.isin(sample_q)].copy(); hs["사람_등급"] = ""; hs = hs.drop(columns=["등급", "근거"])
hs.to_csv(HERE / "human_check_sheet.csv", index=False, encoding="utf-8-sig")

json.dump({"coverage": {"graded": len(G), "expected": expected, "missing": len(missing)},
           "summary": agg.reset_index().to_dict("records"), "by_level_ndcg": lvl.reset_index().to_dict("records"),
           "pairs": pairs, "grade_dist": {int(k): int(v) for k, v in dist.items()}, "recheck_agreement": agree,
           "human_check_qids": sample_q}, open(HERE / "new_key_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
