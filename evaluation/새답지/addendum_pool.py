# -*- coding: utf-8 -*-
"""수정된 Top-5 목록에서 기존 후보 풀에 없던 (문항, 요금제)만 새 코드로 덧붙이고, 채점자용 추가 배치를 만든다."""
import json
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
src = (HERE / "build_pool.py").read_text(encoding="utf-8")
head = src[:src.index("mapping, blocks = {}, []")]   # card(), P, key, profiles 등 정의만 가져온다
exec(compile(head, "build_pool_head", "exec"))
# pool_mapping_v1.json = 덧붙이기 전 build_pool.py 결과. 이 스크립트가 pool_mapping.json(최종본)을 덮어썼고 v1 은 남아 있지 않다
mapping = json.loads((HERE / "pool_mapping_v1.json").read_text(encoding="utf-8"))
res = pd.read_csv(HERE / "answer_key_results_baseline.csv", dtype={"top5_ids": str})
blocks, added = [], 0
for qid, codes in mapping.items():
    have = set(codes.values()); new = []
    for s in res.loc[res.qid == qid, "top5_ids"].fillna(""):
        for i in s.split("|"):
            if i in P and i not in have and i not in new: new.append(i)
    if not new: continue
    n0 = len(codes)
    for k, pid in enumerate(new):
        codes[f"C{n0 + k + 1:02d}"] = pid
    added += len(new)
    targets = [c for c, pid in codes.items() if pid in new]
    p = profiles.get(qid) or {}
    ref = ""
    name = p.get("reference_plan_name")
    if name:
        hits = find_plans_by_name(name)
        ref = ("\n질문 속 현재 요금제(카탈로그에서 찾은 것):\n   " + card(P.get(str(hits[0]["plan_id"]), hits[0]))) if hits else f"\n질문 속 현재 요금제 '{name}'는 카탈로그에서 찾지 못함."
    body = "\n".join(f"- {c}: {card(P[pid])}" for c, pid in codes.items())
    blocks.append(f"## {qid}\n질문: {key[qid]['question']}{ref}\n**이번에 채점할 후보: {', '.join(targets)}** (나머지는 이미 채점됨. '더 나은 대안' 비교에만 쓴다)\n후보 {len(codes)}개:\n{body}\n")
(HERE / "pool_mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
(HERE / "채점자용_문항" / "addendum.md").write_text("\n".join(blocks), encoding="utf-8")
print(f"추가 후보 {added}개 · 문항 {len(blocks)}개")
