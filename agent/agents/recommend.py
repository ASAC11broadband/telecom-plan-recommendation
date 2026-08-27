"""2단계 — Hard Filter 후보를 줄이고 사용자 맥락에 맞게 랭킹한다."""

from __future__ import annotations

import json
import math

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from ..data import filter_candidates, find_plans_by_name
from ..schemas import RankingResult, ScoredPlan, UserProfile
from ..state import PipelineState, feedback_block, get_llm, user_query


RECOMMEND_PROMPT = """Hard Filter를 통과한 실제 요금제 후보만 사용해 상위 5개를 고르라.

- plan_id와 plan_name은 후보 값을 글자 그대로 복사한다.
- 사용자가 priorities를 말했다면 그 순서를 가장 중요하게 반영한다.
- 고정 가중치를 발명하지 말고 가격·데이터 적합성·혜택·QoS·통화·문자의 트레이드오프를 비교한다.
- 데이터 무제한과 QoS는 서로 다른 속성이다. QoS가 높아도 무제한으로 재분류하지 않는다.
- 사용량 추정값이 있으면 데이터 적합성 판단에 사용하되 추정값임을 이유에 드러낸다.
- score는 이 후보군 안에서의 상대 적합도이며 0~100 정수다.
- 할인 가격은 discount_period_months 동안만 유효할 수 있으므로 기간과 종료 후 요금을 함께 고려한다.
- reason은 후보 데이터에 있는 사실만 사용해 1~2문장으로 쓴다.
- 후보가 5개보다 적으면 존재하는 후보만 반환한다.
"""


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
        "effective_fee": profile.reference_fee_won,
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
        "effective_fee",
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
        f"{plan['plan_name']}({plan['carrier']}, 월 {plan['effective_fee']:,}원)"
        for plan in matched[:5]
    )
    return None, f"같은 이름으로 스펙이 다른 요금제가 있습니다. 어느 상품인지 알려주세요: {choices}"


def _apply_comparison(
    candidates: list[dict], reference: dict | None, goals: list[str]
) -> list[dict]:
    if not reference or not goals:
        return candidates

    result = candidates
    if "cheaper" in goals and reference.get("effective_fee") is not None:
        result = [p for p in result if p["effective_fee"] < reference["effective_fee"]]
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
    if reference.get("effective_fee") is not None:
        comparisons.append((candidate["effective_fee"], reference["effective_fee"], False))
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
    if reference.get("effective_fee"):
        distance += abs(plan["effective_fee"] - reference["effective_fee"]) / reference["effective_fee"]
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
    for plan in sorted(candidates, key=lambda p: p["effective_fee"]):
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
        "price": lambda p: (p["effective_fee"], -_data_value(p)),
        "data": lambda p: (-_data_value(p), p["effective_fee"]),
        "benefit": lambda p: (-len(p.get("included_benefits") or []), p["effective_fee"]),
        "qos": lambda p: (-(p.get("qos_mbps") or 0), p["effective_fee"]),
        "voice": lambda p: (-_voice_value(p), -int(p.get("sms_unlimited", False)), p["effective_fee"]),
    }
    priority_axes = ["benefit" if p == "benefit" else p for p in profile.priorities or []]
    axis_order = list(dict.fromkeys(priority_axes + ["price", "data", "benefit", "qos", "voice"]))

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
                p["effective_fee"],
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
            "clarification_question": question,
            "messages": [AIMessage(content=question, name="recommend")],
        }

    candidates = filter_candidates(profile.model_dump())
    candidates = _apply_comparison(candidates, reference, profile.comparison_goals or [])
    if not candidates:
        return {
            "candidates": [],
            "ranked": [],
            "clarification_question": None,
            "messages": [AIMessage(content="조건을 만족하는 요금제가 없습니다.", name="recommend")],
        }

    shortlist = _shortlist(_dedupe_by_name(candidates), profile, reference)
    prompt = (
        RECOMMEND_PROMPT
        + "\n\n[사용자 원문]\n"
        + user_query(state)
        + "\n\n[UserProfile]\n"
        + profile.model_dump_json(exclude_none=True)
        + "\n\n[비교 기준]\n"
        + json.dumps(reference, ensure_ascii=False)
        + "\n\n[후보]\n"
        + json.dumps(shortlist, ensure_ascii=False)
        + "\n\n"
        + feedback_block(state)
    )
    result: RankingResult = get_llm(config).with_structured_output(RankingResult).invoke(
        [SystemMessage(content=prompt)]
    )

    by_id = {candidate["plan_id"]: candidate for candidate in shortlist}
    ranked: list[ScoredPlan] = []
    seen: set[str] = set()
    for plan in result.plans:
        candidate = by_id.get(plan.plan_id)
        if candidate is None or plan.plan_id in seen:
            continue
        seen.add(plan.plan_id)
        ranked.append(plan.model_copy(update={"plan_name": candidate["plan_name"]}))
        if len(ranked) == 5:
            break

    return {
        "candidates": candidates,
        "ranked": ranked,
        "clarification_question": None,
        "messages": [
            AIMessage(
                content=f"[recommend] {json.dumps([p.model_dump() for p in ranked], ensure_ascii=False)}",
                name="recommend",
            )
        ],
    }
