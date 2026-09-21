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

# 우선순위 1단계당/비교 목표당 얼마나 가산할지. 가중치 벡터의 합이 1.0 이라 단위도 그 눈금이다:
# 우선순위 1개 -> 해당 축 약 0.53, 3개 -> 약 0.6/0.3/0.2 비율. 이보다 크면 한 축이 결정을 독점한다.
_PRIORITY_UNIT = 0.10
_GOAL_UNIT = 0.12
# 한 축이 결정을 독점하지 못하게 하는 상한. SMAA-2 는 가중치 불확실성을 탐색하는 방법인데
# 한 축이 이 이상을 먹으면 나머지 표본이 의미를 잃는다.
_MAX_BOOSTED_SHARE = 0.60

_WEIGHT_DATA_PATH = Path(__file__).resolve().parent / "weight_bootstrap.json"

# 비용 비교 기간. 화면의 총비용과 혜택 월 환산이 모두 이 값 하나를 쓴다.
# 기준이 둘이면 같은 상품의 "총비용"이 화면마다 달라 보인다.
COMPARE_MONTHS = 12

# 순위의 가격 축은 "지금 내는 월 요금"(discounted_fee)이다. 1 이면 _effective_monthly_fee 가
# 할인가를 그대로 돌려준다. 12개월 평균으로 바꿔도 Top-5 가 같아서(질의 7종), 카드에 보이는
# 금액과 순위가 쓰는 금액을 맞췄다. 할인 종료 후 정가는 순위가 아니라 근거·유의사항에서
# 금액과 시점으로 밝힌다(priceRisesAfter / priceRisesLater / monthly_fee_schedule).
_PRICE_HORIZON_MONTHS = 1
_DATA_OVERSUPPLY_FLOOR = 0.8

# '무제한' 요청에 대한 등급별 충족도. agent.data.data_tier 와 짝이다.
# 완전 무제한이 1.0 이지만 소진 후 HD 가 되는 QoS형과의 차이는 크지 않다 — 실제 체감은
# 오히려 후자가 빠른 경우가 많다.
_UNLIMITED_TIER_FIT = {
    "unlimited_full": 1.0,
    "qos_hd": 0.95,
    "qos_sd": 0.80,
    "qos_lite": 0.60,
    "qos_text": 0.0,
    "capped": 0.0,
}


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
    # 축별 효용(0~1). SMAA-2 계산에는 쓰지 않고 "왜 이 순위인지"를 화면에 보여주는 용도다.
    # 후보가 2천 건이면 smaa2_score 는 상위 3개가 전부 100 으로 포화해 변별력이 없다.
    utilities: tuple[float, ...] = ()
    # 300개 가중치 시나리오에서 sum(weight*utility)의 평균을 0~100으로 환산한 값.
    # "현재 조건에 대한 다기준 적합도"이지 만족 확률·가입 성공 확률·절대 품질 점수가 아니다.
    recommendation_fit: float = 0.0
    # 300개 가중치 시나리오 중 상위 3위 안에 든 비율. 화면에서는 "상위권 안정성"으로 부른다.
    top3_acceptability: float = 0.0


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
    """순위가 쓰는 월 납부액. 기본값(months=1)이면 지금 내는 할인가 그대로다.

    months 를 늘리면 할인이 끝난 뒤의 정가까지 섞은 그 기간 평균이 된다. 총비용
    계산(backend.plans.total_cost)은 그쪽을 COMPARE_MONTHS 로 따로 쓴다.
    """
    discounted_raw = plan.get("discounted_fee")
    regular_raw = plan.get("monthly_fee")
    discounted = float(regular_raw or 0) if discounted_raw is None else float(discounted_raw)
    regular = float(regular_raw) if regular_raw is not None else discounted
    period = plan.get("discount_period_months")
    if period is None:
        return discounted
    promo_months = max(0, min(int(period), months))
    return (discounted * promo_months + regular * (months - promo_months)) / months


def _switching_monthly_fee(plan: dict) -> float:
    """지금 쓰는 요금제와 "갈아탈 가치가 있나"를 비교할 때 쓰는 월 납부액.

    순위(_effective_monthly_fee, 할인가)와 일부러 다르다. 갈아타기는 되돌리기 어려운
    결정이라 초기 할인가로 판단하면 안 된다 - 1개월 1,000원 뒤 50,000원이 되는 상품이
    20,000원 쓰는 사람에게 "더 싸다"로 나온다. 여기서는 정가 복귀까지 합산한다.
    """
    return _effective_monthly_fee(plan, COMPARE_MONTHS)


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


