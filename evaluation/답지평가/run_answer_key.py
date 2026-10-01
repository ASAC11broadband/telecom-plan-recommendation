# -*- coding: utf-8 -*-
"""시나리오 답지 100문항으로 추천 방식 4개 비교. 저장소 코드는 import 만 하고 고치지 않는다.

문항 → 조건: testset_profiles.json (git 904573c 의 data/eval/testset_profiles.json 사본, LLM 이 추출해 캐시해 둔 것).
방식: 세그먼트 인기 / v1 코사인 / 단순 SMAA-2(균등 난수 가중치) / SMAA-2 + 회귀계수(현행). SMAA 두 가지는 통신 3사 포함으로도 돌린다.
지표: Precision@5(답지 이름+가격 일치, 분모 5) · 같은 스펙 허용 P@5 · 조건 충족 · 더 나은 대안 없음.
"""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import os; os.chdir(REPO)

# CATALOG=baseline 이면 8/21 고정본(가중치를 학습한 시점)으로 카탈로그를 바꾼다. 저장소 파일은 그대로다.
import agent.data as _D
CATALOG = os.environ.get("CATALOG", "latest")
EXCL = os.environ.get("EXCLUDE_PAYBACK") == "1"
FILLED = os.environ.get("ANSWER_IDS")  # fill_answer_key.py 가 만든 채운 답지(qid -> 칸별 plan_id)
if CATALOG == "baseline":
    _D.PLANS_CSV = _D.BASELINE_PLANS_CSV
    _D.BENEFITS_CSV = _D.BASELINE_DIR / "통신요금제_혜택상세_최종.csv"

from agent.data import all_plans, filter_candidates
from agent import mcda
from agent.agents.recommend import _dedupe_identical_offers
from agent.cosine_recommendation import prepare_plan_catalog, recommend_by_cosine
from agent.segmentation import fit_model, holdout_100, load_interactions, segment_popularity_recommend
from experiments.run_metric_table import query_frame, _num, UNLIMITED_GB
from experiments.Calc_precision_recall import load_wide_rows

K = 5
key = load_wide_rows(str(REPO / "data/eval/test_cases_정답지.xlsx"))
profiles = json.loads((HERE / "testset_profiles.json").read_text(encoding="utf-8"))
plans = all_plans()
P = {str(p["plan_id"]): p for p in plans}

def price(p):
    d, m = p.get("discounted_fee"), p.get("monthly_fee")
    return d if d not in (None, "", 0) and d == d else m

def spec(p):
    unl = bool(p.get("effective_unlimited"))
    return ("U" if unl else round(_num(p.get("data_gb")), 1), round(_num(price(p))), round(_num(p.get("qos_mbps")), 2),
            bool(p.get("voice_unlimited")))

# 답지 항목 → 원본 CSV(가격 보정 전)에서 이름+가격이 같은 요금제 ID 집합.
# 서비스는 로딩 때 페이백 상품 가격을 청구액으로 바로잡으므로, 가격으로 다시 맞추면 같은 요금제도 빗나간다.
RAW = pd.read_csv(_D.PLANS_CSV, dtype={"plan_id": str})
RAW["plan_id"] = RAW["plan_id"].astype(str).str.strip()
RAW["name"] = RAW["plan_name"].astype(str).str.strip()
RAW["eff"] = [d if pd.notna(d) and d != 0 else m for d, m in zip(RAW["discounted_fee"], RAW["monthly_fee"])]
RAW_BY_NAME = {n: g for n, g in RAW.groupby("name")}
CORRECTED = set(pd.read_csv(_D.VERIFIED_BILLING_CSV, dtype={"plan_id": str})["plan_id"].astype(str)) if _D.VERIFIED_BILLING_CSV.exists() else set()
def answer_ids(ans):
    out = []
    for a in ans:
        g = RAW_BY_NAME.get(a["name"])
        ids = set() if g is None else {pid for pid, e in zip(g["plan_id"], g["eff"]) if pd.notna(e) and abs(float(e) - float(a["price"])) <= 1}
        out.append({i for i in ids if i in P})
    return out

