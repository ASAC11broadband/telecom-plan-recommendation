# -*- coding: utf-8 -*-
"""새 답지용 후보 풀: 문항마다 (6개 실행의 Top-5 합집합) + (조건 만족 무작위 5개).
채점자에게는 어느 방식이 추천했는지 숨기고 순서를 섞은 스펙 카드만 준다. 8/21 고정본 기준.
출력: pool_mapping.json(qid -> {후보코드: plan_id}, 채점자에게 주지 않음), 채점자용_문항/batch_NN.md(채점자용)
다시 돌리면 pool_mapping.json 과 채점자용_문항/*.md 를 덮어써 채점원본과 어긋난다. 기록용으로 둔다.
"""
import json, sys, os, random
from pathlib import Path
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO)); os.chdir(REPO)
import agent.data as D
D.PLANS_CSV = D.BASELINE_PLANS_CSV
D.BENEFITS_CSV = D.BASELINE_DIR / "통신요금제_혜택상세_최종.csv"
from agent.data import all_plans, filter_candidates, find_plans_by_name
from agent.mcda import _switching_monthly_fee
from experiments.Calc_precision_recall import load_wide_rows

key = load_wide_rows(str(REPO / "data/eval/test_cases_정답지.xlsx"))
profiles = json.loads((HERE / "testset_profiles.json").read_text(encoding="utf-8"))
P = {str(p["plan_id"]): p for p in all_plans()}
res = pd.read_csv(HERE / "answer_key_results_baseline.csv", dtype={"top5_ids": str})
N_RANDOM, PER_BATCH = 5, 10

def won(v):
    try: return f"{int(round(float(v))):,}원"
    except (TypeError, ValueError): return "확인 필요"

def card(p):
    unl = bool(p.get("data_unlimited"))
    data = "기본량 무제한" if unl else (f"{float(p['data_gb']):g}GB" if p.get("data_gb") == p.get("data_gb") and p.get("data_gb") is not None else "확인 필요")
    if p.get("daily_data_gb") and p.get("daily_data_gb") == p.get("daily_data_gb"):
        data += f" + 매일 {float(p['daily_data_gb']):g}GB"
    q = p.get("qos_mbps")
    qos = f"{float(q):g}Mbps" if q is not None and q == q and float(q) > 0 else "없음/확인 필요"
    voice = "무제한" if p.get("voice_unlimited") else (f"{int(float(p['voice_minutes']))}분" if p.get("voice_minutes") == p.get("voice_minutes") and p.get("voice_minutes") is not None else "확인 필요")
    sms = "무제한" if p.get("sms_unlimited") else (f"{int(float(p['sms_count']))}건" if p.get("sms_count") == p.get("sms_count") and p.get("sms_count") is not None else "확인 필요")
    period = p.get("discount_period_months")
    period_s = f"{int(float(period))}개월 후 정가" if period == period and period not in (None, "") else "할인 기간 없음/평생"
    bills = won(p.get("discounted_fee")) if p.get("billing_price_known", True) else f"{won(p.get('discounted_fee'))} (청구액 미확인·페이백 표시가일 수 있음)"
    benefits = ", ".join(str(b) for b in (p.get("included_benefits") or [])[:8]) or "없음"
    ott = str(p.get("ott_options") or "").strip()
    brand = p.get("mvno_brand") if p.get("carrier_type") == "MVNO" else p.get("carrier")
    lines = [
        f"{p.get('plan_name')} | {'알뜰폰' if p.get('carrier_type') == 'MVNO' else '통신 3사'} · {brand} · {p.get('host_mno')}망 · {p.get('network_gen') or ''}",
        f"월 청구액 {bills} · 정가 {won(p.get('monthly_fee'))} · {period_s} · 12개월 평균 {won(_switching_monthly_fee(p))}",
        f"데이터 {data} · 소진 후 {qos} · 통화 {voice} · 문자 {sms}",
        f"혜택: {benefits}" + (f" · OTT: {ott}" if ott and ott != "nan" else ""),
    ]
    extra = []
    if p.get("age_condition") and p.get("age_condition") == p.get("age_condition"): extra.append(f"가입 조건: {p['age_condition']}")
    if p.get("payback_schedule"): extra.append(f"페이백(청구액과 별도 지급): {p['payback_schedule']}")
    if extra: lines.append(" · ".join(extra))
    return "\n   ".join(lines)

mapping, blocks = {}, []
for qid, q in key.items():
    p = profiles.get(qid) or {}
    ids = []
    # 풀을 만들 때 top5_ids 는 공백 구분이었다. 지금 csv 는 | 구분이라, 공백으로 깨진 통신 3사 ID 와 뒤에 바뀐 Top-5 는 addendum_pool.py 가 덧붙였다
    for s in res.loc[res.qid == qid, "top5_ids"].fillna(""):
        for i in s.split():
            if i in P and i not in ids: ids.append(i)
    rng = random.Random(f"pool-{qid}")
    feas = [str(c["plan_id"]) for c in filter_candidates({**p, "include_mno": True}) if str(c["plan_id"]) not in ids]
    ids += rng.sample(feas, min(N_RANDOM, len(feas)))
    rng.shuffle(ids)
    codes = {f"C{k + 1:02d}": pid for k, pid in enumerate(ids)}
    mapping[qid] = codes
    ref = ""
    name = p.get("reference_plan_name")
    if name:
        hits = find_plans_by_name(name)
        if hits:
            ref = "\n질문 속 현재 요금제(카탈로그에서 찾은 것):\n   " + card(P.get(str(hits[0]["plan_id"]), hits[0]))
        else:
            ref = f"\n질문 속 현재 요금제 '{name}'는 카탈로그에서 찾지 못함. 질문 문장만 보고 판단."
    body = "\n".join(f"- {c}: {card(P[pid])}" for c, pid in codes.items())
    blocks.append((qid, f"## {qid}\n질문: {q['question']}{ref}\n후보 {len(codes)}개:\n{body}\n"))

(HERE / "pool_mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
bd = HERE / "채점자용_문항"; bd.mkdir(exist_ok=True)
for f in bd.glob("*.md"): f.unlink()
for b in range(0, len(blocks), PER_BATCH):
    part = blocks[b:b + PER_BATCH]
    (bd / f"batch_{b // PER_BATCH + 1:02d}.md").write_text("\n".join(t for _, t in part), encoding="utf-8")
n = [len(v) for v in mapping.values()]
print(f"문항 {len(n)} · 후보 합계 {sum(n)} · 문항당 평균 {sum(n)/len(n):.1f} (최소 {min(n)}, 최대 {max(n)}) · 배치 {len(list(bd.glob('*.md')))}개")