# 기본 제공량이 예상 사용량을 못 채워도 QoS 로 계속 쓸 수는 있다. 그래서 0 으로 떨어뜨리지
# 않고 이 값까지만 깎는다.
_COVERAGE_FLOOR = 0.6


def _full_speed_coverage(plan: dict, target_gb: float | None) -> float:
    """예상 사용량 중 몇 할을 '전속'으로 쓸 수 있는지. 목표가 없으면 등급만 본다."""
    if target_gb is None or float(target_gb) <= 0 or plan.get("data_unlimited"):
        return 1.0
    covered = min(1.0, float(plan.get("data_gb") or 0) / float(target_gb))
    return _COVERAGE_FLOOR + (1.0 - _COVERAGE_FLOOR) * covered


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
        # '무제한'은 0/1 이 아니다. 완전 무제한의 소진 후 속도는 중앙 0.1Mbps 인데
        # QoS형 100GB+5Mbps 는 3~10Mbps 로 오히려 빠르다. 등급으로 점수를 매긴다.
        #
        # 등급만 보면 '4.5GB + 소진 후 1Mbps'가 '120GB + 소진 후 5Mbps'와 같은 취급을
        # 받는다. 예상 사용량을 알면 전속으로 얼마나 버티는지를 함께 본다.
        data_utility = [
            _UNLIMITED_TIER_FIT.get(p.get("data_tier"), 0.0)
            * _full_speed_coverage(p, target_data)
            for p in candidates
        ]
    elif explicit_min_data is not None:
        data_utility = [
            1.0
            if p.get("data_unlimited")
            else _minimum_fit(float(p.get("data_gb") or 0), float(explicit_min_data))
            for p in candidates
        ]
        # 필수 최소량("20GB 이상")은 필터가 이미 걸러서, 남은 후보는 이 축에서 전부 1.0 이
        # 된다. 상수 축은 _discriminating 이 빼 버리므로 "데이터를 가장 중요하게"라고 말해도
        # 순위가 하나도 안 바뀐다(실측). 사용자가 데이터를 우선하겠다고 직접 말했을 때만
        # 충족 여부 대신 실제 제공량으로 갈라 준다 - 최소량 판정 자체는 그대로다.
        if "data" in (_profile_value(profile, "priorities") or []) and (
            max(data_utility) - min(data_utility) <= 1e-9
        ):
            data_utility = _minmax([math.log1p(_finite_data(p, data_max)) for p in candidates])
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

    requested_benefits = bool(_profile_value(profile, "wanted_benefits")) or bool(
        _profile_value(profile, "wanted_benefit_categories")
    )
    # 혜택을 요청하지 않았으면 혜택 축은 순위를 바꾸지 않는다(통화 축과 같은 규칙).
    # 수집된 혜택 금액은 사용자가 그 서비스를 실제로 쓰고 지급 조건을 맞춰야 절약액이 되므로,
    # 요청이 없을 때 순위 근거로 쓸 수 없다. 상수 축은 _discriminating 이 걸러 낸다.
    benefit_utility = (
        [_benefit_fit(p, profile) for p in candidates]
        if requested_benefits
        else [0.5] * len(candidates)
    )

    columns = {
        "price": price_utility,
        "data": data_utility,
        "qos": _minmax([float(p.get("qos_mbps") or 0) for p in candidates]),
        "benefit": benefit_utility,
        "voice": voice_utility,
        "tethering": _minmax([float(p.get("tethering_gb") or 0) for p in candidates]),
    }
    return [[columns[name][i] for name in CRITERIA] for i in range(len(candidates))]


_GOAL_CRITERIA = {"cheaper": "price", "more_data": "data", "faster_qos": "qos"}


