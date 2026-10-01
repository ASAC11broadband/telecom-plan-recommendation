# -*- coding: utf-8 -*-
"""답지 없이 재는 지표 묶음. 8/21 고정본 기준. 저장소 코드는 import 만 한다.

A. 100문항 시나리오(answer_key_results_baseline.csv 의 방식별 Top-5 + 기준선 '조건 내 최저가 5')
   품질: 조건 충족 · 파레토 비지배(전체) · 파레토 비지배(조건 내) · 가격 위치(할인가/12개월) · 가성비 위치(12개월 원/GB)
   위험: 할인 종료 충격(정가 ≥ 할인가×1.5) · 정보 누락(소진 후 속도 모름)
   목록: 근사중복(요금·데이터 ±10% 쌍) · 브랜드 다양성
   전체: 커버리지(서로 다른 추천 요금제 수) · 개인화(1 − 문항 간 Top-5 자카드 평균) · 인기 편향(가입자 수 백분위)
B. 24개 격자 질의(필요 {5,20,50,100}GB × 예산 {1,1.5,2,3,4,6}만원, 알뜰폰만)
   예산 단조성 위반 · 예산 5% 흔들 때 Top-5 유지율 · 선호 반응('속도 우선'/'데이터 우선'에 Top-5 평균이 움직이는 양)
"""
import json, sys, os, itertools
from pathlib import Path
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO)); os.chdir(REPO)
import agent.data as D
D.PLANS_CSV = D.BASELINE_PLANS_CSV
D.BENEFITS_CSV = D.BASELINE_DIR / "통신요금제_혜택상세_최종.csv"
from agent.data import all_plans, filter_candidates
from agent import mcda
from agent.mcda import _switching_monthly_fee
from agent.agents.recommend import _dedupe_identical_offers
from agent.cosine_recommendation import prepare_plan_catalog, recommend_by_cosine
from agent.segmentation import fit_model, holdout_100, load_interactions, segment_popularity_recommend
from experiments.run_metric_table import query_frame, _num, UNLIMITED_GB, NEEDS, BUDGETS

K = 5
SRC = HERE.parent / "새답지"  # 문항 조건과 방식별 Top-5 는 새답지 것을 쓴다
profiles = json.loads((SRC / "testset_profiles.json").read_text(encoding="utf-8"))
P = {str(p["plan_id"]): p for p in all_plans()}
res = pd.read_csv(SRC / "answer_key_results_baseline.csv", dtype={"top5_ids": str})
RAW = pd.read_csv(D.PLANS_CSV, dtype={"plan_id": str}); RAW["plan_id"] = RAW["plan_id"].astype(str).str.strip()

def fee(p):
    d, m = p.get("discounted_fee"), p.get("monthly_fee")
    return float(d if d not in (None, "", 0) and d == d else m or 0)
def data_gb(p):
    return UNLIMITED_GB if p.get("effective_unlimited") else _num(p.get("data_gb"))
def vec(p):  # 파레토 7축, 전부 클수록 좋게
    return np.array([-fee(p), data_gb(p),
                     1000.0 if p.get("voice_unlimited") else _num(p.get("voice_minutes")),
                     500.0 if p.get("sms_unlimited") else _num(p.get("sms_count")),
                     float(bool(str(p.get("ott_options") or "").strip() not in ("", "nan"))),
                     _num(p.get("qos_mbps")), _num(p.get("tethering_gb"))])
def fee12(p):
    return float(_switching_monthly_fee(p))
def brand(p):
    return p.get("mvno_brand") if p.get("carrier_type") == "MVNO" else p.get("carrier")

ALL = [p for p in P.values() if p.get("billing_price_known", True)]
ALLV = np.array([vec(p) for p in ALL])
def dominated_in(pv, M):
    return bool((np.all(M >= pv, axis=1) & np.any(M > pv, axis=1)).any())

subs = RAW.set_index("plan_id")["subscriber_count"].dropna()
subs = subs[~subs.index.duplicated()]
SUB_PCT = subs.rank(pct=True).to_dict()

def pct_rank(x, arr):
    arr = np.asarray(arr, float)
    return 100.0 * (arr < x).sum() / max(len(arr) - 1, 1)

