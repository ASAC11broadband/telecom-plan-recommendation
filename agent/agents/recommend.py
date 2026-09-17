"""2단계 — Hard Filter 후보를 줄이고 사용자 맥락에 맞게 랭킹한다."""

from __future__ import annotations

import json
import math

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from ..data import diagnose_empty, filter_candidates, find_plans_by_name
from ..mcda import CRITERIA, evaluate_mcda, rank_smaa2
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
        if reference.get("data_unlimited"):
            # 무제한보다 수치상 더 많은 데이터는 존재하지 않는다. 사용자의 의도는
            # 데이터 수준을 떨어뜨리지 않는 대안을 찾는 것으로 보고 무제한끼리 비교한다.
            # 소진 후 속도가 쓸 만한 QoS형도 같은 수준으로 본다(agent.data 참고).
            result = [p for p in result if p.get("effective_unlimited", p.get("data_unlimited"))]
        else:
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


# 요금제 데이터만으로는 끝까지 알 수 없는 것들. 유지든 전환이든 사용자가 직접 확인해야 한다.
REFERENCE_CONFIRM_NOTES = (
    "청구서의 실제 납부액은 요금제 정가와 다를 수 있습니다(단말 할부금·부가서비스 제외분).",
    "결합할인·가족결합·약정할인이 적용 중이면 전환할 때 함께 사라질 수 있습니다.",
    "약정이 남아 있으면 위약금이 생길 수 있습니다.",
)

_REFERENCE_AXIS_LABELS = {"fee": "월 요금", "data": "데이터 제공량"}


def _known_reference_axes(reference: dict) -> set[str]:
    """현재 요금제에 대해 실제로 아는 축. None 은 '없음'이 아니라 '모름'이다."""
    axes = set()
    if reference.get("discounted_fee") is not None:
        axes.add("fee")
    if reference.get("data_unlimited") is not None or reference.get("data_gb") is not None:
        axes.add("data")
    if reference.get("voice_unlimited") is not None or reference.get("voice_minutes") is not None:
        axes.add("voice")
    if reference.get("qos_mbps") is not None:
        axes.add("qos")
    return axes


def _reference_verdict(reference: dict | None, candidates: list[dict]) -> dict | None:
    """현재 요금제를 유지하는 게 나은지 — 코드로만 판정한다.

    세 상태를 구분한다. 특히 '판단 불가'를 '유지가 낫다'로 흘려보내지 않는다.
      keep         현재 요금제를 모든 비교 항목에서 앞서는 후보가 없다.
      switch       파레토 우위 후보가 있다(모든 항목에서 나쁘지 않고 최소 한 항목이 낫다).
      undetermined 비교가 성립하지 않는다 — 현재 스펙을 모르거나 후보가 없다.

    요금만 알고 데이터를 모르면 비교가 성립하지 않는다. 그 상태에서 '더 싼 게 있다'는
    말은 무엇을 포기하는지 빼고 한 말이라, 유불리 판정으로 쓸 수 없다.
    """
    if not reference:
        return None

    axes = _known_reference_axes(reference)
    confirm = list(REFERENCE_CONFIRM_NOTES)
    missing = [label for axis, label in _REFERENCE_AXIS_LABELS.items() if axis not in axes]
    if missing:
        return {
            "status": "undetermined",
            "reason": (
                f"현재 요금제의 {', '.join(missing)}을(를) 알 수 없어 지금이 유리한지 판단하지 못했습니다."
            ),
            "missing": missing,
            "confirm": confirm,
        }

    if not candidates:
        return {
            "status": "undetermined",
            "reason": (
                "조건을 만족하는 후보가 없어 현재 요금제와 비교하지 못했습니다. "
                "후보가 없다는 것이 현재 요금제가 유리하다는 뜻은 아닙니다."
            ),
            "missing": [],
            "confirm": confirm,
        }

    better = sum(1 for plan in candidates if _is_pareto_better(plan, reference))
    cheaper = sum(
        1 for plan in candidates if plan["discounted_fee"] < reference["discounted_fee"]
    )
    if better:
        return {
            "status": "switch",
            "reason": (
                f"현재 요금제보다 모든 비교 항목에서 나쁘지 않고 최소 한 항목이 더 나은 후보가 {better}건 있습니다."
            ),
            "betterCount": better,
            "cheaperCount": cheaper,
            "missing": [],
            "confirm": confirm,
        }
    return {
        "status": "keep",
        "reason": (
            "현재 요금제를 모든 비교 항목에서 앞서는 후보가 없습니다. "
            f"더 싼 후보는 {cheaper}건 있지만 그만큼 제공량이나 속도를 내주는 상품입니다. "
            "맞교환을 감수할 생각이 없다면 지금 요금제를 유지하는 편이 낫습니다."
        ),
        "betterCount": 0,
        "cheaperCount": cheaper,
        "missing": [],
        "confirm": confirm,
    }


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


