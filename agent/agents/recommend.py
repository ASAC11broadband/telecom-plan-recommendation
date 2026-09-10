"""2단계 — Hard Filter 후보를 줄이고 사용자 맥락에 맞게 랭킹한다."""

from __future__ import annotations

import math

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from ..data import filter_candidates, find_plans_by_name
from ..mcda import evaluate_mcda, rank_smaa2
from ..schemas import ScoredPlan, UserProfile
from ..state import PipelineState


def _data_value(plan: dict) -> float:
    if plan.get("data_unlimited"):
        return math.inf
    return float(plan.get("data_gb") or 0)


def _voice_value(plan: dict) -> float:
    if plan.get("voice_unlimited"):
        return math.inf
    return float(plan.get("voice_minutes") or 0)


def _reference_from_profile(profile: UserProfile) -> dict | None:
    values = {
        "discounted_fee": profile.reference_fee_won,
        "data_gb": profile.reference_data_gb,
        "data_unlimited": profile.reference_data_unlimited,
        "voice_minutes": profile.reference_voice_minutes,
        "voice_unlimited": profile.reference_voice_unlimited,
        "qos_mbps": profile.reference_qos_mbps,
    }
    return values if any(value is not None for value in values.values()) else None


def _resolve_reference(profile: UserProfile) -> tuple[dict | None, str | None]:
    if not profile.reference_plan_name:
        return _reference_from_profile(profile), None

    matched = find_plans_by_name(profile.reference_plan_name)
    if not matched:
        return None, f"'{profile.reference_plan_name}' 요금제를 DB에서 찾지 못했습니다. 정확한 요금제명을 알려주세요."
    if len(matched) == 1:
        return matched[0], None

    spec_fields = (
        "discounted_fee",
        "data_gb",
        "data_unlimited",
        "qos_mbps",
        "voice_minutes",
        "voice_unlimited",
        "network_gen",
    )
    specs = {tuple(plan.get(field) for field in spec_fields) for plan in matched}
    if len(specs) == 1:
        return matched[0], None

    choices = ", ".join(
        f"{plan['plan_name']}({plan['carrier']}, 월 {plan['discounted_fee']:,}원)"
        for plan in matched[:5]
    )
    return None, f"같은 이름으로 스펙이 다른 요금제가 있습니다. 어느 상품인지 알려주세요: {choices}"


def _apply_comparison(
    candidates: list[dict], reference: dict | None, goals: list[str]
) -> list[dict]:
    if not reference or not goals:
        return candidates

    result = candidates
    if "cheaper" in goals and reference.get("discounted_fee") is not None:
        result = [p for p in result if p["discounted_fee"] < reference["discounted_fee"]]
    if "more_data" in goals:
        reference_data = _data_value(reference)
        result = [p for p in result if _data_value(p) > reference_data]
    if "faster_qos" in goals and reference.get("qos_mbps") is not None:
        result = [p for p in result if (p.get("qos_mbps") or 0) > reference["qos_mbps"]]
    if "better" in goals:
        result = [p for p in result if _is_pareto_better(p, reference)]
    return result


def _is_pareto_better(candidate: dict, reference: dict) -> bool:
    comparisons: list[tuple[float, float, bool]] = []
    if reference.get("discounted_fee") is not None:
        comparisons.append((candidate["discounted_fee"], reference["discounted_fee"], False))
    if reference.get("data_gb") is not None or reference.get("data_unlimited") is not None:
        comparisons.append((_data_value(candidate), _data_value(reference), True))
    if reference.get("qos_mbps") is not None:
        comparisons.append((candidate.get("qos_mbps") or 0, reference["qos_mbps"], True))
    if reference.get("voice_minutes") is not None or reference.get("voice_unlimited") is not None:
        comparisons.append((_voice_value(candidate), _voice_value(reference), True))
    if not comparisons:
        return False

    no_worse = all(left >= right if higher_better else left <= right for left, right, higher_better in comparisons)
    strictly_better = any(left > right if higher_better else left < right for left, right, higher_better in comparisons)
    return no_worse and strictly_better


def _similarity_distance(plan: dict, reference: dict) -> float:
    distance = 0.0
    if reference.get("discounted_fee"):
        distance += abs(plan["discounted_fee"] - reference["discounted_fee"]) / reference["discounted_fee"]
    if reference.get("data_unlimited") is not None:
        distance += 1.0 if plan.get("data_unlimited") != reference["data_unlimited"] else 0.0
    if not reference.get("data_unlimited") and reference.get("data_gb"):
        distance += abs((plan.get("data_gb") or 0) - reference["data_gb"]) / reference["data_gb"]
    if reference.get("qos_mbps"):
        distance += abs((plan.get("qos_mbps") or 0) - reference["qos_mbps"]) / reference["qos_mbps"]
    if reference.get("network_gen"):
        distance += 0.5 if plan.get("network_gen") != reference["network_gen"] else 0.0
    return distance