# ── 방식 목록(A): 시나리오 결과 + 기준선. 통신 3사 포함 여부를 이름에 밝힌다.
METHODS = {
    "① 세그먼트 분류": ("① 세그먼트 분류(협업)", "mvno"),
    "② 코사인 유사도": ("② 코사인 유사도(콘텐츠)", "mvno"),
    "③ 단순 SMAA-2 (3사 포함)": ("③' 단순 SMAA-2 · 통신 3사 포함", "all"),
    "④ SMAA-2 + 회귀계수 (3사 포함)": ("④' SMAA-2 + 회귀계수 · 통신 3사 포함", "all"),
    "③ 단순 SMAA-2 (알뜰폰만)": ("③ 단순 SMAA-2(균등 난수 가중치)", "mvno"),
    "④ SMAA-2 + 회귀계수 (알뜰폰만, 채택)": ("④ SMAA-2 + 회귀계수(채택)", "mvno"),
}
lists, SCOPE = {}, {}
for short, (full, scope) in METHODS.items():
    SCOPE[short] = scope
    for qid, s in res.loc[res.method == full, ["qid", "top5_ids"]].itertuples(index=False):
        lists[(qid, short)] = [i for i in (s.split("|") if isinstance(s, str) else []) if i in P]
FEAS = {"all": {}, "mvno": {}}
for qid, p in profiles.items():
    F = [str(c["plan_id"]) for c in _dedupe_identical_offers(filter_candidates({**p, "include_mno": True})) if str(c["plan_id"]) in P]
    FEAS["all"][qid] = F
    FEAS["mvno"][qid] = [i for i in F if P[i].get("carrier_type") == "MVNO"]
BASES = {"기준선: 조건 내 최저가 5 (3사 포함)": ("all", fee), "기준선: 조건 내 12개월 최저가 5 (3사 포함)": ("all", fee12)}
for b, (scope, key_fn) in BASES.items():
    SCOPE[b] = scope
    for qid in profiles:
        lists[(qid, b)] = sorted(FEAS[scope][qid], key=lambda i, kf=key_fn: (kf(P[i]), -data_gb(P[i]), i))[:K]
NAMES = list(METHODS) + list(BASES)

def vec12(p):
    v = vec(p); v[0] = -fee12(p); return v
ALLV12 = np.array([vec12(p) for p in ALL])

def usable_gb(p, need):  # 필요량이 있으면 그만큼만 쓸모 있다고 본다
    g = min(data_gb(p), 300.0)
    return max(min(g, need) if need else g, 0.5)

rows = []
for qid, p in profiles.items():
    need = next((float(p[f]) for f in ("min_data_gb", "target_data_gb", "estimated_monthly_data_gb") if p.get(f)), None)
    Fcond = set(FEAS["all"][qid])  # 조건 충족 판정은 범위와 무관하게 같은 조건 집합
    for m in NAMES:
        F = FEAS[SCOPE[m]][qid]; Fs = set(F)
        FV = np.array([vec(P[i]) for i in F]) if F else None
        FV12 = np.array([vec12(P[i]) for i in F]) if F else None
        rec = lists.get((qid, m), [])
        if not rec:
            rows.append({"qid": qid, "method": m, "n": 0, "F_size": len(F)}); continue
        pr = [P[i] for i in rec]
        inF = [P[i] for i in rec if i in Fs]
        f_fee = [fee(P[i]) for i in F]; f_fee12 = [fee12(P[i]) for i in F]; f_pg = [fee12(P[i]) / usable_gb(P[i], need) for i in F]
        full = len(pr) == K
        pairs = list(itertools.combinations(pr, 2))
        near = sum(abs(fee(a) - fee(b)) <= 0.1 * max(fee(a), fee(b), 1) and abs(data_gb(a) - data_gb(b)) <= 0.1 * max(data_gb(a), data_gb(b), 1) for a, b in pairs)
        ratios = [min(data_gb(x), 300.0) / need for x in pr] if need else []
        rows.append({
            "qid": qid, "method": m, "n": len(rec), "F_size": len(F),
            "cond_ok": np.mean([i in Fcond for i in rec]),
            "pareto_all": np.mean([not dominated_in(vec(x), ALLV) for x in pr]),
            "pareto_all12": np.mean([not dominated_in(vec12(x), ALLV12) for x in pr]),
            # 조건 내 파레토: 조건 밖 추천은 실패로 센다
            "pareto_feas": np.mean([(str(x["plan_id"]) in Fs) and not dominated_in(vec(x), FV) for x in pr]) if F else None,
            "pareto_feas12": np.mean([(str(x["plan_id"]) in Fs) and not dominated_in(vec12(x), FV12) for x in pr]) if F else None,
            # 가격·가성비 위치: 조건을 지킨 추천만
            "price_pct": np.mean([pct_rank(fee(x), f_fee) for x in inF]) if inF and len(F) > 1 else None,
            "price12_pct": np.mean([pct_rank(fee12(x), f_fee12) for x in inF]) if inF and len(F) > 1 else None,
            "value_pct": np.mean([pct_rank(fee12(x) / usable_gb(x, need), f_pg) for x in inF]) if inF and len(F) > 1 else None,
            "promo_shock": np.mean([bool(x.get("discount_period_months") == x.get("discount_period_months") and x.get("discount_period_months"))
                                    and _num(x.get("monthly_fee")) >= 1.5 * max(fee(x), 1) for x in pr]),
            "qos_unknown": np.mean([not (_num(x.get("qos_mbps")) > 0) and not x.get("data_unlimited") for x in pr]),
            "near_dup": (near / len(pairs)) if full else None,
            "brand_div": (len({brand(x) for x in pr}) / K) if full else None,
            "popularity": np.mean([SUB_PCT[i] for i in rec if i in SUB_PCT]) * 100 if any(i in SUB_PCT for i in rec) else None,
            "mno_share": np.mean([x.get("carrier_type") == "MNO" for x in pr]),
            "under": np.mean([r < 1 for r in ratios]) if ratios else None,
            "fit": np.mean([1 <= r <= 3 for r in ratios]) if ratios else None,
            "over": np.mean([r > 3 for r in ratios]) if ratios else None,
        })