# ── 파레토: 전체 카탈로그(통신 3사 포함) 7축
pool = pd.DataFrame(plans).drop_duplicates("plan_id").copy()
pool["plan_id"] = pool["plan_id"].astype(str)
pool = pool[pool["billing_price_known"].astype(bool)]
unl = pool["effective_unlimited"].astype(bool)
cols = {
    "fee": -np.array([_num(price(r)) for r in pool.to_dict("records")]),
    "data": np.where(unl, UNLIMITED_GB, [_num(v) for v in pool["data_gb"]]),
    "voice": np.where(pool["voice_unlimited"].astype(bool), 1000.0, [_num(v) for v in pool["voice_minutes"]]),
    "sms": np.where(pool["sms_unlimited"].astype(bool), 500.0, [_num(v) for v in pool["sms_count"]]),
    "ott": pool["ott_options"].fillna("").astype(str).str.strip().ne("").astype(float).to_numpy(),
    "qos": np.array([_num(v) for v in pool["qos_mbps"]]),
    "tether": np.array([_num(v) for v in pool["tethering_gb"]]),
}
Mx = np.column_stack(list(cols.values()))
NONDOM = {}
for i, pid in enumerate(pool["plan_id"]):
    NONDOM[pid] = not bool((np.all(Mx >= Mx[i], axis=1) & np.any(Mx > Mx[i], axis=1)).any())

# ── 방법
train, _ = holdout_100(load_interactions(), seed=42, size=100)
seg_model = fit_model(train)
catalog = prepare_plan_catalog()
MED_NEED, MED_BUDGET = float(train["data_gb_month"].median()), float(train["budget_krw"].median())
OTT = ("넷플릭스", "티빙", "유튜브", "웨이브", "디즈니", "왓챠", "쿠팡", "지니", "멜론")

def need_of(p):
    for f in ("min_data_gb", "target_data_gb", "estimated_monthly_data_gb"):
        if p.get(f): return float(p[f])
    return None

def frame_of(p, for_segment):
    need, budget = need_of(p), p.get("budget_max_won")
    f = query_frame(need if need is not None else (MED_NEED if for_segment else 0.0),
                    budget if budget else (MED_BUDGET if for_segment else 0))
    f.loc[0, "data_unlimited_need"] = bool(p.get("data_unlimited"))
    f.loc[0, "voice_unlimited_need"] = bool(p.get("voice_unlimited"))
    f.loc[0, "sms_unlimited_need"] = bool(p.get("sms_unlimited"))
    f.loc[0, "voice_minutes_need"] = float(p.get("min_voice_minutes") or 0)
    ott = [b for b in (p.get("wanted_benefits") or []) if any(o in str(b) for o in OTT)]
    f.loc[0, "ott_want"] = ott[0] if ott else ""
    f.loc[0, "ott_required"] = bool(ott)
    return f

def m_segment(p):
    return [str(x["plan_id"]) for x in segment_popularity_recommend(seg_model, frame_of(p, True), K)["ranked"]]

def m_cosine(p):
    try:
        return [x["plan_id"] for x in recommend_by_cosine(catalog, frame_of(p, False).iloc[0], K)["ranked"]]
    except ValueError:
        return []

RIDGE = mcda._BASE_WEIGHTS
rng = np.random.default_rng(42)
UNIFORM = rng.dirichlet(np.ones(len(mcda.CRITERIA)), size=len(RIDGE)).tolist()  # 가중치 정보 없음: 단체(simplex) 위 균등

def m_smaa(p, weights, mno=False):
    mcda._BASE_WEIGHTS = weights
    try:
        cands = _dedupe_identical_offers(filter_candidates({**p, "include_mno": True} if mno else p))
        if not cands: return []
        dec = mcda.evaluate_mcda(cands, p.get("priorities") or [], comparison_goals=p.get("comparison_goals") or [], profile=p)
        return [d.plan_id for d in mcda.rank_smaa2(dec)[:K]]
    finally:
        mcda._BASE_WEIGHTS = RIDGE

METHODS = {
    "① 세그먼트 분류(협업)": m_segment,
    "② 코사인 유사도(콘텐츠)": m_cosine,
    "③ 단순 SMAA-2(균등 난수 가중치)": lambda p: m_smaa(p, UNIFORM),
    "④ SMAA-2 + 회귀계수(채택)": lambda p: m_smaa(p, RIDGE),
    "③' 단순 SMAA-2 · 통신 3사 포함": lambda p: m_smaa(p, UNIFORM, True),
    "④' SMAA-2 + 회귀계수 · 통신 3사 포함": lambda p: m_smaa(p, RIDGE, True),
}

