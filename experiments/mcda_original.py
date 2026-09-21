"""SMAA-2 기반 다기준 요금제 랭킹."""

from __future__ import annotations

from dataclasses import dataclass
import math
import random


CRITERIA = (
    "price", "data", "qos", "benefit", "voice", "sms", "tethering", "carrier",
    "similarity", "improvement",
)


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


def _reference_similarity(plan: dict, reference: dict | None) -> float:
    if not reference:
        return 0.5
    distances: list[float] = []
    for field in ("discounted_fee", "data_gb", "qos_mbps", "voice_minutes"):
        target = reference.get(field)
        if target not in (None, 0):
            distances.append(abs(float(plan.get(field) or 0) - float(target)) / float(target))
    for field in ("data_unlimited", "voice_unlimited"):
        if reference.get(field) is not None:
            distances.append(float(bool(plan.get(field)) != bool(reference.get(field))))
    if not distances:
        return 0.5
    return 1.0 / (1.0 + sum(distances) / len(distances))


def _reference_improvement(plan: dict, reference: dict | None) -> float:
    if not reference:
        return 0.5
    gains: list[float] = []
    fee = reference.get("discounted_fee")
    if fee:
        gains.append((float(fee) - float(plan.get("discounted_fee") or 0)) / float(fee))
    for field in ("data_gb", "qos_mbps", "voice_minutes"):
        target = reference.get(field)
        if target not in (None, 0):
            gains.append((float(plan.get(field) or 0) - float(target)) / float(target))
    if not gains:
        return 0.5
    average = sum(gains) / len(gains)
    return max(0.0, min(1.0, 0.5 + average / 2.0))


def _utility_rows(
    candidates: list[dict], preferred_carrier: str | None, reference: dict | None
) -> list[list[float]]:
    data_max = max((float(p.get("data_gb") or 0) for p in candidates), default=1.0) or 1.0
    voice_max = max((float(p.get("voice_minutes") or 0) for p in candidates), default=1.0) or 1.0
    columns = {
        "price": _minmax([float(p.get("discounted_fee") or 0) for p in candidates], cost=True),
        "data": _minmax([_finite_data(p, data_max) for p in candidates]),
        "qos": _minmax([float(p.get("qos_mbps") or 0) for p in candidates]),
        "benefit": _minmax([float(len(p.get("included_benefits") or [])) for p in candidates]),
        "voice": _minmax([_finite_voice(p, voice_max) for p in candidates]),
        "sms": [1.0 if p.get("sms_unlimited") else 0.0 for p in candidates],
        "tethering": _minmax([float(p.get("tethering_gb") or 0) for p in candidates]),
        "carrier": [
            1.0
            if preferred_carrier
            and preferred_carrier
            in {p.get("host_mno"), p.get("mvno_brand"), p.get("carrier")}
            else 0.5
            for p in candidates
        ],
        "similarity": [_reference_similarity(p, reference) for p in candidates],
        "improvement": [_reference_improvement(p, reference) for p in candidates],
    }
    return [[columns[name][i] for name in CRITERIA] for i in range(len(candidates))]


def _weight_samples(
    priorities: list[str], comparison_goals: list[str], count: int, seed: int
) -> list[list[float]]:
    priority_position = {name: i for i, name in enumerate(priorities) if name in CRITERIA}
    alpha = [
        1.0 + max(0, len(priorities) - priority_position[name]) * 10.0
        if name in priority_position
        else 1.0
        for name in CRITERIA
    ]
    goal_criteria = {
        "cheaper": "price",
        "more_data": "data",
        "faster_qos": "qos",
        "similar": "similarity",
        "better": "improvement",
    }
    for goal in comparison_goals:
        criterion = goal_criteria.get(goal)
        if criterion:
            alpha[CRITERIA.index(criterion)] += 12.0
    rng = random.Random(seed)
    samples: list[list[float]] = []
    for _ in range(count):
        draw = [rng.gammavariate(a, 1.0) for a in alpha]
        total = sum(draw)
        samples.append([value / total for value in draw])
    return samples


def evaluate_mcda(
    candidates: list[dict],
    priorities: list[str] | None = None,
    preferred_carrier: str | None = None,
    *,
    reference: dict | None = None,
    comparison_goals: list[str] | None = None,
    samples: int = 1200,
    seed: int = 20260901,
) -> list[MCDAResult]:
    """가중치 표본별 순위를 집계해 SMAA-2 지표를 계산한다."""
    if not candidates:
        return []
    if samples <= 0:
        raise ValueError("samples는 1 이상이어야 합니다.")
    plan_ids = [str(candidate["plan_id"]) for candidate in candidates]
    if len(plan_ids) != len(set(plan_ids)):
        raise ValueError("MCDA 후보의 plan_id는 서로 달라야 합니다.")
    utilities = _utility_rows(candidates, preferred_carrier, reference)
    weights = _weight_samples(priorities or [], comparison_goals or [], samples, seed)
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
    first = evaluate_mcda(sample, ["price"], samples=400)
    second = evaluate_mcda(sample, ["price"], samples=400)
    assert first == second, "고정 seed에서 결과가 재현되어야 한다"
    assert rank_smaa2(first)[0].plan_id == "cheap", "가격 우선순위가 반영되어야 한다"
    print("self-check ok: SMAA-2 ranking")
