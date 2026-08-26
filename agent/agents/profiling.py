"""1단계 — User Profiling Agent.

사용자 발화(+재시도 피드백)를 읽어 UserProfile 로 구조화한다.
"""

from __future__ import annotations

import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from ..schemas import UserProfile
from ..state import PipelineState, feedback_block, get_llm


PROFILING_PROMPT = """휴대폰 요금제 상담 요청을 UserProfile로 구조화하라.

[역할]
- 사용자가 원하는 조건만 추출·정규화한다.
- 조건을 만족하는 실제 요금제가 존재하는지는 판단하지 않는다.
- 사용자가 언급하지 않은 조건은 임의로 추측하지 말고 null로 둔다.
- 통화·문자를 언급하지 않았다고 무제한으로 설정하지 않는다.

[가격]
- 'N만원대'는 N0,000원 이상 N9,999원 이하로 해석해 예산 필드에 저장한다.
- 'N만원 이하/이상'은 해당 금액을 budget_max_won/budget_min_won으로 저장한다.
- 'N만원 정도/내외/안팎'은 N만원 ±5,000원으로 해석해 예산 필드에 저장한다.
- '저렴한/가장 싼/가격 우선'처럼 금액이 없는 표현은 예산을 만들지 말고 priorities에 price를 넣는다.
- 해석에 따라 핵심 후보군이 크게 달라지고 정규화 정책으로 해결할 수 없을 때만 재질문한다.

[통신사]
- '통신 3사만/알뜰폰 제외'는 carrier_type=MNO이다.
- '알뜰폰만'은 carrier_type=MVNO이다.
- '알뜰폰 포함/알뜰폰도 괜찮음'은 유형 제한이 아니므로 carrier_type=null이다.
- 일반적인 SKT/KT/LGU+ 요청은 해당 통신사 본사 요금제 요청으로 보고 carrier_type=MNO와
  host_mno를 함께 저장한다.
- 'KT망 알뜰폰'처럼 특정 망의 알뜰폰을 명시하면 carrier_type=MVNO와 host_mno를 함께 저장한다.
- LG유플러스/LG U+는 host_mno=LGU+로 정규화한다.
- 특정 알뜰폰 브랜드는 mvno_brand에 원문 브랜드명을 저장한다.
- 예: KT엠모바일은 carrier_type=MVNO, host_mno=KT, mvno_brand=KT엠모바일이다.

[연령·혜택]
- 청년은 age_condition='만 34세 이하', 청소년은 '만 18세 이하', 키즈는 '만 12세 이하',
  시니어/어르신은 '만 65세 이상', 군인/현역병사는 '현역병사'로 정규화한다.
- OTT·구독·부가혜택은 wanted_benefits에 서비스명으로 저장한다.

[우선순위·필수 조건]
- priorities에는 사용자가 직접 중요도나 정렬 기준을 말한 항목만 순서대로 저장한다.
- 사용자가 우선순위를 말하지 않았으면 null로 둔다. 시스템 기본 우선순위를 넣지 않는다.
- '무조건/반드시/꼭/~만/~제외'처럼 완화하면 안 된다고 명시한 조건의 필드명을
  hard_constraints에 저장한다.

[모호함·재질문]
- 시스템 정책으로 값이 확정되는 표현은 해당 필드에만 저장하고 assumptions에는 반복 기록하지 않는다.
- assumptions에는 사용자 발화만으로 확정할 수 없어 별도의 가정을 적용한 경우만 기록한다.
- 모호하지만 추천 진행이 가능한 내용은 ambiguous에 기록하되 needs_user_input=false로 둔다.
- 모호함이나 필수 정보 누락 때문에 핵심 결과가 크게 달라질 때만 needs_user_input=true로 둔다.
- needs_user_input=true이면 followup_question에 가장 중요한 질문 하나만 작성한다.
- 단순히 언급되지 않은 모든 필드를 missing에 넣지 않는다.
"""