def _with_reference_baseline(profile: UserProfile, reference: dict | None) -> UserProfile:
    """기준 요금제가 있으면 그 제공량을 데이터 목표치의 기본값으로 삼는다.

    '지금 쓰는 무제한 요금제보다 싼 걸로'라고만 하면 데이터 조건이 비어 있어, 예산만
    맞추는 0.5GB 요금제가 1순위로 올라온다. 사용자가 원한 건 절약이지 다운그레이드가
    아니다. 사용자가 데이터 조건을 직접 말했으면 그 값을 그대로 둔다.
    """
    if reference is None:
        return profile
    if any(
        value is not None
        for value in (
            profile.data_unlimited,
            profile.min_data_gb,
            profile.target_data_gb,
            profile.max_data_gb,
            profile.estimated_monthly_data_gb,
        )
    ):
        return profile
    if reference.get("data_unlimited") or reference.get("effective_unlimited"):
        return profile.model_copy(update={"data_unlimited": True})
    if reference.get("data_gb"):
        return profile.model_copy(update={"target_data_gb": float(reference["data_gb"])})
    return profile


def recommend_node(state: PipelineState, config: RunnableConfig) -> dict:
    profile = state.get("profile") or UserProfile()
    reference, question = _resolve_reference(profile)

    if question:
        return {
            "candidates": [],
            "ranked": [],
            "reference": None,
            "reference_verdict": None,
            "clarification_question": question,
            "messages": [AIMessage(content=question, name="recommend")],
        }

    # 기준 요금제가 있으면 그 수준을 데이터 조건의 기본값으로 세운 뒤 후보를 거른다.
    # 필터가 아니라 점수에만 반영하면 "무제한보다 싼 것" 요청에 0.5GB 요금제가 살아남는다.
    ranking_profile = _with_reference_baseline(profile, reference)
    candidates = filter_candidates(ranking_profile.model_dump())
    candidates = _apply_comparison(candidates, reference, profile.comparison_goals or [])
    if not candidates:
        # 무엇을 고치면 되는지 함께 돌려준다. "없습니다"만으로는 사용자가 다음 수를 못 둔다.
        blockers = diagnose_empty(ranking_profile.model_dump())
        return {
            "candidates": [],
            "ranked": [],
            "reference": reference,
            # 후보가 0건이어도 그것만으로 현재 요금제가 유리하다고 말하지 않는다.
            "reference_verdict": _reference_verdict(reference, []),
            "blockers": blockers,
            "clarification_question": None,
            "messages": [AIMessage(content="조건을 만족하는 요금제가 없습니다.", name="recommend")],
        }

    ranking_candidates = _dedupe_by_name(candidates)
    by_id = {candidate["plan_id"]: candidate for candidate in ranking_candidates}
    decisions = evaluate_mcda(
        ranking_candidates,
        profile.priorities,
        comparison_goals=profile.comparison_goals,
        profile=ranking_profile,
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
            criteria_fit=dict(zip(CRITERIA, (round(value, 3) for value in decision.utilities))),
            expected_rank=decision.smaa2_expected_rank,
            first_rank_acceptability=decision.smaa2_first_rank_acceptability,
        )
        for decision in rank_smaa2(decisions)[:5]
    ]

    return {
        "candidates": candidates,
        "recommendation_trace": {
            "eligibleCount": len(candidates),
            "rankedCount": len(ranking_candidates),
            "shownCount": len(ranked),
            "rankingProfile": ranking_profile.model_dump(exclude_none=True),
            "referenceBaselineApplied": ranking_profile is not profile,
            "deduplication": "동일 상품명은 현재 할인가가 가장 낮은 1건만 순위 계산",
        },
        "ranked": ranked,
        "reference": reference,
        "reference_verdict": _reference_verdict(reference, candidates),
        "blockers": [],
        "clarification_question": None,
        "messages": [
            AIMessage(
                content=f"[recommend] {json.dumps([p.model_dump() for p in ranked], ensure_ascii=False)}",
                name="recommend",
            )
        ],
    }