def _dedupe_by_name(candidates: list[dict]) -> list[dict]:
    """같은 요금제의 가입조건 변형이 순위를 나눠 먹지 않게 이름당 하나만 남긴다.

    plan_id 는 다르지만 plan_name 이 같은 행(예: age_condition 만 다른 초이스90)이
    상위 5개를 전부 채우는 것을 막는다. 같은 이름이면 실납부액이 싼 쪽을 남긴다.
    """
    best: dict[str, dict] = {}
    for plan in sorted(candidates, key=lambda p: p["discounted_fee"]):
        best.setdefault(plan["plan_name"], plan)
    return list(best.values())


def _shortlist(
    candidates: list[dict],
    profile: UserProfile,
    reference: dict | None = None,
    limit_per_axis: int = 6,
) -> list[dict]:
    """고정 총점 없이 여러 축의 우수 후보를 합쳐 LLM 입력을 제한한다."""
    if len(candidates) <= limit_per_axis * 5:
        return candidates

    axes = {
        "price": lambda p: (p["discounted_fee"], -_data_value(p)),
        "data": lambda p: (-_data_value(p), p["discounted_fee"]),
        "benefit": lambda p: (-len(p.get("included_benefits") or []), p["discounted_fee"]),
        "qos": lambda p: (-(p.get("qos_mbps") or 0), p["discounted_fee"]),
        "voice": lambda p: (-_voice_value(p), -int(p.get("sms_unlimited", False)), p["discounted_fee"]),
    }
    axis_order = list(
        dict.fromkeys((profile.priorities or []) + ["price", "data", "benefit", "qos", "voice"])
    )

    selected: dict[str, dict] = {}
    for axis in axis_order:
        key = axes.get(axis)
        if key is None:
            continue
        for plan in sorted(candidates, key=key)[:limit_per_axis]:
            selected.setdefault(plan["plan_id"], plan)

    if profile.estimated_monthly_data_gb is not None:
        target = profile.estimated_monthly_data_gb
        usage_fit = sorted(
            candidates,
            key=lambda p: (
                0 if p.get("data_unlimited") or (p.get("data_gb") or 0) >= target else 1,
                abs((p.get("data_gb") or 0) - target),
                p["discounted_fee"],
            ),
        )
        for plan in usage_fit[:limit_per_axis]:
            selected.setdefault(plan["plan_id"], plan)
    if reference and "similar" in (profile.comparison_goals or []):
        for plan in sorted(candidates, key=lambda p: _similarity_distance(p, reference))[:limit_per_axis]:
            selected.setdefault(plan["plan_id"], plan)
    return list(selected.values())


def recommend_node(state: PipelineState, config: RunnableConfig) -> dict:
    profile = state.get("profile") or UserProfile()
    reference, question = _resolve_reference(profile)

    if question:
        return {
            "candidates": [],
            "ranked": [],
            "reference": None,
            "clarification_question": question,
            "messages": [AIMessage(content=question, name="recommend")],
        }

    candidates = filter_candidates(profile.model_dump())
    candidates = _apply_comparison(candidates, reference, profile.comparison_goals or [])
    if not candidates:
        return {
            "candidates": [],
            "ranked": [],
            "reference": reference,
            "clarification_question": None,
            "messages": [AIMessage(content="조건을 만족하는 요금제가 없습니다.", name="recommend")],
        }

    ranking_candidates = _dedupe_by_name(candidates)
    by_id = {candidate["plan_id"]: candidate for candidate in ranking_candidates}
    preferred_carrier = profile.mvno_brand or profile.host_mno
    decisions = evaluate_mcda(
        ranking_candidates,
        profile.priorities,
        preferred_carrier,
        reference=reference,
        comparison_goals=profile.comparison_goals,
    )
    ranked = [
        ScoredPlan(
            plan_id=decision.plan_id,
            plan_name=by_id[decision.plan_id]["plan_name"],
            score=decision.smaa2_score,
            reason=(
                f"가중치 조합에서 기대순위 {decision.smaa2_expected_rank:.2f}, "
                f"1위 수용도 {decision.smaa2_first_rank_acceptability * 100:.1f}%입니다."
            ),
        )
        for decision in rank_smaa2(decisions)[:5]
    ]

    return {
        "candidates": candidates,
        "ranked": ranked,
        "reference": reference,
        "clarification_question": None,
        "messages": [
            AIMessage(
                content=f"[recommend] {json.dumps([p.model_dump() for p in ranked], ensure_ascii=False)}",
                name="recommend",
            )
        ],
    }
