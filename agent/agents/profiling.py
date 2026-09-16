"""1단계 — 사용자 발화를 UserProfile로 구조화한다."""

from __future__ import annotations

import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from ..data import (
    find_plans_by_name,
    find_plans_mentioned_in_text,
    normalize_benefit_category,
)
from ..schemas import UserProfile
from ..state import PipelineState, feedback_block, get_llm, user_query
from ..usage import estimate_monthly_data_gb


# 값이 있으면 data.py에서 그대로 필터링할 명시 조건 필드다.
CONSTRAINT_FIELDS = (
    "budget_min_won",
    "budget_max_won",
    "min_data_gb",
    "max_data_gb",
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
    "wanted_benefit_categories",
    "min_discount_period_months",
)


PROFILING_PROMPT = """휴대폰 요금제 상담 요청을 UserProfile로 구조화하라.

[기본]
- 사용자가 명시한 조건만 추출하고, 상품 검색·필터링·존재 여부 판단은 하지 않는다.
- 언급하지 않은 값은 추측하지 말고 null로 둔다. 특히 통화·문자를 무제한으로 가정하지 않는다.
- 사용자가 다른 선택도 괜찮다고 한 조건은 필터 필드에 넣지 말고 필요하면 notes에 기록한다.

[수치 조건]
- 금액 단위(원/만원)가 붙은 수치만 예산으로 본다. 요금제명에 붙은 숫자는 금액이 아니다.
  '초이스90', '너겟59', '스마트 20GB' 의 숫자를 budget 필드로 옮기지 마라.
- N만원대 → budget_min_won=N0,000, budget_max_won=N9,999
- N만원 이하/이상 → budget_max_won/budget_min_won
- N만원 정도·내외·안팎 → N만원 ±5,000원
- 데이터 NGB 이상은 min_data_gb, 데이터 NGB 이하·미만·최대 NGB는 max_data_gb에 저장한다.
  데이터 NGB 정도·쯤·내외·전후처럼 목표량을 말하면 target_data_gb에 저장하고
  min_data_gb에는 복사하지 않는다.
  데이터 상한을 요청하면 무제한 요금제는 제외한다. QoS·소진 후 NMbps 이상, 테더링 NGB 이상은 각각 최소 필드에 저장한다.
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
- 포괄적인 혜택 유형은 wanted_benefit_categories에 다음 정식 카테고리명으로 저장한다.
  OTT·영상 스트리밍='영상/OTT', 음악·오디오='음악/오디오', 도서·전자책='도서/콘텐츠',
  외부 제휴 서비스='제휴서비스', 여러 종류 중 선택='복합/선택혜택', AI 교육·모의고사='교육/AI서비스', 멤버십='멤버십',
  스마트워치·태블릿='스마트기기', 추가 데이터='추가데이터', 사은품·페이백='사은품/페이백'.
- 넷플릭스·지니뮤직·밀리의서재처럼 특정 서비스나 혜택을 지정하면 wanted_benefits에 저장한다.
  혜택 이름만 넣고 '포함/혜택/되는' 같은 수식어는 뺀다. '유튜브 프리미엄 포함된' → '유튜브 프리미엄'
- '음악 혜택'은 wanted_benefit_categories=['음악/오디오']이고 wanted_benefits에는 넣지 않는다.
  '지니뮤직 혜택'은 wanted_benefits=['지니뮤직']이고 카테고리를 임의로 추가하지 않는다.
- 혜택 조건이 여러 개일 때 '그리고/모두/동시에'는 benefit_match_mode='all'로 저장한다.
  '또는/둘 중 하나/아무거나/하나라도'는 benefit_match_mode='any'로 저장한다.
  연결 표현이 없거나 혜택 조건이 하나뿐이면 기본값 all을 유지한다.
- 개별 서비스와 카테고리가 섞여도 같은 규칙을 적용한다.
  '디즈니플러스 또는 도서 혜택'은 wanted_benefits=['디즈니플러스'],
  wanted_benefit_categories=['도서/콘텐츠'], benefit_match_mode='any'다.
- 단순히 넷플릭스·유튜브 등을 시청한다고 말한 것은 혜택 요구가 아니다.
  '포함/혜택/되는 요금제'처럼 상품 혜택을 원할 때만 wanted_benefits에 저장한다.

[이용 패턴]
- GB 수치 없이 앱 이용 시간을 말하면 app_usages에 앱별 항목을 저장한다.
  각 항목은 service, daily_hours, mode로 구성하고 mode는 사용자가 말하지 않으면 null로 둔다.
- 앱 이름은 다음 키로 정규화한다.
  유튜브=youtube, 넷플릭스=netflix, 디즈니+=disney_plus, 틱톡=tiktok,
  인스타그램=instagram, 인스타그램 릴스·릴스=instagram_reels, 스포티파이=spotify,
  구글 지도·내비게이션=google_maps, 줌=zoom, 왓츠앱=whatsapp,
  포켓몬GO=pokemongo_game, 리그오브레전드=league_of_legend_game,
  배틀그라운드=battleground_game, 클래시로얄=clashroyale_game,
  포트나이트=fortnite_game, 콜오브듀티=callofduty_game,
  브롤스타즈=brawlstars_game, 스타듀밸리=stardewvalley_game,
  그 밖의 모바일 게임=mobile_game, 앱 미지정 영상=generic_video,
  앱 미지정 숏폼=generic_shortform. 티빙은 전용 계수가 없으므로 generic_video로 저장한다.
- 사용자가 화질·모드를 말하면 해당 app_usages 항목의 mode에 저장한다.
  유튜브: 240p=low_240p, 480p=sd_480p, 720p·HD=hd_720p,
  1440p=fullhd_1440p, 4K=uhd_4k.
  넷플릭스: 저화질=low, SD=sd, HD=hd, 4K=uhd_4k.
  틱톡: 일반=standard, 고화질=hd.
  인스타그램: 사진 피드=photo_feed, 스토리·릴스=story_reels_mix, 라이브=live.
  스포티파이: 일반=normal_96kbps, 매우 높음=very_high_320kbps, 무손실=lossless_hifi.
  Zoom: 오디오=audio_only, 1:1 영상=one_to_one_sd, 그룹 HD=group_hd.
- 화질을 말하지 않으면 mode=null로 둔다. usage.py가 기본 모드를 적용한다.
- 앱별 시간을 저장한 경우 기존 daily_video_hours/daily_shortform_hours/daily_game_hours에 중복 저장하지 않는다.
- 앱이나 화질이 특정되지 않은 생활패턴은 smartchoice_usage_pattern으로 저장한다.
  와이파이 위주=wifi_primary, 웹서핑·음악 위주=web_music_primary,
  영상 하루 약 1시간=video_1h, 영상 하루 약 2시간=video_2h,
  영상 하루 3시간 이상=video_3h_plus.
- smartchoice_usage_pattern을 저장한 경우 generic_video나 daily_video_hours를 중복 저장하지 않는다.
- 앱을 언급했다는 이유만으로 min_qos_mbps를 만들지 않는다. 사용자가 소진 후 속도를 직접 요구한 경우에만 저장한다.

[기준 요금제·비교]
- 현재·기존 요금제 이름은 reference_plan_name에 저장한다.
- 실제 상품 고유명사가 있을 때만 reference_plan_name을 채운다.
  '월 5만원에 데이터 50GB 사용 중', '3만원짜리', '데이터 무제한 요금제'처럼
  현재 스펙만 설명한 문장은 상품명이 아니므로 reference_plan_name=null이다.
- 상품명의 숫자·5G/LTE·GB·+까지 생략하지 말고 사용자가 말한 전체 이름을 보존한다.
  예: 'SKT 다이렉트5G 69'의 reference_plan_name은 '다이렉트5G 69'다.
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


# 금액다운 표현: 숫자 뒤에 원/만/천 이 붙은 것만 예산으로 인정한다.
_MONEY = re.compile(r"[\d,]+\s*(?:원|만|천)")


def _drop_phantom_budget(profile: UserProfile, query: str) -> UserProfile:
    """발화에 금액 표현이 없는데 잡힌 예산은 버린다.

    '초이스90', '너겟59' 처럼 요금제명에 붙은 숫자를 LLM 이 'N만원대' 로 읽어
    budget_max_won=9,999 같은 값을 지어내고, 그 예산이 Hard Constraint 가 되어
    후보를 0건으로 만든 적이 있다. 프롬프트 지시만으로는 재발했다.

    ponytail: '예산 30000' 처럼 단위 없는 표기는 같이 버려진다. 후보가 넓어질 뿐
    깨지지는 않으므로 감수한다. 단위 없는 금액이 흔해지면 파서를 붙인다.
    """
    if profile.budget_min_won is None and profile.budget_max_won is None:
        return profile
    if _MONEY.search(query or ""):
        return profile
    return profile.model_copy(update={"budget_min_won": None, "budget_max_won": None})


_NAMED_VIDEO_APP = re.compile(
    r"유튜브|youtube|넷플릭스|netflix|디즈니\s*(?:플러스|\+)|"
    r"티빙|tving|틱톡|tiktok|인스타(?:그램)?\s*릴스|릴스",
    re.IGNORECASE,
)

# LLM 구조화 출력에 앞뒤 공백·조사가 붙은 데이터 상한을 놓쳐도, 사용자가 직접
# 말한 '100GB 이하'는 필터에서 빠지지 않게 한다.
_EXPLICIT_DATA_MAX_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:g|gb)\s*(?:이하|최대)", re.IGNORECASE
)


def _smartchoice_usage_pattern(query: str) -> str | None:
    """정확한 영상 앱명이 없는 생활패턴을 스마트초이스 구간으로 분류한다."""
    text = (query or "").casefold()
    if _NAMED_VIDEO_APP.search(text):
        return None

    wifi = r"(?:와이파이|wi-?fi)"
    primary = r"(?:주로|위주|대부분|많이)"
    if re.search(wifi + r".{0,12}?" + primary, text) or re.search(
        primary + r".{0,12}?" + wifi, text
    ):
        return "wifi_primary"

    web_music = r"(?:웹\s*서핑|인터넷\s*검색|음악\s*(?:듣기|감상|스트리밍))"
    if re.search(web_music + r".{0,20}?" + primary, text) or re.search(
        primary + r".{0,20}?" + web_music, text
    ):
        return "web_music_primary"

    hour_token = r"[123](?:\.0)?|한|두|세"
    hour_patterns = (
        rf"(?:영상|동영상).{{0,18}}?(?:하루(?:에)?\s*)?(?:약\s*)?"
        rf"(?P<hours>{hour_token})\s*시간(?P<plus>\s*이상)?",
        rf"(?:하루(?:에)?\s*)?(?:약\s*)?(?P<hours>{hour_token})\s*시간"
        r"(?P<plus>\s*이상)?.{0,18}?(?:영상|동영상)",
    )
    korean_hour_numbers = {"한": 1.0, "두": 2.0, "세": 3.0}
    for pattern in hour_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        token = match.group("hours")
        hours = korean_hour_numbers[token] if token in korean_hour_numbers else float(token)
        if hours >= 3:
            return "video_3h_plus"
        if hours == 2:
            return "video_2h"
        if hours == 1:
            return "video_1h"
    return None


def _apply_smartchoice_usage_rule(profile: UserProfile, query: str) -> UserProfile:
    """앱명이 없는 발화에는 LLM 추정보다 스마트초이스 분류를 우선한다."""
    pattern = _smartchoice_usage_pattern(query)
    if pattern is None:
        return profile
    updates: dict[str, object] = {"smartchoice_usage_pattern": pattern}
    if pattern.startswith("video_"):
        updates.update(
            daily_video_hours=None,
            app_usages=[],
        )
    return profile.model_copy(update=updates)


def _apply_explicit_data_max(profile: UserProfile, query: str) -> UserProfile:
    """자연어에 명시된 데이터 상한을 구조화 출력에 확정적으로 반영한다."""
    matches = list(_EXPLICIT_DATA_MAX_RE.finditer(query))
    if not matches:
        return profile
    max_data_gb = float(matches[-1].group(1))
    return profile.model_copy(update={"max_data_gb": max_data_gb})


_GENERIC_REFERENCE_RE = re.compile(
    r"(?:\d[\d,.]*\s*(?:원|만원|천원|g|gb|기가|분|mbps)|"
    r"데이터|통화|무제한|짜리|가격|요금)",
    re.IGNORECASE,
)
_REFERENCE_SPEC_FIELDS = (
    "reference_fee_won",
    "reference_data_gb",
    "reference_data_unlimited",
    "reference_voice_minutes",
    "reference_voice_unlimited",
    "reference_qos_mbps",
)


def _repair_reference_plan_name(profile: UserProfile, query: str) -> UserProfile:
    """스펙 설명을 상품명으로 오인한 값을 버리고 문장 속 실제 DB명을 복구한다."""
    name = (profile.reference_plan_name or "").strip()
    if not name:
        return profile

    direct = find_plans_by_name(name)
    mentioned = find_plans_mentioned_in_text(query)
    has_reference_spec = any(
        getattr(profile, field) is not None for field in _REFERENCE_SPEC_FIELDS
    )
    if has_reference_spec and _GENERIC_REFERENCE_RE.search(name) and not mentioned:
        return profile.model_copy(update={"reference_plan_name": None})
    if len(direct) == 1:
        return profile
    if mentioned and (not direct or len(direct) > 1):
        return profile.model_copy(update={"reference_plan_name": mentioned[0]["plan_name"]})
    return profile


def _normalize_benefit_requests(profile: UserProfile) -> UserProfile:
    """예전 방식으로 추출된 '음악'·'OTT'를 카테고리 요청으로 이관한다."""
    categories = list(profile.wanted_benefit_categories or [])
    benefits: list[str] = []
    for benefit in profile.wanted_benefits or []:
        category = normalize_benefit_category(benefit)
        if category:
            categories.append(category)
        else:
            benefits.append(benefit)
    categories = list(dict.fromkeys(categories))
    return profile.model_copy(
        update={
            "wanted_benefits": benefits or None,
            "wanted_benefit_categories": categories or None,
        }
    )


def _normalize_profile(profile: UserProfile) -> UserProfile:
    """스키마 값으로 Hard Constraint와 재질문 상태를 결정한다."""
    profile = _normalize_benefit_requests(profile)
    if profile.mvno_brand and profile.carrier_type != "MVNO":
        profile = profile.model_copy(update={"carrier_type": "MVNO"})

    usage_hours = {usage.service: usage.daily_hours for usage in profile.app_usages}
    usage_modes = {usage.service: usage.mode for usage in profile.app_usages if usage.mode}
    estimated_gb, usage_notes = estimate_monthly_data_gb(
        profile.daily_video_hours,
        profile.daily_shortform_hours,
        profile.daily_game_hours,
        usage_hours,
        usage_modes,
        profile.smartchoice_usage_pattern,
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


# 데이터와 요금 둘 다 못 잡으면 후보를 좁힐 수 없다. 전체에서 5건을 뽑는 추천은 의미가 없으므로
# 이 경우에만 추천 전에 되묻는다. (그 외에는 부족해도 일단 추천하고 질문을 함께 낸다)
CORE_MISSING_QUESTION = (
    "추천 범위를 좁히려면 두 가지 중 하나는 필요합니다. "
    "월 데이터 사용량(예: 20GB, 무제한)이나 희망 월 예산(예: 3만원 이하) 중 아시는 대로 알려주세요."
)


def core_signal_missing(profile: UserProfile | None) -> bool:
    """데이터·요금 신호가 모두 없으면 True."""
    if profile is None:
        return True
    has_data = any(
        value is not None
        for value in (
            profile.min_data_gb,
            profile.target_data_gb,
            profile.max_data_gb,
            profile.data_unlimited,
            profile.estimated_monthly_data_gb,
            profile.daily_video_hours,
            profile.daily_shortform_hours,
            profile.daily_game_hours,
            profile.reference_data_gb,
        )
    )
    has_data = has_data or bool(profile.app_usages)
    has_data = has_data or profile.smartchoice_usage_pattern is not None
    has_fee = any(
        value is not None
        for value in (profile.budget_max_won, profile.budget_min_won, profile.reference_fee_won)
    )
    # 기준 요금제명만 말해도 후보를 좁힐 수 있다. 금액·데이터는 recommend 가 DB 에서 읽는다.
    has_reference = profile.reference_plan_name is not None
    return not (has_data or has_fee or has_reference)


def profiling_node(state: PipelineState, config: RunnableConfig) -> dict:
    messages = [
        message
        for message in state.get("messages", [])
        if isinstance(message, HumanMessage)
    ] or [HumanMessage(content="")]

    prompt = PROFILING_PROMPT + "\n\n" + feedback_block(state)
    llm = get_llm(config).with_structured_output(UserProfile)
    query = user_query(state)
    profile = _normalize_profile(
        _repair_reference_plan_name(
            _apply_smartchoice_usage_rule(
                _apply_explicit_data_max(
                    _drop_phantom_budget(
                        llm.invoke([SystemMessage(content=prompt), *messages]), query
                    ),
                    query,
                ),
                query,
            ),
            query,
        ),
    )
    if core_signal_missing(profile):
        profile = profile.model_copy(
            update={
                "needs_user_input": True,
                # 이 분기의 원인은 하나(데이터·요금 신호 없음)라 무엇이 필요한지 콕 집어 묻는
                # 고정 문구가 LLM 이 만든 막연한 질문보다 낫다.
                "followup_question": CORE_MISSING_QUESTION,
            }
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


if __name__ == "__main__":
    # 요금제명 숫자에서 나온 예산은 버린다 (실제로 후보를 0건으로 만들던 값)
    phantom = UserProfile(
        budget_min_won=0, budget_max_won=9999, reference_plan_name="초이스90"
    )
    cleaned = _drop_phantom_budget(phantom, "지금 KT 초이스90 쓰는데 이거보다 싼 걸로 바꾸고 싶어")
    assert cleaned.budget_min_won is None and cleaned.budget_max_won is None
    assert cleaned.reference_plan_name == "초이스90"

    # 진짜 예산은 지킨다
    for text in ("월 3만원 이하로", "예산 30,000원", "5천원짜리 있어?", "3만원대"):
        kept = _drop_phantom_budget(UserProfile(budget_max_won=30000), text)
        assert kept.budget_max_won == 30000, text

    # 예산이 없으면 아무것도 하지 않는다
    assert _drop_phantom_budget(UserProfile(), "초이스90").budget_max_won is None

    # 기준 요금제명만 있어도 후보를 좁힐 수 있으므로 되묻지 않는다
    assert core_signal_missing(UserProfile()) is True
    assert core_signal_missing(UserProfile(reference_plan_name="초이스90")) is False
    assert core_signal_missing(UserProfile(budget_max_won=30000)) is False
    assert core_signal_missing(UserProfile(max_data_gb=100)) is False
    assert core_signal_missing(UserProfile(data_unlimited=False)) is False
    assert _apply_explicit_data_max(UserProfile(), "데이터 100GB이하").max_data_gb == 100

    # hard_constraints 는 값이 있는 필터 필드만
    normalized = _normalize_profile(UserProfile(budget_max_won=30000, voice_unlimited=True))
    assert set(normalized.hard_constraints) == {"budget_max_won", "voice_unlimited"}

    music = _normalize_profile(UserProfile(wanted_benefits=["음악 혜택"]))
    assert music.wanted_benefits is None
    assert music.wanted_benefit_categories == ["음악/오디오"]
    assert "wanted_benefit_categories" in music.hard_constraints
    genie = _normalize_profile(UserProfile(wanted_benefits=["지니뮤직"]))
    assert genie.wanted_benefits == ["지니뮤직"]
    assert genie.wanted_benefit_categories is None

    vague_video = UserProfile(
        daily_video_hours=1,
        app_usages=[{"service": "youtube", "daily_hours": 1}],
    )
    for text, expected_pattern, expected_gb in (
        ("하루 한시간 영상을 봐", "video_1h", 37.0),
        ("영상을 하루 두 시간 정도 봐", "video_2h", 80.0),
        ("하루 3시간 이상 동영상을 봐", "video_3h_plus", 90.0),
    ):
        corrected = _normalize_profile(_apply_smartchoice_usage_rule(vague_video, text))
        assert corrected.smartchoice_usage_pattern == expected_pattern, text
        assert corrected.app_usages == [], text
        assert corrected.estimated_monthly_data_gb == expected_gb, text

    named_video = _normalize_profile(
        _apply_smartchoice_usage_rule(
            UserProfile(app_usages=[{"service": "youtube", "daily_hours": 1}]),
            "유튜브 하루 한시간",
        )
    )
    assert named_video.smartchoice_usage_pattern is None
    assert named_video.estimated_monthly_data_gb == 64.4
    assert named_video.min_qos_mbps is None

    print("self-check ok")
