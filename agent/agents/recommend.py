"""2단계 — Hard Filter 후보를 줄이고 사용자 맥락에 맞게 랭킹한다."""

from __future__ import annotations

import json
import math

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from ..data import DATA_TIERS, data_tier, is_effectively_unlimited, diagnose_empty, filter_candidates, find_plans_by_name
from ..mcda import CRITERIA, COMPARE_MONTHS, _effective_monthly_fee, evaluate_mcda, rank_smaa2
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


def _with_current_facts(plan: dict, profile: UserProfile) -> dict:
    """카탈로그 가격보다 사용자가 알려준 현재 납부액·제공량을 우선한다."""
    facts = {key: value for key, value in (_reference_from_profile(profile) or {}).items() if value is not None}
    current = {**plan, **facts}
    if profile.reference_fee_won is not None:
        current.update(monthly_fee=profile.reference_fee_won, discount_period_months=None, billing_price_known=True)
    if profile.reference_data_gb is not None and profile.reference_data_unlimited is None:
        current['data_unlimited'] = False
    if profile.reference_voice_minutes is not None and profile.reference_voice_unlimited is None:
        current['voice_unlimited'] = False
    if any(key in facts for key in ('data_gb', 'data_unlimited', 'qos_mbps')):
        tier = data_tier(current.get('data_unlimited'), current.get('qos_mbps'))
        current.update(data_tier=tier, data_tier_label=DATA_TIERS[tier],
                       effective_unlimited=is_effectively_unlimited(current.get('data_unlimited'), current.get('qos_mbps'),
                                                                    current.get('data_gb')))
        current['data'] = '무제한' if current.get('data_unlimited') else (
            f"{current['data_gb']:g}GB" if current.get('data_gb') is not None else '확인 필요')
    if any(key in facts for key in ('voice_minutes', 'voice_unlimited')):
        current['voice'] = '무제한' if current.get('voice_unlimited') else (
            f"{current['voice_minutes']}분" if current.get('voice_minutes') is not None else '확인 필요')
    return current


def _resolve_reference(profile: UserProfile) -> tuple[dict | None, str | None]:
    if not profile.reference_plan_name:
        return _reference_from_profile(profile), None

    matched = find_plans_by_name(profile.reference_plan_name)
    if not matched:
        return None, f"'{profile.reference_plan_name}' 요금제를 DB에서 찾지 못했습니다. 정확한 요금제명을 알려주세요."
    if len(matched) == 1:
        return _with_current_facts(matched[0], profile), None

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
        return _with_current_facts(matched[0], profile), None

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
        result = [p for p in result if _effective_monthly_fee(p) < _effective_monthly_fee(reference)]
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
    if not _known_reference_axes(reference).issubset(_known_reference_axes(candidate)):
        return False  # 미수집 제공량을 0으로 채워 우열을 단정하지 않는다.
    if not _known_comparison_price(reference) or not _known_comparison_price(candidate):
        return False
    comparisons: list[tuple[float, float, bool]] = []
    if reference.get("discounted_fee") is not None:
        comparisons.append((_effective_monthly_fee(candidate), _effective_monthly_fee(reference), False))
    if "data" in _known_reference_axes(reference):
        comparisons.append((_data_value(candidate), _data_value(reference), True))
    if reference.get("qos_mbps") is not None:
        comparisons.append((candidate.get("qos_mbps") or 0, reference["qos_mbps"], True))
    if "voice" in _known_reference_axes(reference):
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
    if reference.get("data_unlimited") is True or reference.get("data_gb") is not None:
        axes.add("data")
    if reference.get("voice_unlimited") is True or reference.get("voice_minutes") is not None:
        axes.add("voice")
    if reference.get("qos_mbps") is not None:
        axes.add("qos")
    return axes


