"""SMAA-2 기반 다기준 요금제 랭킹.

가중치 표본은 더 이상 무작위(감마분포)로 지어내지 않는다. `weight_bootstrap.json`에
실제 알뜰폰 가입자 데이터(로그 시장점유율 ~ LightGBM, SHAP 중요도)를 부트스트랩 60회
돌려 뽑은 가중치 60세트가 들어있다 — 자세한 도출 과정은
notebooks/logshare_shap_weights.ipynb 참고. 우선순위/비교 목표가 있으면 그 60세트
각각에 가산 후 재정규화해서 반영한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math


CRITERIA = ("price", "data", "qos", "benefit", "voice", "sms", "tethering")

# 우선순위 1단계당/비교 목표당 얼마나 가산할지. 예전 감마분포 버전이 균등 alpha=1.0
# 기준(합계 len(CRITERIA))에 +10.0/+12.0을 더하던 것과 같은 비율로, 합계가 1인
# 실측 가중치 벡터에 맞게 축소했다.
_PRIORITY_UNIT = 10.0 / len(CRITERIA)
_GOAL_UNIT = 12.0 / len(CRITERIA)

_WEIGHT_DATA_PATH = Path(__file__).resolve().parent / "weight_bootstrap.json"


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


def _finite_voice(plan: dict, finite_max: float) -> float:
    return finite_max * 1.25 if plan.get("voice_unlimited") else float(plan.get("voice_minutes") or 0)


def _finite_sms(plan: dict, finite_max: float) -> float:
    return finite_max * 1.25 if plan.get("sms_unlimited") else float(plan.get("sms_count") or 0)


def _utility_rows(candidates: list[dict]) -> list[list[float]]:
    data_max = max((float(p.get("data_gb") or 0) for p in candidates), default=1.0) or 1.0
    voice_max = max((float(p.get("voice_minutes") or 0) for p in candidates), default=1.0) or 1.0
    sms_max = max((float(p.get("sms_count") or 0) for p in candidates), default=1.0) or 1.0
    columns = {
        # price/data는 min-max 전에 log1p를 먼저 씌운다 — 저가/저용량 구간의 차이를
        # 크게, 고가/대용량 구간의 차이를 작게 반영한다(체감효과). 후보군 상대 정규화라
        # 절대 스케일(예: DATA_MAX)이 없어도 되고, 로그값끼리 다시 min-max하면 된다.
        "price": _minmax([math.log1p(float(p.get("discounted_fee") or 0)) for p in candidates], cost=True),
        "data": _minmax([math.log1p(_finite_data(p, data_max)) for p in candidates]),
        "qos": _minmax([float(p.get("qos_mbps") or 0) for p in candidates]),
        "benefit": _minmax([float(len(p.get("included_benefits") or [])) for p in candidates]),
        "voice": _minmax([_finite_voice(p, voice_max) for p in candidates]),
        # 예전엔 무제한 여부(1/0)만 봤다 — sms_count가 후보 dict에 없었다. 이제
        # data.py가 실제 문자 개수를 넘겨주니 voice와 같은 방식으로 정규화한다.
        "sms": _minmax([_finite_sms(p, sms_max) for p in candidates]),
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
) -> list[MCDAResult]:
    """가중치 표본(실측 부트스트랩 60세트)별 순위를 집계해 SMAA-2 지표를 계산한다."""
    if not candidates:
        return []
    plan_ids = [str(candidate["plan_id"]) for candidate in candidates]
    if len(plan_ids) != len(set(plan_ids)):
        raise ValueError("MCDA 후보의 plan_id는 서로 달라야 합니다.")
    utilities = _utility_rows(candidates)
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

    baseline = evaluate_mcda(sample)  # 우선순위 없음 -> 실측 60세트 그대로
    assert len(baseline[0].favorable_weights) == len(CRITERIA)
    print("self-check ok: SMAA-2 ranking (bootstrap weights)")