def _boosted(vector: list[float], priorities: list[str], comparison_goals: list[str]) -> list[float]:
    """실측 가중치 하나에 사용자 우선순위/비교 목표를 가산하고 재정규화한다."""
    boosted = list(vector)
    priority_position = {name: i for i, name in enumerate(priorities) if name in CRITERIA}
    for name, position in priority_position.items():
        extra = max(0, len(priorities) - position) * _PRIORITY_UNIT
        boosted[CRITERIA.index(name)] += extra

    for goal in comparison_goals:
        criterion = _GOAL_CRITERIA.get(goal)
        if criterion:
            boosted[CRITERIA.index(criterion)] += _GOAL_UNIT

    total = sum(boosted)
    normalized = [value / total for value in boosted]

    # 상한을 넘은 축은 초과분을 나머지 축에 원래 비율대로 돌려준다.
    highest = max(normalized)
    if highest > _MAX_BOOSTED_SHARE:
        index = normalized.index(highest)
        spare = highest - _MAX_BOOSTED_SHARE
        rest = sum(value for i, value in enumerate(normalized) if i != index) or 1.0
        normalized = [
            _MAX_BOOSTED_SHARE if i == index else value + spare * value / rest
            for i, value in enumerate(normalized)
        ]
    return normalized


def _weight_samples(priorities: list[str], comparison_goals: list[str]) -> list[list[float]]:
    if not priorities and not comparison_goals:
        return [list(vector) for vector in _BASE_WEIGHTS]
    return [_boosted(vector, priorities, comparison_goals) for vector in _BASE_WEIGHTS]


