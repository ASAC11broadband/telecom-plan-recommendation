"""1단계 — 사용자 발화를 UserProfile로 구조화한다."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from ..schemas import UserProfile
from ..state import PipelineState, feedback_block, get_llm
from ..usage import estimate_monthly_data_gb


# 값이 있으면 data.py에서 그대로 필터링할 명시 조건 필드다.
CONSTRAINT_FIELDS = (
    "budget_min_won",
    "budget_max_won",
    "min_data_gb",
    "data_unlimited",
    "min_qos_mbps",
    "min_tethering_gb",
    "min_voice_minutes",
    "voice_unlimited",
    "sms_unlimited",
    "carrier_type",
    "host_mno",
    "mvno_brand",
    "network_gen",
    "age_condition",
    "wanted_benefits",
    "min_discount_period_months",
)


PROFILING_PROMPT = """휴대폰 요금제 상담 요청을 UserProfile로 구조화하라.

[기본]
- 사용자가 명시한 조건만 추출하고, 상품 검색·필터링·존재 여부 판단은 하지 않는다.
- 언급하지 않은 값은 추측하지 말고 null로 둔다. 특히 통화·문자를 무제한으로 가정하지 않는다.
- 사용자가 다른 선택도 괜찮다고 한 조건은 필터 필드에 넣지 말고 필요하면 notes에 기록한다.

[수치 조건]
- N만원대 → budget_min_won=N0,000, budget_max_won=N9,999
- N만원 이하/이상 → budget_max_won/budget_min_won
- N만원 정도·내외·안팎 → N만원 ±5,000원
- 데이터 NGB 이상, QoS·소진 후 NMbps 이상, 테더링 NGB 이상을 각각 최소 필드에 저장한다.
- 데이터 무제한은 data_unlimited=true, 통화 N분 이상과 통화 무제한은 해당 통화 필드에 저장한다.
- 무제한 상품을 명시적으로 제외할 때만 해당 unlimited 필드를 false로 저장한다.
- 문자는 sms_unlimited만 구조화한다. 문자 건수 조건은 필드를 만들지 말고 notes에 기록한다.

[통신사·상품군]
- 통신 3사만/알뜰폰 제외 → carrier_type=MNO, 알뜰폰만 → MVNO
- 알뜰폰 포함·알뜰폰도 괜찮음은 유형 제한이 아니므로 carrier_type=null
- 일반 SKT/KT/LGU+ 요청 → carrier_type=MNO와 host_mno
- 특정 망 알뜰폰 요청 → carrier_type=MVNO와 host_mno
- LG유플러스/LG U+는 LGU+로 정규화한다.
- 특정 알뜰폰 브랜드는 mvno_brand에 저장한다.
  KT엠모바일 → carrier_type=MVNO, host_mno=KT, mvno_brand=KT엠모바일
- LTE/5G는 network_gen에 저장한다.

[연령·혜택]
- 청년/청소년/키즈/시니어·어르신/군인·현역병사를 각각
  '만 34세 이하'/'만 18세 이하'/'만 12세 이하'/'만 65세 이상'/'현역병사'로 정규화한다.
- '청년 요금제', '20대 청년 요금제', '청년 전용 요금제'처럼 청년 상품군을
  명시적으로 요청하면 age_condition='만 34세 이하'로 저장한다.
- '나는 20대인데 추천해줘'처럼 나이만 밝히고 청년 상품군을 요청하지 않은 경우에는
  청년 전용 상품만 원한다고 단정하지 말고 age_condition을 설정하지 않는다.
- OTT·구독·멤버십 등 요구 혜택은 wanted_benefits에 저장한다.
- 단순히 넷플릭스·유튜브 등을 시청한다고 말한 것은 혜택 요구가 아니다.
  '포함/혜택/되는 요금제'처럼 상품 혜택을 원할 때만 wanted_benefits에 저장한다.