A = pd.DataFrame(rows)
PCT_COLS = ("cond_ok", "pareto_all", "pareto_all12", "pareto_feas", "pareto_feas12", "promo_shock", "qos_unknown",
            "near_dup", "brand_div", "mno_share", "under", "fit", "over")
aggA = A.groupby("method", sort=False).mean(numeric_only=True)
for c in PCT_COLS:
    aggA[c] = aggA[c] * 100
counts = A.groupby("method", sort=False).agg(price_n=("price_pct", "count"), full_n=("brand_div", "count"), need_n=("under", "count"))
cover, person = {}, {}
common_q = [q for q in profiles if all(lists.get((q, m)) for m in NAMES)]
for m in NAMES:
    sets = [set(lists[(q, m)]) for q in common_q]
    cover[m] = len(set().union(*sets)) if sets else 0
    jac = [len(a & b) / len(a | b) for a, b in itertools.combinations(sets, 2) if a | b]
    person[m] = 100 * (1 - np.mean(jac)) if jac else None
aggA["coverage(공통문항)"] = pd.Series(cover); aggA["personalization(공통문항)"] = pd.Series(person)
aggA = aggA.drop(columns=["n", "F_size"]).join(counts).round(1)

# 쌍 비교(문항 단위 부트스트랩 95% CI)
def paired(a, b, col):
    w = A.pivot(index="qid", columns="method", values=col)[[a, b]].dropna().astype(float)
    d = (w[a] - w[b]).to_numpy()
    if len(d) == 0: return None
    r = np.random.default_rng(0); bs = [r.choice(d, len(d)).mean() for _ in range(2000)]
    s = 100 if col in PCT_COLS else 1
    return {"지표": col, "비교": f"{a} − {b}", "차이": round(s * d.mean(), 1),
            "CI": [round(s * np.percentile(bs, 2.5), 1), round(s * np.percentile(bs, 97.5), 1)], "문항": len(d)}
PAIRS = []
for a, b in [("④ SMAA-2 + 회귀계수 (3사 포함)", "③ 단순 SMAA-2 (3사 포함)"),
             ("④ SMAA-2 + 회귀계수 (3사 포함)", "기준선: 조건 내 최저가 5 (3사 포함)"),
             ("④ SMAA-2 + 회귀계수 (3사 포함)", "기준선: 조건 내 12개월 최저가 5 (3사 포함)")]:
    for col in ("pareto_all", "pareto_all12", "pareto_feas", "pareto_feas12", "price_pct", "price12_pct", "value_pct", "promo_shock", "qos_unknown"):
        r_ = paired(a, b, col)
        if r_: PAIRS.append(r_)

