# -*- coding: utf-8 -*-
"""추천 방법 5종을 **답지가 필요 없는 지표**로 같은 질의에 올려 비교한다.

    python run_metric_table.py

왜 답지를 안 쓰나. 공식으로 만든 답지는 결국 또 하나의 추천 로직이라, 그걸로 추천기를
재면 "내 공식이 내 공식과 얼마나 같은가"를 잴 뿐이다(순환). 합성 가입이력의 Hit@5 도
못 쓴다 - 요금제에서 사용자를 역산해 만들어서, 정답 요금제가 예산 이하 후보 중 가격
백분위 **중위 99.5%** 다. 그 지표를 올린다는 건 "예산을 최대한 다 쓰게 하는 추천기"를
만든다는 뜻이 된다.

아래 세 지표는 정답이 필요 없다. 출력이 반드시 지켜야 할 성질을 어기는지만 본다:

    파레토 비지배율   추천한 요금제보다 **모든 축에서 나은 것**이 카탈로그에 있었나.
                      있으면 어떤 취향이든 틀린 추천이다. 답지가 필요 없는 이유.
    가격 백분위       같은 조건을 만족하는 후보 중 가격 하위 몇 %를 골랐나. 낮을수록 좋다.
                      파레토가 만점이어도 프론티어의 최고가만 고르면 이 값이 100% 가 된다.
    예산 단조성 위반  필요 조건을 고정하고 **예산만** 올렸을 때 추천 요금이 오른 횟수.
                      예산은 상한이지 목표액이 아니다. 파레토는 입력을 안 보므로 이걸 못 잡는다.

세 지표는 서로 다른 층을 본다. 파레토는 "프론티어 밖인가", 백분위는 "프론티어 안 어디인가",
단조성은 "입력에 옳게 반응하는가".

**각 방법은 자기 필터를 그대로 쓴다**(구현 비교). 세그먼트·KNN 은 필터가 아예 없고,
v1 코사인은 용량만, v2 는 무제한+예산, MCDA 는 `filter_candidates` 전체다. 그래서 축
개수도 다르다 - 표에 함께 적는다. 파레토는 필터와 무관하게 **전체 카탈로그** 기준으로
재므로, 필터가 더 좋은 상품을 걸러낸 경우도 잡힌다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from agent.cosine_recommendation import prepare_plan_catalog, recommend_by_cosine, recommend_by_coverage
from agent.data import all_plans, filter_candidates
from agent.mcda import evaluate_mcda, rank_smaa2
from agent.segmentation import ROOT, fit_model, holdout_100, load_interactions

OUTPUT = ROOT / "outputs" / "지표표_방법별비교.json"

# (필요 데이터 GB, 예산). 예산 단조성은 같은 need 안에서 예산을 올려가며 잰다.
NEEDS = (5.0, 20.0, 50.0, 100.0)
BUDGETS = (10_000, 15_000, 20_000, 30_000, 40_000, 60_000)

# 파레토 지배를 판정할 축. 전부 "클수록 좋다"로 방향을 맞춰 둔다(요금은 부호를 뒤집는다).
PARETO_AXES = ("fee", "data", "voice", "sms", "ott", "qos", "tether")
UNLIMITED_GB = 1_000.0  # 무제한을 수치 축에 올릴 때 쓰는 상한. 유한 최대(약 300GB)보다 크면 된다


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if out != out else out


def build_pool() -> pd.DataFrame:
    """비교 기준이 되는 전체 알뜰폰 카탈로그. 파레토·백분위를 전부 여기서 잰다."""
    pool = pd.DataFrame(all_plans())
    pool = pool[pool["carrier_type"].eq("MVNO") & pool["billing_price_known"].astype(bool)].copy()
    pool["plan_id"] = pool["plan_id"].astype(str).str.strip()
    pool = pool.drop_duplicates("plan_id").reset_index(drop=True)

    unlimited = pool["effective_unlimited"].astype(bool)
    pool["fee"] = [_num(v) for v in pool["discounted_fee"]]
    pool["data"] = np.where(unlimited, UNLIMITED_GB, [_num(v) for v in pool["data_gb"]])
    pool["voice"] = np.where(pool["voice_unlimited"].astype(bool), 1_000.0,
                             [_num(v) for v in pool["voice_minutes"]])
    pool["sms"] = np.where(pool["sms_unlimited"].astype(bool), 500.0,
                           [_num(v) for v in pool["sms_count"]])
    pool["ott"] = pool["ott_options"].fillna("").astype(str).str.strip().ne("").astype(float)
    pool["qos"] = [_num(v) for v in pool["qos_mbps"]]
    pool["tether"] = [_num(v) for v in pool["tethering_gb"]]
    return pool


def pareto_nondominated(pool: pd.DataFrame) -> dict[str, bool]:
    """다른 요금제에 완전히 지배당하지 않는 요금제. 요금은 낮을수록 좋으니 부호를 뒤집는다."""
    matrix = np.column_stack([-pool["fee"].to_numpy()] + [pool[a].to_numpy() for a in PARETO_AXES[1:]])
    flags = {}
    for i in range(len(matrix)):
        dominated = (np.all(matrix >= matrix[i], axis=1) & np.any(matrix > matrix[i], axis=1)).any()
        flags[pool["plan_id"].iat[i]] = not bool(dominated)
    return flags


def feasible(pool: pd.DataFrame, need: float, budget: int) -> pd.DataFrame:
    """가격 백분위의 기준이 되는 후보군. 방법과 무관하게 질의 조건만 건다."""
    return pool[(pool["data"] >= need) & (pool["fee"] <= budget)]


def query_frame(need: float, budget: int) -> pd.DataFrame:
    """세그먼트·KNN·코사인이 읽는 합성데이터 스키마 한 행."""
    return pd.DataFrame([{
        "customer_id": "Q", "age": 40, "gender": "여",
        "data_gb_month": need, "data_unlimited_need": False,
        "voice_unlimited_need": False, "sms_unlimited_need": False,
        "voice_minutes_need": 0.0, "sms_count_need": 0.0,
        "carrier_type": "MVNO", "current_carrier": "알뜰폰", "mvno_ok": True,
        "current_fee_krw": budget, "budget_krw": budget,
        "ott_want": "", "ott_required": False, "fee_group": "중",
    }])


def _ids(ranked: list[dict]) -> list[str]:
    return [str(item["plan_id"]) for item in ranked]


def build_methods(model, catalog: pd.DataFrame) -> dict[str, tuple[str, int, Callable]]:
    """이름 -> (쓰는 축 설명, 축 개수, 질의 -> Top-5 plan_id)."""
    from agent.segmentation import neighbor_recommend, segment_popularity_recommend

    def seg(need, budget):
        return _ids(segment_popularity_recommend(model, query_frame(need, budget), 5)["ranked"])

    def knn(need, budget):
        return _ids(neighbor_recommend(model, query_frame(need, budget), 5)["ranked"])

    def cos(need, budget):
        row = query_frame(need, budget).iloc[0]
        return _ids(recommend_by_cosine(catalog, row, 5)["ranked"])

    def cov(need, budget):
        row = query_frame(need, budget).iloc[0]
        return _ids(recommend_by_coverage(catalog, row, 5)["ranked"])

    def mcda(need, budget):
        profile = {"budget_max_won": budget, "min_data_gb": need}
        candidates = filter_candidates(profile)
        candidates = [c for c in candidates if c.get("carrier_type") == "MVNO"]
        if not candidates:
            return []
        ranked = rank_smaa2(evaluate_mcda(candidates, profile=profile))[:5]
        return [str(r.plan_id) for r in ranked]

    return {
        "① 세그먼트 인기": ("사용자 프로필 10축 · 필터 없음", 10, seg),
        "① 이웃 KNN": ("사용자 프로필 10축 · 필터 없음", 10, knn),
        "② v1 코사인": ("공통 8축 · 용량 필터", 8, cos),
        "② v2 비대칭 충족도": ("공통 8축 · 무제한+예산 필터", 8, cov),
        "③ SMAA-2 + Ridge": ("요금제 6축 · 전체 하드 필터", 6, mcda),
    }


def measure(methods, pool: pd.DataFrame, nondominated: dict[str, bool]) -> dict[str, dict]:
    fee = dict(zip(pool["plan_id"], pool["fee"]))
    supply = dict(zip(pool["plan_id"], pool["data"]))
    rows: dict[str, dict] = {}
    for name, (axes_label, axis_count, fn) in methods.items():
        pareto, pct, meets, violations, steps, empty = [], [], [], 0, 0, 0
        for need in NEEDS:
            previous = None
            for budget in BUDGETS:
                picks = fn(need, budget)
                if not picks:
                    empty += 1
                    continue
                # 하드 조건 충족률. 성능이 아니라 "이 방법이 무엇을 보장하는가"를 읽는 칸이다.
                # 이게 낮으면 다른 지표가 좋아도 조건을 어겨서 얻은 점수일 수 있다.
                meets.extend(supply.get(p, 0.0) >= need and fee.get(p, 0.0) <= budget for p in picks)
                pareto.extend(nondominated.get(p, False) for p in picks)
                base = feasible(pool, need, budget)["fee"].to_numpy()
                if len(base) > 1:
                    pct.extend(100.0 * (base < fee.get(p, 0.0)).sum() / (len(base) - 1) for p in picks)
                top = fee.get(picks[0], 0.0)
                if previous is not None:
                    steps += 1
                    violations += top > previous + 1e-9
                previous = top
        rows[name] = {
            "축": axes_label,
            "축 개수": axis_count,
            "하드 조건 충족률": round(100 * float(np.mean(meets)), 1) if meets else None,
            "파레토 비지배율": round(100 * float(np.mean(pareto)), 1) if pareto else None,
            "가격 백분위 평균": round(float(np.mean(pct)), 1) if pct else None,
            "예산 단조성 위반": f"{violations}/{steps}",
            "추천 없음": empty,
        }
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=OUTPUT)
    args = ap.parse_args()

    pool = build_pool()
    nondominated = pareto_nondominated(pool)
    train, _ = holdout_100(load_interactions(), seed=args.seed, size=100)
    methods = build_methods(fit_model(train), prepare_plan_catalog())
    rows = measure(methods, pool, nondominated)

    width = max(len(name) for name in rows)
    print(f"카탈로그 {len(pool)}종 · 파레토 프론티어 {sum(nondominated.values())}종 "
          f"({100 * sum(nondominated.values()) / len(pool):.1f}%)")
    print(f"질의 {len(NEEDS)}종 x 예산 {len(BUDGETS)}단계 = {len(NEEDS) * len(BUDGETS)}건\n")
    print(f"{'방법':<{width}} {'축':>3} {'조건충족':>8} {'파레토':>8} {'백분위':>8} {'단조성위반':>10}  쓰는 축")
    for name, row in rows.items():
        print(f"{name:<{width}} {row['축 개수']:>3} {row['하드 조건 충족률']:>7}% "
              f"{row['파레토 비지배율']:>7}% {row['가격 백분위 평균']:>7}% "
              f"{row['예산 단조성 위반']:>10}  {row['축']}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(
        {"catalog_plans": len(pool), "pareto_frontier": sum(nondominated.values()),
         "needs_gb": list(NEEDS), "budgets_won": list(BUDGETS), "methods": rows},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {args.out}")


if __name__ == "__main__":
    main()