[이용 패턴]
- GB 수치 없이 이용 시간만 말하면 계산하지 말고 하루 시간만 저장한다.
- 유튜브 일반영상·넷플릭스·티빙·디즈니+ → daily_video_hours
- 릴스·틱톡·유튜브 쇼츠 → daily_shortform_hours
- 모바일 게임 → daily_game_hours
- 음악 스트리밍과 일반 SNS 피드는 위 시간 필드에 넣지 않는다.

[기준 요금제·비교]
- 현재·기존 요금제 이름은 reference_plan_name에 저장한다.
- 현재 요금제명 앞의 통신사명은 기준 상품 식별 정보일 뿐 새 상품의 carrier 조건으로 복사하지 않는다.
  예: '현재 KT 초이스90보다 싼 것' → reference_plan_name='초이스90', comparison_goals=['cheaper']
- 사용자가 현재 가격·데이터·통화·QoS를 직접 말하면 reference_* 필드에 저장한다.
- 비교 목적은 cheaper/more_data/faster_qos/similar/better 중 해당 값을 comparison_goals에 저장한다.
- 비교 기준값을 현재 추천 조건 필드에 복사하지 않는다. 실제 기준 상품 조회와 비교는 Recommend가 한다.

[할인 기간]
- 1년 이상 할인 → min_discount_period_months=12
- 1년 넘게 할인 → min_discount_period_months=13

[우선순위·Hard Constraint]
- priorities에는 사용자가 말한 정렬 기준만 저장한다. 없으면 null이며 시스템 기본값을 넣지 않는다.
- 구체적인 금액·사용량·통신사·혜택 등 필터 조건은 기본적으로 Hard Constraint다.
- 가장 싼 것·데이터 많은 순 같은 정렬 표현은 조건 필드가 아니라 priorities에만 저장한다.
- 가격대·연령·브랜드 변환처럼 정해진 정규화는 assumptions에 반복 기록하지 않는다.

[모호함·재질문]
- assumptions에는 사용자 발화만으로 확정할 수 없어 실제로 별도 가정을 적용한 경우만 기록한다.
- 모호해도 진행 가능하면 ambiguous만 기록하고 needs_user_input=false로 둔다.
- 핵심 결과가 크게 달라질 때만 needs_user_input=true로 두고 followup_question 하나를 작성한다.
- 언급되지 않은 모든 필드를 missing에 넣지 않는다.
"""


def _has_value(value: object) -> bool:
    """False는 명시 조건이지만 None과 빈 컨테이너는 조건이 아니다."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _normalize_profile(profile: UserProfile) -> UserProfile:
    """스키마 값으로 Hard Constraint와 재질문 상태를 결정한다."""
    if profile.mvno_brand and profile.carrier_type != "MVNO":
        profile = profile.model_copy(update={"carrier_type": "MVNO"})

    estimated_gb, usage_notes = estimate_monthly_data_gb(
        profile.daily_video_hours,
        profile.daily_shortform_hours,
        profile.daily_game_hours,
    )
    hard_constraints = [
        field for field in CONSTRAINT_FIELDS if _has_value(getattr(profile, field))
    ]
    followup_question = profile.followup_question if profile.needs_user_input else None

    return profile.model_copy(
        update={
            "hard_constraints": hard_constraints,
            "followup_question": followup_question,
            "estimated_monthly_data_gb": estimated_gb,
            "usage_estimate_notes": usage_notes,
        }
    )


def profiling_node(state: PipelineState, config: RunnableConfig) -> dict:
    messages = [
        message
        for message in state.get("messages", [])
        if isinstance(message, HumanMessage)
    ] or [HumanMessage(content="")]

    prompt = PROFILING_PROMPT + "\n\n" + feedback_block(state)
    llm = get_llm(config).with_structured_output(UserProfile)
    profile = _normalize_profile(
        llm.invoke([SystemMessage(content=prompt), *messages])
    )
    content = (
        profile.followup_question
        if profile.needs_user_input and profile.followup_question
        else f"[profiling] {profile.model_dump_json(exclude_none=True)}"
    )

    return {
        "profile": profile,
        "clarification_question": profile.followup_question
        if profile.needs_user_input
        else None,
        "messages": [
            AIMessage(
                content=content,
                name="profiling",
            )
        ],
    }