def _discriminating(utilities: list[list[float]]) -> set[str]:
    """후보 사이에서 실제로 값이 갈리는 축만 돌려준다.

    요청한 혜택이 없으면 benefit 효용은 전 후보 같은 값이다. 이런 상수 축에 우선순위를
    얹으면 가중치의 대부분이 아무것도 구분하지 못하는 축으로 빠지고, 남은 자투리로만
    순위가 정해진다. ('혜택 비슷한 걸로' 요청에 0.5GB 요금제가 1순위로 올라온 원인)
    """
    if len(utilities) < 2:
        return set(CRITERIA)
    return {
        name
        for index, name in enumerate(CRITERIA)
        if max(row[index] for row in utilities) - min(row[index] for row in utilities) > 1e-9
    }


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
    usable = _discriminating(utilities)
    weights = _weight_samples(
        [name for name in (priorities or []) if name in usable],
        [
            goal
            for goal in (comparison_goals or [])
            if _GOAL_CRITERIA.get(goal, "price") in usable
        ],
    )
    samples = len(weights)
    n = len(candidates)
    rank_counts = [[0] * n for _ in candidates]
    favorable_weight_sums = [[0.0] * len(CRITERIA) for _ in candidates]
    favorable_counts = [0] * n
    fit_sums = [0.0] * n

    for weight in weights:
        totals = [sum(w * u for w, u in zip(weight, row)) for row in utilities]
        order = sorted(range(n), key=lambda i: (-totals[i], str(candidates[i]["plan_id"])))
        for index, total in enumerate(totals):
            fit_sums[index] += total
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
    # 상위 3위 안에 든 비율. favorable_counts 는 이미 rank < 3 조건으로 집계돼 있다.
    top3_acceptabilities = [count / samples for count in favorable_counts]
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
            utilities=tuple(utilities[i]),
            recommendation_fit=max(0.0, min(100.0, 100 * fit_sums[i] / samples)),
            top3_acceptability=top3_acceptabilities[i],
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

    # 우선순위 하나가 결정을 독점하면 안 된다
    price_index = CRITERIA.index("price")
    boosted_one = _weight_samples(["price"], [])[0]
    assert 0.45 < boosted_one[price_index] <= _MAX_BOOSTED_SHARE, boosted_one[price_index]
    assert boosted_one[price_index] > _BASE_WEIGHTS[0][price_index], "우선순위는 반영돼야 한다"
    assert abs(sum(boosted_one) - 1.0) < 1e-9
    for vector in _weight_samples(["price", "data", "qos"], ["cheaper"]):
        assert max(vector) <= _MAX_BOOSTED_SHARE + 1e-9
        assert abs(sum(vector) - 1.0) < 1e-9

    # 전 후보가 같은 값인 축은 우선순위를 얹어도 가중치를 가져가지 않는다
    flat = [
        {"plan_id": "a", "discounted_fee": 10_000, "monthly_fee": 10_000, "data_gb": 5},
        {"plan_id": "b", "discounted_fee": 30_000, "monthly_fee": 30_000, "data_gb": 90},
    ]
    assert "voice" not in _discriminating(_utility_rows(flat, None))
    assert "price" in _discriminating(_utility_rows(flat, None))
    flat_priority = evaluate_mcda(flat, ["voice"])
    assert rank_smaa2(flat_priority) == rank_smaa2(evaluate_mcda(flat)), (
        "구분되지 않는 축의 우선순위는 순위를 바꾸지 않아야 한다"
    )

    # 혜택을 요청하지 않았으면 혜택 금액도 개수도 순위를 바꾸지 않는다.
    # 수집된 혜택 금액은 사용자가 그 서비스를 실제로 쓰는지 확인돼야 절약액이 된다.
    valued = [
        {"plan_id": "rich", "discounted_fee": 20_000, "monthly_fee": 20_000, "data_gb": 10,
         "benefit_value_won": 20_000, "included_benefits": ["OTT", "멤버십", "데이터쉐어링"]},
        {"plan_id": "poor", "discounted_fee": 20_000, "monthly_fee": 20_000, "data_gb": 10,
         "benefit_value_won": 0, "included_benefits": []},
    ]
    benefit_column = [row[benefit_index] for row in _utility_rows(valued, None)]
    assert benefit_column == [0.5, 0.5], benefit_column
    assert "benefit" not in _discriminating(_utility_rows(valued, None))
    # 혜택이 많은 쪽이 비싼 상황: 혜택 때문에 순위가 뒤집히면 안 된다
    tilted = [
        {**valued[0], "discounted_fee": 25_000, "monthly_fee": 25_000},
        valued[1],
    ]
    assert rank_smaa2(evaluate_mcda(tilted))[0].plan_id == "poor", "혜택이 가격을 이기면 안 된다"

    # 혜택을 요청하면 그때는 요청과 일치하는지로 갈린다
    asked = [row[benefit_index] for row in _utility_rows(valued, {"wanted_benefits": ["OTT"]})]
    assert asked == [1.0, 0.0], asked

    # '무제한' 요청: 소진 후 등급이 같아도 전속으로 버티는 양이 순위를 갈라야 한다
    unlimited_ask = [
        {"plan_id": "small", "discounted_fee": 100, "monthly_fee": 100, "data_gb": 4.5,
         "qos_mbps": 1.0, "data_tier": "qos_lite"},
        {"plan_id": "large", "discounted_fee": 7000, "monthly_fee": 7000, "data_gb": 120,
         "qos_mbps": 5.0, "data_tier": "qos_hd"},
        {"plan_id": "lite_large", "discounted_fee": 100, "monthly_fee": 100, "data_gb": 120,
         "qos_mbps": 1.0, "data_tier": "qos_lite"},
    ]
    ask = {"data_unlimited": True, "estimated_monthly_data_gb": 128.7}
    data_column = [row[data_index] for row in _utility_rows(unlimited_ask, ask)]
    assert data_column[0] < data_column[2] < data_column[1], data_column
    # 예상 사용량을 모르면 등급만 본다 (같은 등급끼리는 동점)
    no_target = [row[data_index] for row in _utility_rows(unlimited_ask, {"data_unlimited": True})]
    assert no_target[0] == no_target[2] < no_target[1], no_target
    assert len(evaluate_mcda(valued)[0].utilities) == len(CRITERIA)

    # recommendation_fit/top3_acceptability: 0~100, 0~1 범위 안에 있고, 1위가 꼴찌보다 높아야 한다.
    fit_check = evaluate_mcda(sample, ["price"])
    for decision in fit_check:
        assert 0.0 <= decision.recommendation_fit <= 100.0
        assert 0.0 <= decision.top3_acceptability <= 1.0
    ranked_fit_check = rank_smaa2(fit_check)
    assert ranked_fit_check[0].recommendation_fit >= ranked_fit_check[-1].recommendation_fit
    # 후보 2건이면 둘 다 항상 상위 3위 안이라 top3_acceptability는 1.0이어야 한다.
    assert all(decision.top3_acceptability == 1.0 for decision in fit_check)
    print("self-check ok: SMAA-2 ranking (bootstrap weights)")