# ── B. 24개 격자(알뜰폰만): 단조성 · 안정성 · 선호 반응
train, _ = holdout_100(load_interactions(), seed=42, size=100)
seg = fit_model(train); cat = prepare_plan_catalog()
RIDGE = mcda._BASE_WEIGHTS
UNIFORM = np.random.default_rng(42).dirichlet(np.ones(len(mcda.CRITERIA)), size=len(RIDGE)).tolist()

def smaa(profile, weights, priorities=None):
    # 서비스와 같게: 우선순위는 인자(가중치 가산)와 프로필(효용 계산) 두 곳에 모두 들어간다
    profile = {**profile, "priorities": list(priorities or [])}
    mcda._BASE_WEIGHTS = weights
    try:
        c = [x for x in _dedupe_identical_offers(filter_candidates(profile)) if x.get("carrier_type") == "MVNO"]
        if not c: return []
        return [d.plan_id for d in mcda.rank_smaa2(mcda.evaluate_mcda(c, priorities or [], profile=profile))[:K]]
    finally:
        mcda._BASE_WEIGHTS = RIDGE

GRID = {
    "① 세그먼트 분류": lambda n, b, pr=None: [str(x["plan_id"]) for x in segment_popularity_recommend(seg, query_frame(n, b), K)["ranked"]],
    "② 코사인 유사도": lambda n, b, pr=None: [x["plan_id"] for x in recommend_by_cosine(cat, query_frame(n, b).iloc[0], K)["ranked"]],
    "③ 단순 SMAA-2 (알뜰폰만)": lambda n, b, pr=None: smaa({"budget_max_won": b, "min_data_gb": n}, UNIFORM, pr),
    "④ SMAA-2 + 회귀계수 (알뜰폰만, 채택)": lambda n, b, pr=None: smaa({"budget_max_won": b, "min_data_gb": n}, RIDGE, pr),
    "기준선: 조건 내 최저가 5 (정의상 단조)": lambda n, b, pr=None: sorted([str(c["plan_id"]) for c in _dedupe_identical_offers(filter_candidates({"budget_max_won": b, "min_data_gb": n})) if c.get("carrier_type") == "MVNO"],
                                       key=lambda i: (fee(P[i]), -data_gb(P[i]), i))[:K],
}
rowsB = {}
for m, fn in GRID.items():
    viol = steps = 0; keep = []; dq, dd = [], []
    for n in NEEDS:
        prev = None
        for b in BUDGETS:
            r = [i for i in fn(n, b) if i in P]
            if not r: continue
            top = fee(P[r[0]])
            if prev is not None:
                steps += 1; viol += top > prev + 1000  # 1,000원 넘게 오를 때만 위반
            prev = top
            r2 = [i for i in fn(n, int(b * 1.05)) if i in P]
            keep.append(len(set(r) & set(r2)) / K)
            if m.startswith(("③", "④")):
                rq = [i for i in fn(n, b, ["qos"]) if i in P]; rd = [i for i in fn(n, b, ["data"]) if i in P]
                base_q = np.mean([_num(P[i].get("qos_mbps")) for i in r]); base_d = np.mean([min(data_gb(P[i]), 300) for i in r])
                if rq: dq.append(np.mean([_num(P[i].get("qos_mbps")) for i in rq]) - base_q)
                if rd: dd.append(np.mean([min(data_gb(P[i]), 300) for i in rd]) - base_d)
    rowsB[m] = {"예산 단조성 위반": f"{viol}/{steps}", "예산 5% 흔들 때 Top-5 유지(%)": round(100 * np.mean(keep), 1),
                "속도 우선 시 소진 후 속도 변화(Mbps)": round(float(np.mean(dq)), 2) if dq else "입력 없음",
                "데이터 우선 시 데이터 변화(GB)": round(float(np.mean(dd)), 1) if dd else "입력 없음"}
B = pd.DataFrame(rowsB).T

A.to_csv(HERE / "extra_metrics_by_question.csv", index=False, encoding="utf-8-sig")
json.dump({"scenario_100": aggA.reset_index().to_dict("records"), "pairs": PAIRS, "common_questions": len(common_q), "grid_24": B.reset_index().to_dict("records")},
          open(HERE / "extra_metrics_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
pd.set_option("display.width", 250)
print("A. 100문항 시나리오 (평균)"); print(aggA.T.to_string())
print("\nB. 24개 격자(알뜰폰만)"); print(B.to_string())