# ── 채점
rows, coverage = [], []
for qid, q in key.items():
    p = profiles.get(qid)
    if p is None: continue
    ans = q["items"]
    aids = [set(x) for x in json.loads(Path(FILLED).read_text(encoding="utf-8"))[qid]] if FILLED else answer_ids(ans)
    payback_ans = sum(bool(s & CORRECTED) for s in aids)
    if EXCL:  # 페이백 체감가로 뽑힌 정답은 뺀다(서비스는 실제 청구액으로 순위를 매긴다)
        aids = [s for s in aids if not (s & CORRECTED)]
    found = sum(bool(s) for s in aids)
    denom = min(K, found)
    coverage.append((found, len(ans)))
    aspecs = [{spec(P[i]) for i in s} for s in aids]
    ok_ids = {str(c["plan_id"]) for c in filter_candidates({**p, "include_mno": True})}
    for name, fn in METHODS.items():
        rec = [r for r in fn(p) if r in P][:K]
        # 정확 일치: 같은 요금제(ID), 1:1 그리디
        left, exact = list(aids), 0
        for r in rec:
            for i, s in enumerate(left):
                if r in s:
                    exact += 1; left.pop(i); break
        # 스펙 일치 허용: 데이터·요금·소진 후 속도·통화 무제한이 같은 답지 요금제가 있으면 정답
        pool_specs, sp = list(aspecs), 0
        for r in rec:
            s = spec(P[r])
            for i, ss in enumerate(pool_specs):
                if s in ss:
                    sp += 1; pool_specs.pop(i); break
        rows.append({
            "qid": qid, "level": q["level"], "ans_found": found, "ans_payback": payback_ans, "question": q["question"], "method": name, "n_rec": len(rec),
            "p5_exact": exact / K, "p5_spec": sp / K,
            "p5_matchable": (exact / denom) if denom else None,
            "cond_ok": (sum(r in ok_ids for r in rec) / len(rec)) if rec else None,
            "nondom": (sum(NONDOM.get(r, False) for r in rec) / len(rec)) if rec else None,
            "top5": " | ".join(f"{P[r]['plan_name']}({_num(price(P[r])):.0f})" for r in rec),
        })

df = pd.DataFrame(rows)
df.to_csv(HERE / f"answer_key_results_{CATALOG}{'_nopayback' if EXCL else ''}{'_filled' if FILLED else ''}.csv", index=False, encoding="utf-8-sig")
agg = df.groupby("method", sort=False).agg(
    P5_정확=("p5_exact", "mean"), P5_맞힐수있는정답기준=("p5_matchable", "mean"), P5_스펙허용=("p5_spec", "mean"),
    조건충족=("cond_ok", "mean"), 대안없음=("nondom", "mean"),
    추천없음=("n_rec", lambda s: int((s == 0).sum())), 문항=("qid", "count"))
for c in ("P5_정확", "P5_맞힐수있는정답기준", "P5_스펙허용", "조건충족", "대안없음"):
    agg[c] = (agg[c] * 100).round(1)
lvl = df.pivot_table(index="method", columns="level", values="p5_exact", aggfunc="mean", sort=False).mul(100).round(1)
lvl_n = df[df.method == list(METHODS)[0]].groupby("level").size()
found = sum(f for f, _ in coverage); total = sum(t for _, t in coverage)
print(f"[{CATALOG}] 카탈로그 {len(P)}행 · 문항 {len(coverage)} · 답지 요금제 {total}개 중 현재 카탈로그에 있는 것 {found}개 ({100*found/total:.1f}%)")
print(agg.to_string())
print("\n레벨별 P5_정확 (문항 수:", dict(lvl_n), ")")
print(lvl.to_string())
json.dump({"coverage": [found, total], "summary": agg.reset_index().to_dict("records"),
           "by_level_p5_exact": lvl.reset_index().to_dict("records"), "level_n": {str(k): int(v) for k, v in lvl_n.items()}},
          open(HERE / f"answer_key_summary_{CATALOG}{'_nopayback' if EXCL else ''}{'_filled' if FILLED else ''}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
