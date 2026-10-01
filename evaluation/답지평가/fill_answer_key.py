# -*- coding: utf-8 -*-
"""답지에서 뺀 페이백 체감가 정답 45개를, 답지를 만든 규칙으로 다시 채운다(8/21 고정본).

1) 규칙 추정: 조건을 만족하는 요금제(통신 3사 포함)를 원본 CSV 가격(체감가 포함) 낮은 순으로 줄 세웠을 때
   원래 답과 얼마나 겹치나. 동점 처리 방식별로 재서 가장 잘 맞는 것을 고른다(방법 결과는 보지 않는다).
2) 채우기: 같은 규칙에 가격만 실제 청구액(서비스 로딩 보정값)으로 바꿔, 뺀 개수만큼 다음 순위로 채운다.
출력: filled_answer_ids.json (qid -> 정답 칸별 plan_id 목록), filled_answer_key.csv (사람용)
"""
import json, sys, os
from pathlib import Path
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO)); os.chdir(REPO)
import agent.data as D
D.PLANS_CSV = D.BASELINE_PLANS_CSV
D.BENEFITS_CSV = D.BASELINE_DIR / "통신요금제_혜택상세_최종.csv"
from agent.data import all_plans, filter_candidates
from experiments.Calc_precision_recall import load_wide_rows

K = 5
key = load_wide_rows(str(REPO / "data/eval/test_cases_정답지.xlsx"))
profiles = json.loads((HERE / "testset_profiles.json").read_text(encoding="utf-8"))
P = {str(p["plan_id"]): p for p in all_plans()}

RAW = pd.read_csv(D.PLANS_CSV, dtype={"plan_id": str})
RAW["plan_id"] = RAW["plan_id"].astype(str).str.strip()
RAW["name"] = RAW["plan_name"].astype(str).str.strip()
RAW["eff"] = [d if pd.notna(d) and d != 0 else m for d, m in zip(RAW["discounted_fee"], RAW["monthly_fee"])]
RAW_EFF = dict(zip(RAW["plan_id"], RAW["eff"]))
RAW_BY_NAME = {n: g for n, g in RAW.groupby("name")}
CORRECTED = set(pd.read_csv(D.VERIFIED_BILLING_CSV, dtype={"plan_id": str})["plan_id"].astype(str))

def answer_ids(ans):
    out = []
    for a in ans:
        g = RAW_BY_NAME.get(a["name"])
        ids = set() if g is None else {pid for pid, e in zip(g["plan_id"], g["eff"]) if pd.notna(e) and abs(float(e) - float(a["price"])) <= 1}
        out.append({i for i in ids if i in P})
    return out

def gb(p):
    return 10**4 if p.get("effective_unlimited") else float(p.get("data_gb") or 0)

def price_of(p, raw):
    if raw:
        return float(RAW_EFF.get(str(p["plan_id"]), 10**9))
    d, m = p.get("discounted_fee"), p.get("monthly_fee")
    return float(d if d not in (None, "", 0) and d == d else m)

TIES = {
    "가격↑, 동점은 데이터↓": lambda p, raw: (price_of(p, raw), -gb(p), str(p["plan_id"])),
    "가격↑, 동점은 데이터↑": lambda p, raw: (price_of(p, raw), gb(p), str(p["plan_id"])),
    "가격↑, 동점은 ID": lambda p, raw: (price_of(p, raw), str(p["plan_id"])),
}

def ranked_names(p, sort_key, raw):
    """조건 만족 요금제를 규칙 순으로. 이름+가격이 같은 요금제는 한 칸으로 묶는다(답지도 이름 단위)."""
    cands = filter_candidates({**p, "include_mno": True})
    cands = sorted(cands, key=lambda c: sort_key(c, raw))
    slots, seen = [], set()
    for c in cands:
        sig = (str(c["plan_name"]).strip(), round(price_of(c, raw)))
        if sig in seen: continue
        seen.add(sig)
        slots.append({i for i, q in P.items() if str(q["plan_name"]).strip() == sig[0] and round(price_of(q, raw)) == sig[1]})
    return slots

# ── 1) 규칙 추정: 페이백이 아닌 원래 정답만으로 겹침을 잰다(페이백 상품은 가격이 달라 공정하게 못 잰다)
agree = {}
for tname, tk in TIES.items():
    hits = total = 0
    for qid, q in key.items():
        p = profiles.get(qid)
        if p is None or q["level"] == 4: continue  # 레벨 4 는 현재 요금제 비교라 규칙이 다르다
        ans = [s for s in answer_ids(q["items"]) if s and not (s & CORRECTED)]
        if not ans: continue
        top = ranked_names(p, tk, raw=False)
        t5 = set().union(*top[:K]) if top else set(); t10 = set().union(*top[:2 * K]) if top else set()
        hits += sum(bool(s & t5) for s in ans); hits10 = agree.get(tname + "_10", 0) + sum(bool(s & t10) for s in ans)
        agree[tname + "_10"] = hits10; total += len(ans)
    agree[tname] = (hits, total)
    print(f"[규칙 추정] {tname}: 원래 정답 {total}개 중 규칙 상위 5개에 {hits}개 ({100*hits/total:.1f}%), 상위 10개에 {agree[tname + '_10']}개 ({100*agree[tname + '_10']/total:.1f}%)")
best = max(TIES, key=lambda k: agree[k][0] / agree[k][1])
print("채택 규칙:", best)

# ── 2) 채우기: 뺀 개수만큼, 실제 청구액 기준 같은 규칙의 다음 순위로
filled, rows = {}, []
for qid, q in key.items():
    p = profiles.get(qid)
    ids = answer_ids(q["items"])
    keep = [s for s in ids if not (s & CORRECTED)]
    need = len(ids) - len(keep)
    new = []
    if need and p is not None:
        taken = set().union(*[s for s in keep if s]) if any(keep) else set()
        for slot in ranked_names(p, TIES[best], raw=False):
            if slot & taken: continue
            new.append(slot); taken |= slot
            if len(new) == need: break
    filled[qid] = [sorted(s) for s in keep + new]
    for s in new:
        x = P[sorted(s)[0]]
        rows.append({"qid": qid, "question": q["question"], "added_name": x["plan_name"],
                     "added_price": price_of(x, False), "added_data": x.get("data_gb"), "carrier": x.get("carrier_type")})
    if need and len(new) < need:
        rows.append({"qid": qid, "question": q["question"], "added_name": f"(채울 요금제 부족: {need - len(new)}칸 비움)"})

(HERE / "filled_answer_ids.json").write_text(json.dumps(filled, ensure_ascii=False, indent=1), encoding="utf-8")
pd.DataFrame(rows).to_csv(HERE / "filled_answer_key.csv", index=False, encoding="utf-8-sig")
print(f"채운 칸 {sum(1 for r in rows if not str(r.get('added_name','')).startswith('('))} / 비운 칸 {sum(1 for r in rows if str(r.get('added_name','')).startswith('('))}")
