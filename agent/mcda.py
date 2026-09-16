"""SMAA-2 기반 다기준 요금제 랭킹.

가중치 표본은 더 이상 무작위(감마분포)로 지어내지 않는다. `weight_bootstrap.json`에
실제 알뜰폰 가입자 데이터의 Ridge 표준화 계수를 부트스트랩 300회 돌려 뽑은
가중치 300세트가 들어있다. 이 값은 시장 중요도의 사전분포로 사용하고, 각 후보의
Feature 값은 사용자 요구 적합도(0~1)로 계산한다. 우선순위/비교 목표가 있으면 각
가중치 표본에 가산 후 재정규화한다.

문자(sms)는 축에서 뺐다. 통화 무제한과 98.1% 겹치는 묶음 상품이라 같이 넣으면 두
계수가 서로 상쇄하며 부호가 뒤집힌다(부트스트랩 상관 -0.895). 통화 축이 이미 재고
있으니 회귀 전에 뺀다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math


CRITERIA = ("price", "data", "qos", "benefit", "voice", "tethering")

# 우선순위 1단계당/비교 목표당 얼마나 가산할지. 예전 감마분포 버전이 균등 alpha=1.0
# 기준(합계 len(CRITERIA))에 +10.0/+12.0을 더하던 것과 같은 비율로, 합계가 1인
# 실측 가중치 벡터에 맞게 축소했다.
_PRIORITY_UNIT = 10.0 / len(CRITERIA)
_GOAL_UNIT = 12.0 / len(CRITERIA)

_WEIGHT_DATA_PATH = Path(__file__).resolve().parent / "weight_bootstrap.json"
_PRICE_HORIZON_MONTHS = 12
_DATA_OVERSUPPLY_FLOOR = 0.8


def _load_base_weights() -> list[list[float]]:
    payload = json.loads(_WEIGHT_DATA_PATH.read_text(encoding="utf-8"))
    if tuple(payload["criteria"]) != CRITERIA:
        raise ValueError(
            f"{_WEIGHT_DATA_PATH.name}의 criteria 순서가 CRITERIA와 다르다: "
            f"{payload['criteria']} != {list(CRITERIA)}"
        )
    return payload["vectors"]


_BASE_WEIGHTS = _load_base_weights()


@dataclass(frozen=True)
class MCDAResult:
    plan_id: str
    smaa2_first_rank_acceptability: float
    smaa2_expected_rank: float
    smaa2_score: int
    favorable_weights: tuple[float, ...]


def _minmax(values: list[float], *, cost: bool = False) -> list[float]:
    low, high = min(values), max(values)
    if math.isclose(low, high):
        return [0.5] * len(values)
    normalized = [(value - low) / (high - low) for value in values]
    return [1.0 - value for value in normalized] if cost else normalized


def _finite_data(plan: dict, finite_max: float) -> float:
    return finite_max * 1.25 if plan.get("data_unlimited") else float(plan.get("data_gb") or 0)


def _profile_value(profile: object | dict | None, field: str):
    if profile is None:
        return None
    if isinstance(profile, dict):
        return profile.get(field)
    return getattr(profile, field, None)


def _effective_monthly_fee(plan: dict, months: int = _PRICE_HORIZON_MONTHS) -> float:
    """프로모션 종료 후 정상가까지 포함한 비교기간 평균 월 납부액."""
    discounted_raw = plan.get("discounted_fee")
    regular_raw = plan.get("monthly_fee")
    discounted = float(regular_raw or 0) if discounted_raw is None else float(discounted_raw)
    regular = float(regular_raw) if regular_raw is not None else discounted
    period = plan.get("discount_period_months")
    if period is None:
        return discounted
    promo_months = max(0, min(int(period), months))
    return (discounted * promo_months + regular * (months - promo_months)) / months


def _minimum_fit(value: float, target: float | None) -> float:
    if target is None or target <= 0:
        return 0.5
    return max(0.0, min(1.0, value / target))


def _target_data_fit(value: float, target: float) -> float:
    """목표량에서 1.0, 2배 이상에서는 0.8인 부족/과잉 비대칭 적합도."""
    if target <= 0:
        return 0.5
    ratio = max(0.0, value) / target
    if ratio <= 1.0:
        return ratio
    if ratio >= 2.0:
        return _DATA_OVERSUPPLY_FLOOR
    return 1.0 - (1.0 - _DATA_OVERSUPPLY_FLOOR) * (ratio - 1.0)


def _benefit_fit(plan: dict, profile: object | dict | None) -> float:
    wanted_names = list(_profile_value(profile, "wanted_benefits") or [])
    wanted_categories = list(_profile_value(profile, "wanted_benefit_categories") or [])
    requested = len(wanted_names) + len(wanted_categories)
    if not requested:
        return 0.5

    searchable = " | ".join(
        [
            str(plan.get("ott_options") or ""),
            *(str(value) for value in plan.get("included_benefits") or []),
            *(str(value) for value in plan.get("benefit_details") or []),
        ]
    ).casefold()
    categories = {str(value).casefold() for value in plan.get("benefit_categories") or []}
    matched_names = sum(str(value).casefold() in searchable for value in wanted_names)
    matched_categories = sum(str(value).casefold() in categories for value in wanted_categories)
    return (matched_names + matched_categories) / requested


def _utility_rows(
    candidates: list[dict], profile: object | dict | None = None
) -> list[list[float]]:
    data_max = max((float(p.get("data_gb") or 0) for p in candidates), default=1.0) or 1.0

    fees = [_effective_monthly_fee(p) for p in candidates]
    budget_max = _profile_value(profile, "budget_max_won")
    if budget_max is not None and float(budget_max) > 0:
        price_utility = [
            max(0.0, min(1.0, math.exp(-math.log(2.0) * fee / float(budget_max))))
            for fee in fees
        ]
    else:
        # 예산이 없을 때도 단기 프로모션에 끌리지 않도록 12개월 평균요금을 사용한다.
        price_utility = _minmax([math.sqrt(max(0.0, fee)) for fee in fees], cost=True)

    explicit_min_data = _profile_value(profile, "min_data_gb")
    target_data = _profile_value(profile, "target_data_gb")
    if target_data is None:
        target_data = _profile_value(profile, "estimated_monthly_data_gb")

    if _profile_value(profile, "data_unlimited") is True:
        data_utility = [1.0 if p.get("data_unlimited") else 0.0 for p in candidates]
    elif explicit_min_data is not None:
        data_utility = [
            1.0
            if p.get("data_unlimited")
            else _minimum_fit(float(p.get("data_gb") or 0), float(explicit_min_data))
            for p in candidates
        ]
    elif target_data is not None and float(target_data) > 0:
        data_utility = [
            _DATA_OVERSUPPLY_FLOOR
            if p.get("data_unlimited")
            else _target_data_fit(float(p.get("data_gb") or 0), float(target_data))
            for p in candidates
        ]
    else:
        data_utility = _minmax([math.log1p(_finite_data(p, data_max)) for p in candidates])

    if _profile_value(profile, "voice_unlimited") is True:
        voice_utility = [1.0 if p.get("voice_unlimited") else 0.0 for p in candidates]
    elif _profile_value(profile, "min_voice_minutes") is not None:
        target_voice = float(_profile_value(profile, "min_voice_minutes"))
        voice_utility = [
            1.0
            if p.get("voice_unlimited")
            else _minimum_fit(float(p.get("voice_minutes") or 0), target_voice)
            for p in candidates
        ]
    else:
        # 요구량이 없으면 통화량이 많다는 이유만으로 순위를 바꾸지 않는다.
        voice_utility = [0.5] * len(candidates)

    columns = {
        "price": price_utility,
        "data": data_utility,
        "qos": _minmax([float(p.get("qos_mbps") or 0) for p in candidates]),
        "benefit": [_benefit_fit(p, profile) for p in candidates],
        "voice": voice_utility,
        "tethering": _minmax([float(p.get("tethering_gb") or 0) for p in candidates]),
    }
    return [[columns[name][i] for name in CRITERIA] for i in range(len(candidates))]


def _boosted(vector: list[float], priorities: list[str], comparison_goals: list[str]) -> list[float]:
    """실측 가중치 하나에 사용자 우선순위/비교 목표를 가산하고 재정규화한다."""
    boosted = list(vector)
    priority_position = {name: i for i, name in enumerate(priorities) if name in CRITERIA}
    for name, position in priority_position.items():
        extra = max(0, len(priorities) - position) * _PRIORITY_UNIT
        boosted[CRITERIA.index(name)] += extra

    goal_criteria = {
        "cheaper": "price",
        "more_data": "data",
        "faster_qos": "qos",
    }
    for goal in comparison_goals:
        criterion = goal_criteria.get(goal)
        if criterion:
            boosted[CRITERIA.index(criterion)] += _GOAL_UNIT

    total = sum(boosted)
    return [value / total for value in boosted]


def _weight_samples(priorities: list[str], comparison_goals: list[str]) -> list[list[float]]:
    if not priorities and not comparison_goals:
        return [list(vector) for vector in _BASE_WEIGHTS]
    return [_boosted(vector, priorities, comparison_goals) for vector in _BASE_WEIGHTS]


def evaluate_mcda(
    candidates: list[dict],
    priorities: list[str] | None = None,
    *,
    comparison_goals: list[str] | None = None,
    profile: object | dict | None = None,
) -> list[MCDAResult]:
    """가중치 표본(실측 부트스트랩 300세트)별 순위를 집계해 SMAA-2 지표를 계산한다."""
    if not candidates:
        return []
    plan_ids = [str(candidate["plan_id"]) for candidate in candidates]
    if len(plan_ids) != len(set(plan_ids)):
        raise ValueError("MCDA 후보의 plan_id는 서로 달라야 합니다.")
    utilities = _utility_rows(candidates, profile)
    weights = _weight_samples(priorities or [], comparison_goals or [])
    samples = len(weights)
    n = len(candidates)
    rank_counts = [[0] * n for _ in candidates]
    favorable_weight_sums = [[0.0] * len(CRITERIA) for _ in candidates]
    favorable_counts = [0] * n

    for weight in weights:
        totals = [sum(w * u for w, u in zip(weight, row)) for row in utilities]
        order = sorted(range(n), key=lambda i: (-totals[i], str(candidates[i]["plan_id"])))
        for rank, index in enumerate(order):
            rank_counts[index][rank] += 1
            if rank < min(3, n):
                favorable_counts[index] += 1
                for criterion_index, value in enumerate(weight):
                    favorable_weight_sums[index][criterion_index] += value

    expected_ranks = [
        sum((rank + 1) * count for rank, count in enumerate(row)) / samples
        for row in rank_counts
    ]
    return [
        MCDAResult(
            plan_id=plan_ids[i],
            smaa2_first_rank_acceptability=rank_counts[i][0] / samples,
            smaa2_expected_rank=expected_ranks[i],
            smaa2_score=max(
                0,
                min(100, round(100 * (1.0 - (expected_ranks[i] - 1) / max(1, n - 1)))),
            ),
            favorable_weights=tuple(
                value / favorable_counts[i] if favorable_counts[i] else 0.0
                for value in favorable_weight_sums[i]
            ),
        )
        for i, candidate in enumerate(candidates)
    ]


def rank_smaa2(results: list[MCDAResult]) -> list[MCDAResult]:
    """기대순위가 낮고 1위 수용도가 높은 후보부터 정렬한다."""
    return sorted(
        results,
        key=lambda result: (
            result.smaa2_expected_rank,
            -result.smaa2_first_rank_acceptability,
            result.plan_id,
        ),
    )


if __name__ == "__main__":
    sample = [
        {"plan_id": "cheap", "discounted_fee": 20_000, "data_gb": 10, "voice_minutes": 100},
        {"plan_id": "large", "discounted_fee": 40_000, "data_gb": 100, "voice_minutes": 300},
    ]
    first = evaluate_mcda(sample, ["price"])
    second = evaluate_mcda(sample, ["price"])
    assert first == second, "같은 입력이면 결과가 재현되어야 한다(실측 데이터라 난수 없음)"
    assert rank_smaa2(first)[0].plan_id == "cheap", "가격 우선순위가 반영되어야 한다"

    baseline = evaluate_mcda(sample)  # 우선순위 없음 -> 실측 300세트 그대로
    assert len(baseline[0].favorable_weights) == len(CRITERIA)

    fit_sample = [
        {"plan_id": "target", "discounted_fee": 30_000, "monthly_fee": 30_000, "data_gb": 100,
         "voice_minutes": 300, "included_benefits": ["넷플릭스"]},
        {"plan_id": "over", "discounted_fee": 30_000, "monthly_fee": 30_000, "data_gb": 150,
         "voice_minutes": 500, "included_benefits": []},
        {"plan_id": "unlimited", "discounted_fee": 30_000, "monthly_fee": 30_000,
         "data_unlimited": True, "voice_unlimited": True, "included_benefits": ["넷플릭스"]},
    ]
    rows = _utility_rows(
        fit_sample,
        {"target_data_gb": 100, "min_voice_minutes": 300, "wanted_benefits": ["넷플릭스"]},
    )
    data_index, benefit_index, voice_index = (
        CRITERIA.index("data"), CRITERIA.index("benefit"), CRITERIA.index("voice")
    )
    assert [round(row[data_index], 2) for row in rows] == [1.0, 0.9, 0.8]
    assert [row[benefit_index] for row in rows] == [1.0, 0.0, 1.0]
    assert [row[voice_index] for row in rows] == [1.0, 1.0, 1.0]
    print("self-check ok: SMAA-2 ranking (bootstrap weights)")