def _human_messages(state: PipelineState) -> list[HumanMessage]:
    """내부 에이전트 메시지는 제외하고 사용자가 말한 내용만 반환한다."""
    return [
        message
        for message in state.get("messages", [])
        if isinstance(message, HumanMessage)
    ]


def _append_once(items: list[str], value: str) -> None:
    """같은 보정 내용이 재시도 과정에서 중복 저장되지 않게 한다."""
    if value not in items:
        items.append(value)


def _normalize_profile(profile: UserProfile, user_text: str) -> UserProfile:
    """LLM이 빠뜨리기 쉬운 정책성 메타데이터를 결정적으로 보완한다.

    주요 조건값은 LLM이 추출하되, 명시적인 필수 조건과 필드 간 일관성은
    원문을 기준으로 다시 확인한다.
    """
    assumptions = list(profile.assumptions)
    hard_constraints = list(profile.hard_constraints)

    # 통신사 유형을 명시적으로 한정한 표현만 완화 불가 조건으로 본다.
    carrier_type_required = (
        "알뜰폰만",
        "통신 3사만",
        "통신3사만",
        "대형 통신사만",
        "알뜰폰 제외",
    )
    if profile.carrier_type is not None and any(
        phrase in user_text for phrase in carrier_type_required
    ):
        _append_once(hard_constraints, "carrier_type")

    # 특정 통신사를 "~만"이라고 한정했을 때만 망 조건을 필수로 둔다.
    if profile.host_mno is not None and re.search(
        r"(?:SKT|KT|LG\s*U\+|LGU\+|LG유플러스)\s*(?:만|만으로)",
        user_text,
        flags=re.IGNORECASE,
    ):
        _append_once(hard_constraints, "host_mno")

    # 사용자가 무제한을 명시적으로 필수라고 표현한 경우만 강제한다.
    required_words = r"(?:무조건|반드시|꼭|필수)"
    if profile.data_unlimited is True and (
        re.search(rf"데이터\s*무제한[^.\n]*{required_words}", user_text)
        or re.search(rf"{required_words}[^.\n]*데이터\s*무제한", user_text)
    ):
        _append_once(hard_constraints, "data_unlimited")
    if profile.voice_unlimited is True and (
        re.search(rf"(?:통화|전화)\s*무제한[^.\n]*{required_words}", user_text)
        or re.search(rf"{required_words}[^.\n]*(?:통화|전화)\s*무제한", user_text)
    ):
        _append_once(hard_constraints, "voice_unlimited")
    if profile.sms_unlimited is True and (
        re.search(rf"문자\s*무제한[^.\n]*{required_words}", user_text)
        or re.search(rf"{required_words}[^.\n]*문자\s*무제한", user_text)
    ):
        _append_once(hard_constraints, "sms_unlimited")

    # 재질문이 필요하지 않다면 사용자에게 노출할 질문도 없어야 한다.
    followup_question = profile.followup_question if profile.needs_user_input else None

    return profile.model_copy(
        update={
            "assumptions": assumptions,
            "hard_constraints": hard_constraints,
            "followup_question": followup_question,
        }
    )


def profiling_node(state: PipelineState, config: RunnableConfig) -> dict:
    prompt = PROFILING_PROMPT + "\n\n" + feedback_block(state)
    messages = _human_messages(state)
    if not messages:
        messages = [HumanMessage(content="")]

    llm = get_llm(config).with_structured_output(UserProfile)
    profile: UserProfile = llm.invoke(
        [SystemMessage(content=prompt), *messages]
    )
    user_text = "\n".join(str(message.content) for message in messages)
    profile = _normalize_profile(profile, user_text)

    return {
        "profile": profile,
        "messages": [
            AIMessage(
                content=f"[profiling] {profile.model_dump_json(exclude_none=True)}",
                name="profiling",
            )
        ],
    }