def _known_comparison_price(plan: dict) -> bool:
    if plan.get('billing_price_known') is False:
        return False
    fee = plan.get('discounted_fee')
    regular = plan.get('monthly_fee')
    return fee is not None and (regular is None or regular == fee or plan.get('discount_period_months') is not None)


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
    if not _known_comparison_price(reference):
        missing.append("할인 기간을 포함한 요금")
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

    comparable = [plan for plan in candidates
                  if axes.issubset(_known_reference_axes(plan)) and _known_comparison_price(plan)]
    if not comparable:
        return {"status": "undetermined", "reason": "후보의 요금·제공량 정보가 부족해 현재 요금제와 비교하지 못했습니다.",
                "missing": [], "confirm": confirm}
    better = sum(1 for plan in comparable if _is_pareto_better(plan, reference))
    cheaper = sum(1 for plan in comparable
                  if _effective_monthly_fee(plan) < _effective_monthly_fee(reference))
    unknown = len(candidates) - len(comparable)
    scope = f"확인된 요금·제공량과 {COMPARE_MONTHS}개월 평균요금 기준으로 "
    if unknown:
        confirm.append(f"정보가 부족한 후보 {unknown}건은 우열 비교에서 제외했습니다.")
    if better:
        return {
            "status": "switch",
            "reason": (
                scope + f"현재 요금제보다 나쁘지 않고 최소 한 항목이 더 나은 후보가 {better}건 있습니다. 실제 전환 이익은 결합할인과 위약금 확인 후 판단해 주세요."
            ),
            "betterCount": better,
            "cheaperCount": cheaper,
            "missing": [],
            "confirm": confirm,
        }
    return {
        "status": "keep",
        "reason": (
            scope + "현재 요금제보다 확실히 우위인 후보를 찾지 못했습니다. "
            f"더 싼 후보 {cheaper}건은 제공량이나 속도와 맞교환이 필요합니다. "
            "현재 수준을 유지하려면 기존 요금제도 선택지입니다. 이것만으로 현재 요금제가 최적이라고 단정할 수는 없습니다."
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


# 사용자가 상품을 고를 때 실제로 보고 갈라지는 값들. 이게 다르면 다른 상품이다.
_OFFER_FIELDS = (
    "plan_name",
    "age_condition",
    "carrier",
    "carrier_type",
    "host_mno",
    "mvno_brand",
    "network_gen",
    "data_gb",
    "data_unlimited",
    "voice_minutes",
    "voice_unlimited",
)


def _offer_key(plan: dict) -> tuple:
    """같은 상품의 같은 조건이면 같은 키. 요금은 넣지 않는다(싼 쪽을 남기려고)."""
    return (
        *(str(plan.get(field)) for field in _OFFER_FIELDS),
        tuple(sorted(str(value) for value in plan.get("included_benefits") or [])),
    )


def _dedupe_identical_offers(candidates: list[dict]) -> list[dict]:
    """가입 조건·혜택·망까지 똑같은 중복 행만 합친다. 남기는 것은 실납부액이 싼 쪽.

    예전에는 plan_name 하나로만 묶었다. 그러면 이름이 같다는 이유로 실제로 다른 상품이
    사라진다 — 동명 그룹 200개(569행) 중 지워지던 369행을 실측해 보니 313행이
    가입 조건·포함 혜택·망·데이터가 다른 별개 상품이었다. 혜택이 하나 더 많은 청년 전용
    상품이 같은 요금인데도 조용히 빠지고 있었다.

    이름이 같은 변형이 상위 몇 개를 나눠 먹는 문제는 여기서 지워서 막는 게 아니라
    _diverse_selection 이 고를 때 막는다. 후보에서 없애면 그 상품은 아예 볼 수 없다.
    """
    best: dict[tuple, dict] = {}
    for plan in sorted(candidates, key=lambda p: p["discounted_fee"]):
        best.setdefault(_offer_key(plan), plan)
    return list(best.values())


# 데이터 제공량을 '체감이 갈리는' 구간으로만 나눈다. 같은 구간이면 고르는 기준이 사실상 같다.
_DATA_BANDS = (3, 10, 20, 50, 100, 200)


def _data_band(plan: dict) -> str:
    if plan.get("data_unlimited"):
        return "unlimited"
    gb = float(plan.get("data_gb") or 0)
    return str(next((index for index, edge in enumerate(_DATA_BANDS) if gb < edge), len(_DATA_BANDS)))


def _offer_character(plan: dict) -> tuple:
    """'어떤 성격의 선택지인가'. 사업자·데이터 구간·소진 후 등급이 같으면 같은 성격으로 본다."""
    return (str(plan.get("carrier")), _data_band(plan), str(plan.get("data_tier")))


def _prefer_network_generation(ordered: list, by_id: dict[str, dict], preference: str | None) -> list:
    """세대 우선은 결과 순서만 바꾼다. 반대 세대 후보를 없애지 않는다."""
    if preference not in ("LTE", "5G"):
        return ordered
    matching = [decision for decision in ordered if by_id[decision.plan_id].get("network_gen") == preference]
    return matching + [decision for decision in ordered if by_id[decision.plan_id].get("network_gen") != preference]


# 화면에 내보내는 추천 개수. 여기 한 곳만 바꾸면 선정·리포트·테스트가 모두 따라온다.
TOP_N = 5


def _diverse_selection(ordered: list, by_id: dict[str, dict], limit: int = TOP_N) -> list:
    """순위를 지키면서, 성격이 겹치는 상품이 자리를 나눠 먹지 않게 고른다.

    한 사업자의 비슷한 라인업이 상위를 채우던 문제를 막는다(실측: 3만원 이하 20GB 이상
    요청에서 2·3·4위가 모두 같은 사업자의 20GB 상품이었다).

    성격 분류를 억지로 채우지는 않는다. 겹치지 않는 후보가 모자라면 미뤄 둔 후보를
    기대순위 순서대로 그냥 채운다. '절약형·데이터형·혜택형' 같은 칸을 만들지 않는다.
    같은 상품명이 두 번 나오는 것만은 끝까지 막는다(코드 검증이 중복 추천으로 잡는다).
    """
    picked, deferred = [], []
    seen_names, seen_characters = set(), set()
    for decision in ordered:
        plan = by_id[decision.plan_id]
        name = plan["plan_name"]
        character = _offer_character(plan)
        if name in seen_names or character in seen_characters:
            deferred.append(decision)
            continue
        seen_names.add(name)
        seen_characters.add(character)
        picked.append(decision)
        if len(picked) == limit:
            return picked

    for decision in deferred:
        name = by_id[decision.plan_id]["plan_name"]
        if name in seen_names:
            continue
        seen_names.add(name)
        picked.append(decision)
        if len(picked) == limit:
            break
    # 채우면서 순서가 흐트러졌으므로 최초 순위(세대 우선 포함)로 되돌린다.
    position = {decision.plan_id: index for index, decision in enumerate(ordered)}
    return sorted(picked, key=lambda decision: position[decision.plan_id])


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
    if reference and reference.get('billing_price_known') is False and not question:
        question = '현재 상품의 수집 가격은 페이백 반영 표시가입니다. 실제 월 납부액을 알려주시면 비교하겠습니다.'

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
    compared = _apply_comparison(candidates, reference, profile.comparison_goals or [])
    # '현재보다 더 나은 것'은 방향 요청이지 필수 조건이 아니다. 우위 후보가 없을 때 후보를
    # 0건으로 만들면 "비교하지 못했습니다"가 되는데, 사실은 비교한 끝에 우위가 없는 것이다.
    # 이 경우 전체 후보로 돌아가 유지 판정과 맞교환 후보를 보여준다. '더 싸게'·'데이터 더'
    # 같은 명시적 맞교환 요청은 그대로 필수로 남긴다.
    if not compared and candidates and (profile.comparison_goals or []) == ["better"]:
        compared = candidates
    candidates = compared
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

    # 현재 요금제에 대한 일반 비교에서는 판정 배너와 실제 카드의 범위를 맞춘다.
    # 더 싼 것/더 많은 데이터 등 명시적인 맞교환 요청은 기존 목적을 유지한다.
    ranking_pool = candidates
    if reference and not profile.comparison_goals:
        better_candidates = [plan for plan in candidates if _is_pareto_better(plan, reference)]
        if better_candidates:
            ranking_pool = better_candidates
    ranking_candidates = _dedupe_identical_offers(ranking_pool)
    by_id = {candidate["plan_id"]: candidate for candidate in ranking_candidates}
    decisions = evaluate_mcda(
        ranking_candidates,
        profile.priorities,
        comparison_goals=profile.comparison_goals,
        profile=ranking_profile,
    )
    ordered = _prefer_network_generation(
        rank_smaa2(decisions), by_id, profile.network_preference
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
            recommendation_fit=round(decision.recommendation_fit, 1),
            top3_acceptability=decision.top3_acceptability,
        )
        for decision in _diverse_selection(ordered, by_id)
    ]

    return {
        "candidates": candidates,
        "recommendation_trace": {
            "eligibleCount": len(candidates),
            "rankedCount": len(ranking_candidates),
            "referenceImprovementPreferred": ranking_pool is not candidates,
            "shownCount": len(ranked),
            "rankingProfile": ranking_profile.model_dump(exclude_none=True),
            "referenceBaselineApplied": ranking_profile is not profile,
            "deduplication": (
                "가입 조건·혜택·망까지 같은 중복 행만 1건으로 합침(현재 할인가가 낮은 쪽). "
                "이름이 같아도 조건이 다르면 별개 상품으로 남김"
            ),
            "diversification": (
                f"상위 {TOP_N}개는 사업자·데이터 구간·소진 후 등급이 겹치지 않게 고름. "
                "겹치지 않는 후보가 모자라면 기대순위 순서로 채움"
            ),
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


if __name__ == "__main__":
    from types import SimpleNamespace

    rows = {
        "five": {"network_gen": "5G"},
        "lte": {"network_gen": "LTE"},
        "unknown": {},
    }
    ordered = [
        SimpleNamespace(plan_id="five"),
        SimpleNamespace(plan_id="unknown"),
        SimpleNamespace(plan_id="lte"),
    ]
    preferred = _prefer_network_generation(ordered, rows, "LTE")
    assert [decision.plan_id for decision in preferred] == ["lte", "five", "unknown"]
    assert {decision.plan_id for decision in preferred} == {decision.plan_id for decision in ordered}
    print("self-check ok: network preference keeps all candidates")
