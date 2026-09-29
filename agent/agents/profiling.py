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
from ..schemas import AppUsage, UserProfile
from ..state import PipelineState, feedback_block, get_profile_llm, user_query
from ..usage import estimate_monthly_data_gb, required_qos_mbps


# 값이 있으면 data.py에서 그대로 필터링할 명시 조건 필드다.
CONSTRAINT_FIELDS = (
    "budget_min_won",
    "budget_max_won",
    "min_data_gb",
    "min_monthly_base_data_gb",
    "min_daily_data_gb",
    "max_data_gb",
    "data_unlimited",
    "require_full_unlimited",
    "min_qos_mbps",
    "requires_qos",
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
- 'N만원 이하'는 상한만 말한 것이다. budget_min_won 은 null로 둔다. 상한과 같은 값을
  하한에 복사하면 정확히 그 금액인 상품만 남는다. 하한은 '이상/부터/N만원대'처럼
  사용자가 직접 아래쪽 경계를 말했을 때만 채운다.
- N만원 정도·내외·안팎 → N만원 ±5,000원
- 데이터 NGB 이상은 min_data_gb, 데이터 NGB 이하·미만·최대 NGB는 max_data_gb에 저장한다.
  '매일/하루에 NGB씩 제공·주는 요금제'는 min_daily_data_gb=N으로 저장한다.
  '월 기본 11GB + 매일 2GB', '한 달에 10GB 그리고 매일 2GB 제공'은
  min_monthly_base_data_gb와 min_daily_data_gb에 각각 저장한다.
  min_monthly_base_data_gb는 월 기본량과 일 제공량을 함께 요구할 때만 사용한다.
  '월 기본 11GB 이상'만 말했으면 일반 min_data_gb=11로 저장한다.
  일 제공량을 min_data_gb에 복사하지 않는다. '하루 NGB를 쓴다/사용한다'는 이용량이지
  일 제공형 요금제 조건이 아니므로 min_daily_data_gb에 저장하지 않는다.
  데이터 NGB 정도·쯤·내외·전후처럼 목표량을 말하면 target_data_gb에 저장하고
  min_data_gb에는 복사하지 않는다.
  데이터 상한을 요청하면 무제한 요금제는 제외한다. QoS·소진 후 NMbps 이상, 테더링 NGB 이상은 각각 최소 필드에 저장한다.
- 'QoS 있는/제공되는 요금제', '데이터 소진 후에도 사용할 수 있는 요금제'처럼 속도 수치 없이
  소진 후 데이터 사용 가능 여부를 요구하면 requires_qos=true로 저장한다.
  'QoS는 상관없음/없어도 됨'은 필수조건이 아니므로 requires_qos=null로 둔다.
- 데이터 무제한은 data_unlimited=true, 통화 N분 이상과 통화 무제한은 해당 통화 필드에 저장한다.
  '완전 무제한', '속도 제한 없는 무제한'처럼 QoS형(기본량 소진 후 속도 제한)을 명시적으로
  배제할 때만 require_full_unlimited=true를 함께 저장한다. 그냥 '무제한'은 null로 둔다.
- 무제한 상품을 명시적으로 제외할 때만 해당 unlimited 필드를 false로 저장한다.
- 문자는 sms_unlimited만 구조화한다. 문자 건수 조건은 필드를 만들지 말고 notes에 기록한다.

[통신사·상품군]
- 통신 3사만/알뜰폰 제외 → carrier_type=MNO, 알뜰폰만 → MVNO
- 알뜰폰 포함·알뜰폰도 괜찮음은 유형 제한이 아니므로 carrier_type=null
- 기본 추천 대상은 알뜰폰이다. '통신 3사도 포함', '통신사 상관없이 전체에서', '추천 범위(알뜰폰) 조건은 빼고'처럼
  통신 3사 상품까지 넓혀 달라고 명시한 경우에만 include_mno=true, 그 외에는 null.
  현재 쓰는 요금제가 통신 3사라는 말은 넓혀 달라는 뜻이 아니다(reference_plan_name 에만 저장).
- '지금 SKT ○○ 요금제를 쓰고 있다'처럼 **현재 쓰는** 통신사·요금제를 말한 것은 찾는 상품의 조건이 아니다.
  이때 carrier_type 과 host_mno 는 null 로 두고 reference_plan_name 에만 저장한다.
  'SKT 안에서', 'SKT 요금제 중에서', '같은 망으로'처럼 찾는 범위를 말했을 때만 carrier_type/host_mno 를 채운다.
- 일반 SKT/KT/LGU+ 요청 → carrier_type=MNO와 host_mno
- 특정 망 알뜰폰 요청 → carrier_type=MVNO와 host_mno
- LG유플러스/LG U+는 LGU+로 정규화한다.
- 특정 알뜰폰 브랜드는 mvno_brand에 저장한다.
  KT엠모바일 → carrier_type=MVNO, host_mno=KT, mvno_brand=KT엠모바일
- LTE/5G만 보겠다는 조건은 network_gen에 저장한다.
- LTE/5G를 우선하거나 선호한다고 했고 다른 세대도 가능하면 network_preference에 저장하고,
  network_gen은 null로 둔다. 우선은 순위 조정일 뿐 다른 세대를 후보에서 빼지 않는다.
- "통신 세대 우선순위는 LTE/5G로 하고, 다른 세대도 후보에 포함" 같은 화면 선택 문장은
  network_preference로 확정한다.

[연령·혜택]
- 청년/청소년/키즈/시니어·어르신/군인·현역병사를 각각
  '만 34세 이하'/'만 18세 이하'/'만 12세 이하'/'만 65세 이상'/'현역병사'로 정규화한다.
- '청년 요금제', '20대 청년 요금제', '청년 전용 요금제'처럼 청년 상품군을
  명시적으로 요청하면 age_condition='만 34세 이하'로 저장한다.
- '나는 20대인데 추천해줘'처럼 나이만 밝히고 청년 상품군을 요청하지 않은 경우에는
  청년 전용 상품만 원한다고 단정하지 말고 age_condition을 설정하지 않는다.
- 사용자가 나이를 밝히면 age_condition과 별개로 user_age에 만 나이를 저장한다.
  '20대', '30대'처럼 구간만 알면 user_age=null로 두고 만 나이가 필요한 전용 상품은 제외한다.
  '스물다섯', '25살', '만 24세'처럼 정확한 나이는 그대로 넣는다.
  user_age는 연령 전용 요금제의 가입 자격 판정에만 쓰이므로 전용 상품을 원하는지와 무관하게 채운다.
- 포괄적인 혜택 유형은 wanted_benefit_categories에 다음 정식 카테고리명으로 저장한다.
  OTT·영상 스트리밍='영상/OTT', 음악·오디오='음악/오디오', 도서·전자책='도서/콘텐츠',
  외부 제휴 서비스='제휴서비스', 여러 종류 중 선택='복합/선택혜택', AI 교육·모의고사='교육/AI서비스', 멤버십='멤버십',
  스마트기기 혜택 전반(워치·태블릿·회선 포함)='스마트기기',
  기기값·할부금 할인='스마트기기', 스마트기기 회선 요금·데이터쉐어링을 특정하면
  '스마트기기 회선/데이터쉐어링',
  추가 데이터='추가데이터', 페이백·캐시백='페이백', 포인트·적립='포인트/적립',
  상품권·사은품='상품권/사은품', 쿠폰='쿠폰/할인', 무료 유심·배송비='유심/배송비',
  요금 할인='요금할인', 로밍='로밍', 보험·안심='보험/안심'.
- '페이백'은 상품권·네이버페이 포인트·쿠폰·무료 유심과 다른 유형이다.
  이름에 페이백/캐시백 지급이 확인되지 않으면 페이백으로 추측하지 않는다.
  '사은품이나 페이백', '사은품/페이백'은 wanted_benefit_categories에
  ['상품권/사은품', '페이백']을 넣고 benefit_match_mode='any'로 둔다.
  '사은품과 페이백 둘 다'는 같은 두 유형에 benefit_match_mode='all'을 쓴다.
  '사은품/페이백'이라는 통합 분류값은 프로필에 저장하지 않는다.
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
- 특정 혜택명이나 유형 없이 '혜택 좋은/더 괜찮은/우선/선호해/많은 순/다양한 순'이라고만 하면
  혜택의 좋고 나쁨이나 개수를 임의로 평가하지 않는다. needs_user_input=true로 두고
  원하는 혜택 유형을 질문한다.
- 반대로 '괜찮은 요금제/좋은 상품/나은 플랜'은 혜택 요청이 아니라 일반적인 비교 표현이다.
  이 표현만으로 혜택 유형을 묻지 말고, 현재 요금제·데이터·가격 정보가 충분하면 추천을 진행한다.
- 'OTT 혜택이 좋은 요금제'처럼 유형을 함께 말하면 해당 유형을 필수 혜택으로 저장하고
  comparison_goals의 better로 해석하지 않는다.

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
- 앱을 언급했다는 이유만으로 min_qos_mbps를 만들지 않는다. 사용자가 소진 후에도 해당 앱·화질을
  이용하고 싶다고 직접 요구한 경우에만 앱·화질에 필요한 최소 속도를 저장한다.

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
  '지금 월 3만원 내고 있다'는 reference_fee_won=30000이다. budget_max_won이 아니다.
  같은 현재 요금제명에 가격 조건이 여러 개라 후속으로 '월 7,700원짜리'처럼 답하면
  그 금액은 추천 예산이 아니라 현재 요금제의 reference_fee_won이다.
  현재 납부액을 예산 상한으로 옮기면 지금보다 싼 상품만 후보가 되어, 바꾸는 게 나은지를
  물은 사용자에게 유지가 낫다는 답을 아예 못 주게 된다.
- 비교 목적은 cheaper/more_data/faster_qos/similar/better 중 해당 값을 comparison_goals에 저장한다.
- 비교 기준값을 현재 추천 조건 필드에 복사하지 않는다. 실제 기준 상품 조회와 비교는 Recommend가 한다.

[할인 기간]
- 1년 이상 할인 → min_discount_period_months=12
- 1년 넘게 할인 → min_discount_period_months=13

[우선순위·Hard Constraint]
- priorities에는 사용자가 말한 정렬 기준 중 price/data/qos/benefit만 저장한다.
  없으면 null이며 시스템 기본값을 넣지 않는다.
- '선호해/선호하고/선호하며/중요하게/우선/위주'는 정렬 선호다.
  여러 축을 '데이터와 테더링을 선호해'처럼 함께 말하면 priorities_ordered=false,
  '1순위는 데이터, 그다음은 테더링'처럼 순서를 밝히면 true다.
- 예산·데이터량 같은 조건 문장은 정렬 기준이 아니다. '3만원 이하로 추천해줘'는 budget_max_won만
  채우고 priorities에 price를 넣지 않는다. '제일 싼 걸로', '가격을 최우선으로'처럼 순서를
  직접 요구할 때만 priorities에 넣는다.
- tethering·voice·sms·carrier는 사용자 선호 가중치 축이 아니다. '테더링/통화/문자가 중요하다'만으로
  voice_unlimited·sms_unlimited를 추측하지 말고, '통화 무제한이 필수', '문자 무제한만'처럼
  구체적으로 요구한 경우에만 필터 조건으로 저장한다. '테더링 20GB 이상'도
  min_tethering_gb 필터로는 계속 지원한다.
  선호 통신사 이름 없이 '통신사가 중요하다'고만 하면 carrier 조건도 추측하지 않는다.
  선호 통신사 이름 없이 '통신사가 중요하다'고만 하면 carrier 조건도 추측하지 않는다.
- 구체적인 금액·사용량·통신사·혜택 등 필터 조건은 기본적으로 Hard Constraint다.
- 다만 말투가 희망이면 Hard Constraint가 아니다. 값은 해당 필드에 그대로 저장하되
  hard_constraints 목록에서는 뺀다. 그 조건을 만족하지 않는 상품도 후보로 남기고
  점수에서만 유리하게 본다.
  희망: '가능하면', '되도록', '웬만하면', '있으면 좋겠다', '~면 좋겠어', '선호해', '괜찮을 것 같아'
  필수: '~만', '반드시', '꼭', '무조건', '있어야 해', '필수', '아니면 안 돼'
  예) '넷플릭스 포함이면 좋겠어' → wanted_benefits=['넷플릭스'], hard_constraints에 넣지 않음
      '넷플릭스 포함 요금제만 원해' → wanted_benefits=['넷플릭스'], hard_constraints에 포함
      '넷플릭스 이용 중이야' → 혜택 요구가 아니다. wanted_benefits를 채우지 않는다.
      '혜택은 상관없어' → 혜택 필드를 모두 비우고 notes에만 적는다.
- '가능하면 데이터가 넉넉했으면 좋겠어'처럼 수치 없는 여유 희망은 min_data_gb를 만들지 말고
  priorities에 data를 넣어 가중치로만 반영한다.
- 후속 발화에서 '소진 후 최소 속도 조건은 빼고', 'QoS 조건을 제외해줘'처럼 기존 조건을
  명시적으로 풀면 이전 발화의 min_qos_mbps를 유지하지 말고 null로 둔다. 최신 지시가 우선이다.
- 필수 조건이라는 이유로 같은 축을 priorities에도 넣지 않는다. 두 곳은 서로 다른 뜻이다
  (필터 대 정렬 가중치).
- 가장 싼 것·데이터 많은 순 같은 정렬 표현은 조건 필드가 아니라 priorities에만 저장한다.
- 가격대·연령·브랜드 변환처럼 정해진 정규화는 assumptions에 반복 기록하지 않는다.

[모호함·재질문]
- assumptions에는 사용자 발화만으로 확정할 수 없어 실제로 별도 가정을 적용한 경우만 기록한다.
- 모호해도 진행 가능하면 ambiguous만 기록하고 needs_user_input=false로 둔다.
- 핵심 결과가 크게 달라질 때만 needs_user_input=true로 두고 followup_question 하나를 작성한다.
- 언급되지 않은 모든 필드를 missing에 넣지 않는다.
"""


AGE_UNKNOWN_ASSUMPTION = (
    "나이를 알 수 없어 청년·시니어·키즈 같은 연령 전용 요금제는 후보에서 제외했습니다"
)


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


# "3만원 이하" 는 30,000원까지 포함이다. LLM 이 'N만원대' 규칙(N9,999)을 섞어 쓰면서
# 정확히 30,000원인 상품 8건이 조용히 탈락한다. 상한은 코드로 못 박는다.
_BUDGET_MAX_RE = re.compile(
    r"(?:(?P<amount>[\d,]+(?:\.\d+)?)\s*(?P<unit>만원|천원|원)|"
    r"(?P<implicit_unit>(?<![가-힣\d,])(?:만원|천원)))"
    r"\s*(?:이하|까지|안(?:쪽)?|미만)",
    re.IGNORECASE,
)
_BUDGET_UNIT = {"만원": 10_000, "천원": 1_000, "원": 1}

# 하한을 말하는 표현. 'N만원대'는 하한과 상한을 동시에 뜻하므로 여기 포함한다.
_BUDGET_MIN_RE = re.compile(
    r"(?:[\d,]+(?:\.\d+)?\s*(?:만원|천원|원)|"
    r"(?<![가-힣\d,])(?:만원|천원))\s*(?:이상|부터|넘|초과)"
    r"|[\d,]+\s*만원\s*대"
    r"|최소\s*[\d,]+\s*(?:만원|천원|원)",
    re.IGNORECASE,
)


def _repair_budget_bounds(profile: UserProfile, query: str) -> UserProfile:
    """'N만원 이하'의 경계를 확정하고 사용자가 말하지 않은 하한을 지운다.

    '3만원 이하로 추천해줘'에 LLM 이 'N만원대' 규칙을 섞어 쓰면 budget_min_won=30,000 이
    함께 붙는다. 상한을 30,000 으로 못 박고 나면 하한과 상한이 같아져 정확히 30,000원인
    상품만 남는다(실측: 후보 578건 -> 4건). 사용자는 하한을 말한 적이 없다.
    """
    updates: dict[str, object] = {}
    matches = list(_BUDGET_MAX_RE.finditer(query or ""))
    if matches:
        match = matches[-1]
        amount = (match.group("amount") or "1").replace(",", "")
        unit = match.group("unit") or match.group("implicit_unit")
        limit = int(float(amount) * _BUDGET_UNIT[unit])
        # '미만'만 경계를 뺀다. '이하/까지'는 그 금액을 포함한다.
        if matches[-1].group(0).rstrip().endswith("미만"):
            limit -= 1
        updates["budget_max_won"] = limit
        if profile.budget_min_won is not None and not _BUDGET_MIN_RE.search(query or ""):
            updates["budget_min_won"] = None
    if profile.budget_min_won == 0:
        updates["budget_min_won"] = None
    return profile.model_copy(update=updates) if updates else profile


# 순서를 직접 요구하는 표현. 조건 문장("3만원 이하로")은 정렬 기준이 아니다.
_PRIORITY_PHRASE_RE = re.compile(
    r"(?:제일|가장|최대한|무조건)\s*(?:싼|저렴|많|빠른|좋)"
    r"|(?:싼|저렴한|비싼|많은|빠른|좋은)\s*(?:것|거|순|순서|쪽)"
    r"|순으로|순서대로|(?:1|2|3)\s*순위|첫째|둘째|셋째"
    r"|우선|최우선|중요(?:해|하|시)|중심으로|위주로|따지"
    r"|선호(?:해|하고|하며|해서|하는|하는데|하지만|합니다|한다|함)"
    r"|가성비",
    re.IGNORECASE,
)


# 나이 표현. 연령 전용 요금제(331건)의 가입 자격 판정에만 쓴다.
_AGE_BAND_RE = re.compile(r"(\d0)\s*대")
_AGE_EXACT_RE = re.compile(r"(?:만\s*)?(\d{1,2})\s*(?:세|살)")


def _apply_user_age(profile: UserProfile, query: str) -> UserProfile:
    """사용자가 밝힌 나이를 확정적으로 보존한다. 없으면 건드리지 않는다."""
    text = query or ""
    exact = list(_AGE_EXACT_RE.finditer(text))
    if exact:
        age = int(exact[-1].group(1))
        return profile.model_copy(update={"user_age": age}) if 5 <= age <= 99 else profile
    band = _AGE_BAND_RE.search(text)
    if band:
        return profile.model_copy(update={"user_age": None, "age_condition": None,
            "assumptions": [*profile.assumptions, "연령대만으로 만 나이를 확정하지 않아 연령 전용 상품은 제외했습니다."]})
    return profile


def _drop_inferred_priorities(profile: UserProfile, query: str) -> UserProfile:
    """정렬 요구가 없는데 잡힌 priorities 를 버린다.

    '월 3만원 이하로 추천해주세요' 가 priorities=['price'] 로 추출되면 가중치가 한 축으로
    쏠려, 128GB 가 필요한 사용자에게 10GB/10원 요금제가 1순위로 올라간다. 예산은 제약이지
    정렬 기준이 아니다.
    """
    if not profile.priorities:
        return profile
    if any(_axes_in_priority_clauses(line) for line in (query or "").splitlines()):
        return profile
    return profile.model_copy(update={"priorities": None, "priorities_ordered": False})


# 우선순위 표현이 가리키는 축. UserProfile.priorities 의 Literal 과 1:1 이다.
_PRIORITY_AXIS_PATTERNS = (
    ("price", re.compile(r"가격|요금|저렴|싼|비용|가성비")),
    ("data", re.compile(r"데이터|제공량|용량")),
    ("qos", re.compile(r"속도|qos|소진\s*후", re.IGNORECASE)),
    ("benefit", re.compile(r"혜택|사은품|페이백|ott", re.IGNORECASE)),
)

def _first_priority_axis(text: str) -> tuple[int, str] | None:
    found = [
        (match.start(), name)
        for name, pattern in _PRIORITY_AXIS_PATTERNS
        if (match := pattern.search(text)) is not None
    ]
    return min(found, default=None)


def _explicit_priority_axes(utterance: str) -> list[str]:
    """1순위·2순위 또는 '그다음' 표현에서 순서가 확실한 축만 반환한다."""
    ranked: list[tuple[int, str]] = []
    ordinal_matches = list(
        re.finditer(r"(?P<rank>[123])\s*순위|첫째|둘째|셋째", utterance)
    )
    word_ranks = {"첫째": 1, "둘째": 2, "셋째": 3}
    for index, marker in enumerate(ordinal_matches):
        end = ordinal_matches[index + 1].start() if index + 1 < len(ordinal_matches) else len(utterance)
        axis = _first_priority_axis(utterance[marker.end():end])
        if axis:
            rank = int(marker.group("rank")) if marker.group("rank") else word_ranks[marker.group(0)]
            ranked.append((rank, axis[1]))
    if ranked:
        return list(dict.fromkeys(name for _, name in sorted(ranked)))

    next_marker = re.search(r"그다음|다음으로", utterance)
    if next_marker:
        before = [
            (match.start(), name)
            for name, pattern in _PRIORITY_AXIS_PATTERNS
            for match in pattern.finditer(utterance[:next_marker.start()])
        ]
        after = _first_priority_axis(utterance[next_marker.end():])
        if before and after:
            return list(dict.fromkeys([max(before)[1], after[1]]))
    return []

# 한 발화 안에서도 우선순위를 말한 절만 본다. "3만원 이하, 데이터 20GB 이상 조건은
# 그대로 두고, 가격을 가장 중요하게" 에서 앞 절의 '데이터'까지 축으로 세면 순서가 뒤집힌다.
_CLAUSE_SPLIT_RE = re.compile(r"[,.·\n]|그리고|그다음|다음으로")


def _axes_in_priority_clauses(utterance: str) -> list[str]:
    axes: list[str] = []
    for clause in _CLAUSE_SPLIT_RE.split(utterance or ""):
        if not _PRIORITY_PHRASE_RE.search(clause):
            continue
        for name, pattern in _PRIORITY_AXIS_PATTERNS:
            match = pattern.search(clause)
            # "데이터 소진 후 속도"의 '데이터'는 제공량 축이 아니라 QoS 용어의
            # 일부다. 이를 data와 qos 두 축으로 잡으면 화면은 첫 값인 데이터 버튼을
            # 켜고 실제 가중치도 데이터에 더 많이 주게 된다.
            if (
                name == "data"
                and match
                and re.match(r"\s*소진\s*후", clause[match.end():], re.IGNORECASE)
            ):
                continue
            if match and name not in axes:
                axes.append((match.start(), name))
    ordered = sorted(axes, key=lambda item: item[0])
    return list(dict.fromkeys(name for _, name in ordered))


def _repair_latest_priority(profile: UserProfile, query: str) -> UserProfile:
    """선호 축은 **가장 마지막에 말한 것**이 이긴다.

    프로파일링은 매 턴 대화 전체에서 프로필을 다시 뽑는다. 그래서 "가격을 가장 중요하게"
    다음에 "데이터를 가장 중요하게" 라고 해도 앞의 발화가 그대로 남아 순위가 바뀌지 않는
    일이 있었다(실측: priorities 가 ['price'] 로 고정). 사용자가 선호를 바꾸는 것은
    조건을 추가하는 것과 다르다 - 마지막 지시로 교체한다.

    필수 조건(예산 상한·최소 데이터량)은 건드리지 않는다. 바뀌는 것은 정렬 축뿐이다.
    """
    for utterance in reversed((query or "").splitlines()):
        explicit_axes = _explicit_priority_axes(utterance)
        axes = explicit_axes or _axes_in_priority_clauses(utterance)
        if axes:
            ordered = len(axes) > 1 and bool(explicit_axes)
            if profile.priorities == axes and profile.priorities_ordered == ordered:
                return profile
            return profile.model_copy(
                update={"priorities": axes, "priorities_ordered": ordered}
            )
    return profile


# '무제한'을 어느 범위로 볼지. 순서가 중요하다 - "완전 무제한이 아니어도"는 ON 패턴을
# 부분 문자열로 품고 있어서 OFF 를 먼저 본다.
_FULL_UNLIMITED_OFF_RE = re.compile(
    r"완전\s*무제한이?\s*아니어?도|속도\s*제한(?:이)?\s*있어도"
    r"|소진\s*후\s*속도(?:가)?\s*유지|qos\s*형?도\s*(?:괜찮|포함)",
    re.IGNORECASE,
)
_FULL_UNLIMITED_ON_RE = re.compile(r"완전\s*무제한|속도\s*제한\s*없")


def _repair_latest_unlimited_strictness(profile: UserProfile, query: str) -> UserProfile:
    """'완전 무제한만' ↔ 'QoS형도 괜찮다'도 마지막에 말한 쪽이 이긴다.

    priorities 와 같은 문제다(_repair_latest_priority). 프로필을 매 턴 대화 전체에서 다시
    뽑기 때문에, 앞에서 '완전 무제한만'이라고 했으면 뒤에 범위를 넓혀도 그대로 남는다.
    """
    for utterance in reversed((query or "").splitlines()):
        if _FULL_UNLIMITED_OFF_RE.search(utterance):
            return (profile.model_copy(update={"require_full_unlimited": None})
                    if profile.require_full_unlimited else profile)
        if _FULL_UNLIMITED_ON_RE.search(utterance):
            return profile.model_copy(
                update={"data_unlimited": True, "require_full_unlimited": True})
    return profile


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
_EXPLICIT_QOS_MIN_RE = re.compile(
    r"(?:qos|소진\s*후(?:\s*속도)?)\s*(?:가|는|도)?\s*"
    r"(\d+(?:\.\d+)?)\s*mbps\s*(?:이상|최소)",
    re.IGNORECASE,
)
_QOS_MIN_RELAX_RE = re.compile(
    r"(?:qos|소진\s*후(?:\s*최소)?\s*속도)"
    r".{0,18}?(?:조건)?\s*(?:은|는|을|를)?\s*"
    r"(?:빼|제외|삭제|해제|없애|풀)",
    re.IGNORECASE,
)

_POST_EXHAUSTION_APP_INTENT_RE = re.compile(
    r"(?:데이터\s*)?소진\s*후|qos|"
    r"데이터.{0,10}?(?:다\s*)?(?:쓰고|써도|쓴\s*뒤|사용한\s*뒤|사용해도)|"
    r"기본\s*제공량.{0,10}?(?:다\s*)?(?:쓰|사용)",
    re.IGNORECASE,
)
_QOS_SERVICE_PATTERNS = (
    ("youtube", re.compile(r"유튜브|youtube", re.IGNORECASE)),
    ("netflix", re.compile(r"넷플릭스|netflix", re.IGNORECASE)),
    ("disney_plus", re.compile(r"디즈니\s*(?:플러스|\+)|disney\s*\+?", re.IGNORECASE)),
    ("tiktok", re.compile(r"틱톡|tiktok", re.IGNORECASE)),
    ("instagram_reels", re.compile(r"인스타(?:그램)?\s*릴스|릴스", re.IGNORECASE)),
    ("instagram", re.compile(r"인스타그램|instagram", re.IGNORECASE)),
    ("spotify", re.compile(r"스포티파이|spotify", re.IGNORECASE)),
    ("google_maps", re.compile(r"구글\s*지도|내비게이션|google\s*maps?", re.IGNORECASE)),
    ("zoom", re.compile(r"줌|zoom", re.IGNORECASE)),
    ("whatsapp", re.compile(r"왓츠앱|whatsapp", re.IGNORECASE)),
)


def _qos_mode_from_text(service: str, text: str) -> str | None:
    """소진 후 이용 문장에서 앱별 화질·모드를 정규화한다."""
    if service == "youtube":
        if re.search(r"4k|uhd", text, re.IGNORECASE):
            return "uhd_4k"
        if re.search(r"1440p", text, re.IGNORECASE):
            return "fullhd_1440p"
        if re.search(r"1080p|fhd|full\s*hd", text, re.IGNORECASE):
            return "fhd_1080p"
        if re.search(r"720p|(?:^|\W)hd(?:\W|$)|고화질", text, re.IGNORECASE):
            return "hd_720p"
        if re.search(r"480p|(?:^|\W)sd(?:\W|$)|표준\s*화질", text, re.IGNORECASE):
            return "sd_480p"
        if re.search(r"240p|저화질", text, re.IGNORECASE):
            return "low_240p"
    elif service == "netflix":
        if re.search(r"4k|uhd", text, re.IGNORECASE):
            return "uhd_4k"
        if re.search(r"1080p|fhd|full\s*hd", text, re.IGNORECASE):
            return "fhd_1080p"
        if re.search(r"(?:^|\W)hd(?:\W|$)|고화질", text, re.IGNORECASE):
            return "hd"
        if re.search(r"(?:^|\W)sd(?:\W|$)|표준\s*화질", text, re.IGNORECASE):
            return "sd"
        if re.search(r"저화질", text, re.IGNORECASE):
            return "low"
    elif service == "disney_plus":
        if re.search(r"4k|uhd", text, re.IGNORECASE):
            return "uhd_4k"
        if re.search(r"라이브|실시간", text, re.IGNORECASE):
            return "live"
    elif service == "tiktok":
        return "hd" if re.search(r"(?:^|\W)hd(?:\W|$)|고화질", text, re.IGNORECASE) else None
    elif service == "instagram":
        if re.search(r"라이브", text, re.IGNORECASE):
            return "live"
        if re.search(r"사진\s*피드|피드", text, re.IGNORECASE):
            return "photo_feed"
    elif service == "spotify":
        if re.search(r"무손실|hi-?fi|lossless", text, re.IGNORECASE):
            return "lossless_hifi"
        if re.search(r"320\s*kbps|매우\s*높", text, re.IGNORECASE):
            return "very_high_320kbps"
    elif service == "google_maps":
        if re.search(r"위성", text, re.IGNORECASE):
            return "satellite"
        if re.search(r"스트리트\s*뷰", text, re.IGNORECASE):
            return "street_view"
    elif service == "zoom":
        if re.search(r"1080p|fhd|full\s*hd", text, re.IGNORECASE):
            return "fhd_1080p"
        if re.search(r"그룹.{0,5}hd|hd.{0,5}그룹", text, re.IGNORECASE):
            return "group_hd"
        if re.search(r"오디오|음성", text, re.IGNORECASE):
            return "audio_only"
    elif service == "whatsapp":
        if re.search(r"그룹\s*영상", text, re.IGNORECASE):
            return "group_video"
        if re.search(r"영상\s*통화", text, re.IGNORECASE):
            return "one_to_one_video"
    return None


def _apply_usage_based_qos(profile: UserProfile, query: str) -> UserProfile:
    """소진 후 앱 이용 의도가 명시된 경우에만 앱·화질의 최소 QoS를 적용한다."""
    text = query or ""
    intents = list(_POST_EXHAUSTION_APP_INTENT_RE.finditer(text))
    relaxations = list(_QOS_MIN_RELAX_RE.finditer(text))
    if not intents or (relaxations and relaxations[-1].start() > intents[-1].start()):
        return profile

    requirements: list[float] = []
    matched_service = False
    unresolved_service = False
    for service, pattern in _QOS_SERVICE_PATTERNS:
        if not pattern.search(text):
            continue
        matched_service = True
        required = required_qos_mbps(service, _qos_mode_from_text(service, text))
        if required is not None:
            requirements.append(required)
        else:
            unresolved_service = True

    # 공식 수치를 찾지 못한 앱·모드는 임의 Mbps를 만들지 않는다. 다만 사용자가
    # 소진 후 이용 자체를 요구했으므로 QoS 제공 여부만 필수로 남긴다.
    if unresolved_service or (not matched_service and re.search(r"영상|동영상", text, re.IGNORECASE)):
        return profile.model_copy(update={"min_qos_mbps": None, "requires_qos": True})
    if not requirements:
        return profile

    return profile.model_copy(
        update={
            "min_qos_mbps": max(requirements),
            "requires_qos": True,
        }
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


def _apply_explicit_qos_min(profile: UserProfile, query: str) -> UserProfile:
    """가장 최근의 QoS 최솟값 설정 또는 해제를 구조화 출력에 확정적으로 반영한다.

    전체 대화를 매번 다시 읽으므로 예전 수치만 검색하면 사용자가 후속 발화에서 조건을
    풀어도 그 값이 되살아난다. 발화를 역순으로 보고 처음 만나는 설정/해제를 적용한다.
    """
    for utterance in reversed((query or "").splitlines()):
        if _QOS_MIN_RELAX_RE.search(utterance):
            return profile.model_copy(update={"min_qos_mbps": None})
        match = _EXPLICIT_QOS_MIN_RE.search(utterance)
        if match:
            return profile.model_copy(update={"min_qos_mbps": float(match.group(1))})
    return profile


_QOS_REQUIRED_RE = re.compile(
    r"(?:qos\s*(?:가|는|도)?\s*(?:있(?:는|어|고)|제공|지원|적용|포함|요금제|상품)|"
    r"(?:데이터\s*)?소진\s*후(?:에도)?(?:\s*속도(?:가|는)?\s*(?:있|제공|지원)|"
    r"(?:\s*데이터(?:를|가)?)?.{0,10}?(?:사용\s*가능|사용할\s*수\s*있|계속\s*사용)))",
    re.IGNORECASE,
)
_QOS_OPTIONAL_RE = re.compile(
    r"(?:qos|소진\s*후).{0,12}?(?:상관\s*없|없어도|필요\s*없)",
    re.IGNORECASE,
)


def _apply_explicit_qos_requirement(profile: UserProfile, query: str) -> UserProfile:
    """수치가 없는 'QoS 있음' 요청도 후보 필터에서 빠지지 않게 한다."""
    text = query or ""
    required = list(_QOS_REQUIRED_RE.finditer(text))
    optional = list(_QOS_OPTIONAL_RE.finditer(text))
    if not required:
        return profile
    # 대화 전체가 들어오므로 서로 충돌하면 사용자가 나중에 말한 의도를 따른다.
    if optional and optional[-1].start() > required[-1].start():
        return profile.model_copy(update={"requires_qos": None})
    return profile.model_copy(update={"requires_qos": True})


BENEFIT_PREFERENCE_QUESTION = (
    "어떤 혜택을 찾으시나요? OTT·영상, 음악·오디오, 도서·콘텐츠, 멤버십, "
    "스마트기기, 추가 데이터, 제휴서비스, 교육, 페이백, "
    "포인트·적립, 상품권·사은품, 쿠폰·할인, 유심·배송비, 요금할인, "
    "로밍, 보험·안심 중에서 말씀해 주세요."
)
_VAGUE_BENEFIT_PREFERENCE_RE = re.compile(
    r"(?:부가\s*)?혜택\s*(?:이|은|을|도)?\s*(?:현재보다\s*)?(?:더\s*)?"
    r"(?:(?:가장|제일|특히)\s*)?(?:좋(?:은|아|고|게)|괜찮(?:은|아|고)|나은|우선|중요|중심|"
    r"많(?:은|아|고|게)|다양(?:한|해|하고)|풍부(?:한|해)|"
    r"선호(?:해|하고|하며|해서|하는|하는데|하지만|합니다|한다|함))",
    re.IGNORECASE,
)
_BENEFIT_PREFERENCE_MARKER = "benefit_preference"


def benefit_preference_missing(profile: UserProfile | None) -> bool:
    """혜택의 좋고 나쁨을 판단할 사용자 기준이 아직 없는지 반환한다."""
    return bool(
        profile
        and profile.needs_user_input
        and _BENEFIT_PREFERENCE_MARKER in profile.ambiguous
    )


def _apply_benefit_preference_question(profile: UserProfile, query: str) -> UserProfile:
    """주관적인 '혜택 좋음'을 임의 점수화하지 않고 원하는 유형을 확인한다."""
    if not _VAGUE_BENEFIT_PREFERENCE_RE.search(query or ""):
        return profile

    goals = [goal for goal in profile.comparison_goals or [] if goal != "better"]
    ambiguous = [item for item in profile.ambiguous if item != _BENEFIT_PREFERENCE_MARKER]
    has_specific_preference = bool(
        profile.wanted_benefits or profile.wanted_benefit_categories
    )
    if has_specific_preference:
        updates: dict[str, object] = {
            "comparison_goals": goals or None,
            "ambiguous": ambiguous,
        }
        # 이전 문구('페이백')가 대화 기록에 남아 있어도 구체적 혜택을 고르면
        # 같은 질문을 다시 띄우지 않는다.
        if (profile.followup_question or "").startswith("어떤 혜택을 찾으시나요?"):
            updates.update({"needs_user_input": False, "followup_question": None})
        return profile.model_copy(update=updates)

    ambiguous.append(_BENEFIT_PREFERENCE_MARKER)
    return profile.model_copy(
        update={
            "comparison_goals": goals or None,
            "ambiguous": ambiguous,
            "needs_user_input": True,
            "followup_question": BENEFIT_PREFERENCE_QUESTION,
        }
    )


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


# "현재보다 나은 것"처럼 방향만 말한 요청. 어느 항목을 포기할지는 말하지 않았다.
_GENERAL_BETTER_RE = re.compile(
    r"(?:현재|기존|지금).*보다\s*(?:더\s*)?(?:유리|나은|좋은|괜찮)"
    r"|더\s*(?:나은|좋은|괜찮은)\s*(?:요금제|상품|플랜|거|것)"
)
# 무엇을 얻는 대신 무엇을 포기하겠다는 명시적 맞교환 요청. 이건 필수 조건으로 남긴다.
_EXPLICIT_TRADEOFF_RE = re.compile(
    r"더\s*(?:싼|저렴)|싼\s*(?:거|것|걸)|저렴한\s*(?:거|것|걸)"
    r"|데이터\s*(?:가|를)?\s*더\s*많|더\s*많은\s*데이터|더\s*빠른"
)
_MORE_DATA_COMPARISON_RE = re.compile(
    r"(?:현재|기존|지금|이것|이거).{0,45}보다.{0,18}(?:데이터|용량).{0,10}(?:더\s*)?많"
    r"|(?:데이터|용량).{0,10}더\s*많|더\s*많은\s*(?:데이터|용량)",
    re.IGNORECASE,
)
_CHEAPER_COMPARISON_RE = re.compile(
    r"(?:현재|기존|지금|이것|이거).{0,45}보다.{0,18}(?:더\s*)?(?:싼|저렴)"
    r"|더\s*(?:싼|저렴한)|(?:싼|저렴한)\s*(?:거|것|걸|요금제)",
    re.IGNORECASE,
)
_FASTER_QOS_COMPARISON_RE = re.compile(
    r"(?:현재|기존|지금|이것|이거).{0,45}보다.{0,18}(?:소진\s*후|qos)?.{0,8}(?:더\s*)?빠른",
    re.IGNORECASE,
)


def _repair_general_comparison(profile: UserProfile, query: str) -> UserProfile:
    """현재 요금제와의 비교 목표는 **비교를 말한 문장**에서만 나온다.

    두 가지 오추출을 막는다.
    1. "현재보다 나은 것"을 cheaper 로 좁히는 것. 그러면 무엇을 포기하는지 말한 적 없는
       사용자에게 데이터가 줄어든 저가 상품만 남는다.
    2. "가격을 가장 중요하게" 같은 **선호 표현**이 cheaper 로 들어가는 것. 선호는 순위
       가중치이지 후보를 잘라내는 조건이 아니다. 실측: 선호만 바꿨는데 후보가 0건이 됐다.
    """
    if not any(getattr(profile, field) is not None for field in _REFERENCE_SPEC_FIELDS):
        return profile
    text = query or ""
    explicit_goals: list[str] = []
    if _CHEAPER_COMPARISON_RE.search(text):
        explicit_goals.append("cheaper")
    if _MORE_DATA_COMPARISON_RE.search(text):
        explicit_goals.append("more_data")
    if _FASTER_QOS_COMPARISON_RE.search(text):
        explicit_goals.append("faster_qos")
    if explicit_goals:
        # 짧은 가격 답변 뒤에도 전체 대화에 남아 있는 최초 비교 목표를 복원한다.
        return profile.model_copy(update={"comparison_goals": explicit_goals})
    if _EXPLICIT_TRADEOFF_RE.search(text):
        return profile
    if _GENERAL_BETTER_RE.search(text):
        return profile.model_copy(update={"comparison_goals": ["better"]})
    if profile.comparison_goals:
        # 비교를 말한 문장이 하나도 없다. 선호 표현에서 끌려 나온 목표이므로 버린다.
        return profile.model_copy(update={"comparison_goals": None})
    return profile


# "지금 SKT ○○ 쓰는데"의 SKT 는 **출발지**이지 찾는 범위가 아니다. 프롬프트로만 막으면 carrier_type=MNO 가
# 계속 들어와 통신 3사 -> 알뜰폰으로 옮기려는 사용자에게 통신 3사 상품만 보여 주게 된다.
_CURRENT_CARRIER_RE = re.compile(
    r"(지금|현재|요즘).{0,12}(SK ?T|SK텔레콤|KT|LG ?U\+?|유플러스|엘지|통신 ?3사).{0,25}(쓰|사용|이용|가입)")
_CARRIER_SCOPE_RE = re.compile(r"안에서|중에서|내에서|망으로|망에서|같은 망|통신 ?3사(만|에서|로)|요금제만|(으)?로만")


def _drop_current_carrier_scope(profile: UserProfile, query: str) -> UserProfile:
    if not (profile.carrier_type or profile.host_mno) or profile.mvno_brand:
        return profile
    text = query or ""
    if not _CURRENT_CARRIER_RE.search(text) or _CARRIER_SCOPE_RE.search(text):
        return profile
    return profile.model_copy(update={"carrier_type": None, "host_mno": None})


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


_DATA_AMOUNT_IN_TEXT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:GB|G|기가)(?![A-Za-z])", re.IGNORECASE)
_DAILY_ALLOWANCE_RE = re.compile(
    r"(?:매일|하루(?:에)?|일일|(?<![가-힣])일(?:당)?)\s*(?:기본\s*)?(?:데이터\s*)?"
    r"(?P<amount>\d+(?:\.\d+)?)\s*(?:GB|G|기가)(?![A-Za-z])",
    re.IGNORECASE,
)
_MONTHLY_BASE_RE = re.compile(
    r"(?:월|매월|한\s*달(?:에)?)\s*(?:기본\s*)?(?:데이터\s*)?"
    r"(?P<amount>\d+(?:\.\d+)?)\s*(?:GB|G|기가)(?![A-Za-z])",
    re.IGNORECASE,
)
_DAILY_USAGE_VERB_RE = re.compile(
    r"^\s*(?:씩\s*)?(?:를|을|가|이)?\s*(?:정도\s*)?(?:쓰|쓴|씁|사용|소모|이용|필요)"
)


def _apply_daily_allowance(profile: UserProfile, query: str) -> UserProfile:
    """'매일 5GB 제공'을 월 5GB나 하루 5GB 사용량으로 오해하지 않는다."""
    text = query or ""
    if profile.reference_plan_name:
        text = re.sub(re.escape(profile.reference_plan_name), " ", text, flags=re.IGNORECASE)
    matches = list(_DAILY_ALLOWANCE_RE.finditer(text))
    daily_matches = [match for match in matches if not _DAILY_USAGE_VERB_RE.match(text[match.end():match.end() + 16])]
    if not daily_matches:
        updates: dict[str, object] = {}
        if profile.min_monthly_base_data_gb is not None:
            # 월 기본량만 말한 질문은 일 제공형으로 한정하지 않는다.
            updates["min_monthly_base_data_gb"] = None
            if profile.min_data_gb is None:
                updates["min_data_gb"] = profile.min_monthly_base_data_gb
        # '하루 5GB를 쓴다'는 일 제공형 필터가 아니라 30일 기준 월 사용 목표다.
        if matches:
            updates["min_daily_data_gb"] = None
            if len(list(_DATA_AMOUNT_IN_TEXT_RE.finditer(text))) == 1:
                updates.update({
                    "min_data_gb": None,
                    "target_data_gb": float(matches[-1].group("amount")) * 30,
                })
        return profile.model_copy(update=updates) if updates else profile

    daily = daily_matches[-1]
    daily_gb = float(daily.group("amount"))
    updates: dict[str, object] = {"min_daily_data_gb": daily_gb}
    monthly_matches = list(_MONTHLY_BASE_RE.finditer(text))
    monthly_base = next(
        (
            float(match.group("amount"))
            for match in reversed(monthly_matches)
            if "기본" in match.group(0)
            or re.search(
                r"(?:\+|그리고|및|더해|추가로|추가|,\s*|，\s*|에\s*(?:매일|하루|일))",
                text[match.end():daily.start()] if match.end() <= daily.start()
                else text[daily.end():match.start()],
            )
        ),
        None,
    )
    updates["min_monthly_base_data_gb"] = monthly_base
    # LLM이 일 제공량이나 그 월 환산치를 월 데이터 필드로 복사했으면 제거한다.
    # 별도로 말한 월 총량 조건이 있으면 그대로 유지한다.
    mistaken_monthly_values = {daily_gb, daily_gb * 30 + (monthly_base or 0)}
    if monthly_base is not None:
        mistaken_monthly_values.add(monthly_base)
    for field in ("min_data_gb", "target_data_gb", "max_data_gb"):
        value = getattr(profile, field)
        if value is not None and any(abs(float(value) - wrong) < 1e-6 for wrong in mistaken_monthly_values):
            updates[field] = None
    return profile.model_copy(update=updates)


def _drop_reference_name_data_constraint(profile: UserProfile, query: str) -> UserProfile:
    """현재 상품명 속 용량을 신규 요금제의 데이터 조건으로 쓰지 않는다."""
    name = (profile.reference_plan_name or "").strip()
    if not name:
        return profile
    matched = find_plans_by_name(name)
    if not matched:
        return profile
    # 상품명 바깥에 사용자가 별도 수치를 말했다면 그 조건은 보존한다.
    residual = re.sub(re.escape(name), " ", query or "", flags=re.IGNORECASE)
    if _DATA_AMOUNT_IN_TEXT_RE.search(residual):
        return profile
    base_values = {
        float(plan["base_data_gb"])
        for plan in matched
        if plan.get("base_data_gb") is not None
    }
    updates: dict[str, object] = {}
    for field in ("min_data_gb", "target_data_gb", "max_data_gb"):
        value = getattr(profile, field)
        if value is not None and any(abs(float(value) - base) < 1e-9 for base in base_values):
            updates[field] = None
    return profile.model_copy(update=updates) if updates else profile


_REFERENCE_PRICE_REPLY_RE = re.compile(
    r"^\s*(?:월\s*)?([\d,]+(?:\.\d+)?)\s*(만원|천원|원)"
    r"\s*(?:짜리|내고\s*있어|내요|입니다|이야|이에요|예요|맞아)?[.!?]?\s*$",
    re.IGNORECASE,
)


def _apply_ambiguous_reference_fee_reply(profile: UserProfile, query: str) -> UserProfile:
    """중복 상품 확인 질문에 가격만 답한 경우 현재 납부액으로 확정한다."""
    if not profile.reference_plan_name:
        return profile
    matched = find_plans_by_name(profile.reference_plan_name)
    if len(matched) < 2:
        return profile
    utterances = [line.strip() for line in (query or "").splitlines() if line.strip()]
    if len(utterances) < 2:
        return profile
    latest = utterances[-1]
    if re.search(r"예산|이하|미만|이상|까지|추천", latest):
        return profile
    price = _REFERENCE_PRICE_REPLY_RE.fullmatch(latest)
    if not price:
        return profile
    fee = int(float(price.group(1).replace(",", "")) * _BUDGET_UNIT[price.group(2)])
    return profile.model_copy(
        update={
            "reference_fee_won": fee,
            "budget_min_won": None,
            "budget_max_won": None,
        }
    )


_BENEFIT_FOLLOWUP_RE = re.compile(
    r"(?:어떤|무슨).{0,12}혜택|혜택.{0,16}(?:포함|좋(?:을|은)|원하|찾)",
    re.IGNORECASE,
)


def _apply_pending_benefit_reply(
    profile: UserProfile,
    messages: list[object],
) -> UserProfile:
    """직전 혜택 질문에 대한 짧은 답을 혜택 카테고리로 확정한다.

    프로필 LLM은 HumanMessage만 받으므로 ``도서·콘텐츠`` 같은 답이
    직전의 "어떤 혜택?" 질문에 대한 선택임을 놓칠 수 있다. 실제
    대화 메시지를 확인해 이 연결만 결정적으로 보정한다.
    """
    latest_human_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], HumanMessage)
        ),
        None,
    )
    if latest_human_index is None:
        return profile

    latest_reply = str(messages[latest_human_index].content).strip()
    category = normalize_benefit_category(latest_reply)
    if category is None:
        return profile

    previous_assistant = next(
        (
            message
            for message in reversed(messages[:latest_human_index])
            if isinstance(message, AIMessage)
        ),
        None,
    )
    if previous_assistant is None or not _BENEFIT_FOLLOWUP_RE.search(
        str(previous_assistant.content)
    ):
        return profile

    categories = list(profile.wanted_benefit_categories or [])
    if category not in categories:
        categories.append(category)
    return profile.model_copy(
        update={
            "wanted_benefit_categories": categories,
            "needs_user_input": False,
            "followup_question": None,
            "ambiguous": [
                item
                for item in profile.ambiguous
                if item != _BENEFIT_PREFERENCE_MARKER
            ],
            "comparison_goals": [
                goal for goal in (profile.comparison_goals or []) if goal != "better"
            ] or None,
        }
    )


def _drop_unrequested_benefit_followup(profile: UserProfile, query: str) -> UserProfile:
    """LLM이 일반적인 '괜찮은 요금제'를 혜택 질문으로 과해석한 경우를 되돌린다.

    혜택을 실제로 언급하지 않은 비교 요청은 이미 데이터·가격 조건만으로 추천할 수 있다.
    이 질문을 남기면 결과 3개와 '어떤 혜택?'이 동시에 떠 사용자가 추가 입력이 필수라고
    오해한다. 명시적인 모호 혜택 요청은 바로 앞 보정이 BENEFIT_PREFERENCE_QUESTION으로
    확정하므로 그대로 남긴다.
    """
    question = profile.followup_question or ""
    if not _BENEFIT_FOLLOWUP_RE.search(question):
        return profile
    if _VAGUE_BENEFIT_PREFERENCE_RE.search(query or ""):
        return profile
    return profile.model_copy(
        update={
            "needs_user_input": False,
            "followup_question": None,
            "ambiguous": [
                item for item in profile.ambiguous
                if item != _BENEFIT_PREFERENCE_MARKER
            ],
        }
    )


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


def _repair_gift_pair_request(profile: UserProfile, query: str) -> UserProfile:
    """최신 발화의 사은품/페이백 둘 중 하나와 둘 다를 별도 분류로 확정한다."""
    latest = (query or "").splitlines()[-1].strip()
    if re.search(r"빼|제외|상관없|원치|싫", latest):
        return profile
    if not (re.search(r"사은품|상품권", latest) and re.search(r"페이백|캐시백", latest)):
        return profile
    pair = re.search(
        r"(?:사은품|상품권)\s*(?:/|·|이나|또는|혹은|및|과|와|하고)\s*(?:페이백|캐시백)"
        r"|(?:페이백|캐시백)\s*(?:/|·|이나|또는|혹은|및|과|와|하고)\s*(?:사은품|상품권)",
        latest,
    )
    if not pair:
        return profile
    together = bool(re.search(r"둘\s*다|모두|동시|및|과|와|하고", pair.group(0) + latest[pair.end():]))
    categories = [
        category for category in profile.wanted_benefit_categories or []
        if category not in {"상품권/사은품", "페이백"}
    ]
    categories.extend(["상품권/사은품", "페이백"])
    names = [
        name for name in profile.wanted_benefits or []
        if not (re.search(r"사은품|상품권", name) and re.search(r"페이백|캐시백", name))
    ]
    return profile.model_copy(update={
        "wanted_benefits": names or None,
        "wanted_benefit_categories": list(dict.fromkeys(categories)),
        "benefit_match_mode": "all" if together else "any",
    })


# 앱별 이용 시간은 후속 요청 때도 같아야 한다. LLM이 이전의 "유튜브 1시간"을
# "일반 영상 1시간"으로 다시 분류하면 GB/시간 계수가 달라져 예상량이 흔들린다.
_EXPLICIT_USAGE_ALIASES: tuple[tuple[str, str], ...] = (
    (r"인스타그램\s*릴스|인스타\s*릴스|릴스", "instagram_reels"),
    (r"디즈니\s*(?:플러스|\+)", "disney_plus"),
    (r"포켓몬\s*(?:고|go)", "pokemongo_game"),
    (r"리그\s*오브\s*레전드|롤", "league_of_legend_game"),
    (r"배틀그라운드|배그", "battleground_game"),
    (r"클래시\s*로얄", "clashroyale_game"),
    (r"콜\s*오브\s*듀티", "callofduty_game"),
    (r"브롤\s*스타즈", "brawlstars_game"),
    (r"스타듀\s*밸리", "stardewvalley_game"),
    (r"유튜브|youtube", "youtube"),
    (r"넷플릭스|netflix", "netflix"),
    (r"틱톡|tiktok", "tiktok"),
    (r"인스타그램|인스타", "instagram"),
    (r"스포티파이|spotify", "spotify"),
    (r"구글\s*지도|내비게이션|네비게이션", "google_maps"),
    (r"줌|zoom", "zoom"),
    (r"왓츠앱|whatsapp", "whatsapp"),
    (r"포트나이트", "fortnite_game"),
    (r"게임", "mobile_game"),
)
_USAGE_TIME_TOKEN = r"(?:\d+(?:\.\d+)?|한|두|세|반)"
_USAGE_REMOVE_RE = re.compile(r"빼|제외|안\s*(?:해|봐|보|쓰|사용)|하지\s*않", re.IGNORECASE)


def _usage_hours(amount: str, unit: str) -> float:
    values = {"한": 1.0, "두": 2.0, "세": 3.0, "반": 0.5}
    value = values.get(amount, float(amount) if re.fullmatch(r"\d+(?:\.\d+)?", amount) else 0.0)
    return value / 60 if unit.startswith("분") else value


def _explicit_app_usages(query: str) -> dict[str, float]:
    """시간순으로 앱 사용량을 읽는다. 같은 앱은 최신 언급이 덮고 이후 제외 요청은 지운다."""
    found: dict[str, float] = {}
    for utterance in re.split(r"[\n.!?]+", query or ""):
        for alias, service in _EXPLICIT_USAGE_ALIASES:
            for mention in re.finditer(alias, utterance, re.IGNORECASE):
                nearby = utterance[max(0, mention.start() - 28):mention.end() + 28]
                if _USAGE_REMOVE_RE.search(nearby):
                    found.pop(service, None)
                    continue
                after = utterance[mention.end():mention.end() + 28]
                before = utterance[max(0, mention.start() - 28):mention.start()]
                pattern = rf"(?:하루(?:에)?\s*)?(?:약\s*)?(?P<n>{_USAGE_TIME_TOKEN})\s*(?P<u>시간|분)(?:씩)?"
                match = re.search(pattern, after)
                if match is None:
                    matches = list(re.finditer(pattern, before))
                    match = matches[-1] if matches else None
                if match is not None:
                    hours = _usage_hours(match.group("n"), match.group("u"))
                    if hours > 0:
                        found[service] = hours
    return found


def _explicitly_removed_app_usages(query: str) -> set[str]:
    """앱별 마지막 지시가 제외 요청인 항목을 반환한다."""
    removed: set[str] = set()
    for utterance in re.split(r"[\n.!?]+", query or ""):
        for alias, service in _EXPLICIT_USAGE_ALIASES:
            for mention in re.finditer(alias, utterance, re.IGNORECASE):
                nearby = utterance[max(0, mention.start() - 28):mention.end() + 28]
                if _USAGE_REMOVE_RE.search(nearby):
                    removed.add(service)
                elif re.search(rf"{_USAGE_TIME_TOKEN}\s*(?:시간|분)", nearby):
                    removed.discard(service)
    return removed


def _repair_explicit_app_usages(profile: UserProfile, query: str) -> UserProfile:
    """명시된 앱·시간을 LLM 재해석보다 우선하고, 앱 미지정 범주와의 중복을 제거한다."""
    explicit = _explicit_app_usages(query)
    removed = _explicitly_removed_app_usages(query)
    if not explicit and not removed:
        return profile

    existing = {
        usage.service: usage for usage in profile.app_usages
        if usage.service not in removed
    }
    for service, hours in explicit.items():
        previous = existing.get(service)
        existing[service] = AppUsage(
            service=service,
            daily_hours=hours,
            mode=previous.mode if previous else None,
        )

    usages = list(existing.values())
    updates: dict[str, object] = {"app_usages": usages}
    if {"youtube", "netflix", "disney_plus"}.intersection(explicit):
        updates["daily_video_hours"] = None
        updates["smartchoice_usage_pattern"] = None
        usages = [usage for usage in usages if usage.service != "generic_video"]
    if {"tiktok", "instagram", "instagram_reels"}.intersection(explicit):
        updates["daily_shortform_hours"] = None
        usages = [usage for usage in usages if usage.service != "generic_shortform"]
    if any(service.endswith("_game") for service in explicit):
        updates["daily_game_hours"] = None
    updates["app_usages"] = usages
    return profile.model_copy(update=updates)


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

    assumptions = list(profile.assumptions)
    if profile.user_age is None and profile.age_condition is None:
        # 무엇을 왜 뺐는지 사용자가 볼 수 있어야 한다. 조용히 거르면 후보 수만 줄어 보인다.
        assumptions.append(AGE_UNKNOWN_ASSUMPTION)

    return profile.model_copy(
        update={
            "hard_constraints": hard_constraints,
            "followup_question": followup_question,
            "assumptions": assumptions,
            "estimated_monthly_data_gb": estimated_gb,
            "usage_estimate_notes": usage_notes,
        }
    )


# 데이터와 요금 둘 다 못 잡으면 후보를 좁힐 수 없다. 전체에서 3건을 뽑는 추천은 의미가 없으므로
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
            profile.min_monthly_base_data_gb,
            profile.min_daily_data_gb,
            profile.target_data_gb,
            profile.max_data_gb,
            profile.data_unlimited,
            profile.estimated_monthly_data_gb,
            profile.daily_video_hours,
            profile.daily_shortform_hours,
            profile.daily_game_hours,
            profile.min_qos_mbps,
            profile.requires_qos,
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


# "~면 좋겠어" 류. 조건을 말하긴 했지만 못 맞추면 탈락시킬 정도는 아니라는 뜻이다.
_BENEFIT_WISH_RE = re.compile(
    r"면\s*좋(?:겠|을)|있으면\s*좋|가능하면|되도록|웬만하면|선호|괜찮을\s*것\s*같",
)
# "~만", "반드시" 류. 못 맞추면 후보에서 빼라는 뜻이다.
_BENEFIT_MUST_RE = re.compile(
    r"만\s*(?:원해|원합|주세|보여|추천|찾|골라|해\s*줘)|반드시|무조건|필수|꼭\s|있어야",
)
_BENEFIT_FIELDS = ("wanted_benefits", "wanted_benefit_categories")


def _apply_benefit_constraint_strength(profile: UserProfile, query: str) -> UserProfile:
    """혜택 조건이 필수인지 선호인지를 말투로 갈라 hard_constraints 를 고친다.

    '넷플릭스 포함이면 좋겠어'까지 필수 필터가 되면 포함하지 않은 상품이 후보에서 통째로
    사라진다. 사용자가 원한 건 가점이지 배제가 아니다. 반대로 '넷플릭스 포함만 원해'는
    필터여야 한다. 둘 다 아니면 LLM 이 정한 값을 그대로 둔다.
    """
    if not any(getattr(profile, field, None) for field in _BENEFIT_FIELDS):
        return profile
    text = query or ""
    must, wish = _BENEFIT_MUST_RE.search(text), _BENEFIT_WISH_RE.search(text)
    if must or not wish:
        if not must:
            return profile
        missing = [f for f in _BENEFIT_FIELDS if getattr(profile, f, None) and f not in profile.hard_constraints]
        if not missing:
            return profile
        return profile.model_copy(update={"hard_constraints": [*profile.hard_constraints, *missing]})
    relaxed = [field for field in profile.hard_constraints if field not in _BENEFIT_FIELDS]
    if len(relaxed) == len(profile.hard_constraints):
        return profile
    return profile.model_copy(update={"hard_constraints": relaxed})


# 구조화 출력이 값 없음을 문자열로 흘리는 경우. 'null' 이라는 이름의 요금제를 DB 에서
# 찾다가 "정확한 요금제명을 알려주세요"로 파이프라인 전체가 멈춘 적이 있다.
_PLACEHOLDER_TEXT = {
    "", "null", "none", "nil", "n/a", "na", "-", "미상", "없음", "해당없음",
    "unknown", "undefined", "not specified", "미지정",
}
_TEXT_FIELDS = ("mvno_brand", "age_condition", "reference_plan_name", "notes", "followup_question")


def _drop_placeholder_text(profile: UserProfile, query: str) -> UserProfile:
    """문자열 필드에 들어온 'null' 같은 자리표시자를 진짜 None 으로 바꾼다."""
    updates = {
        field: None
        for field in _TEXT_FIELDS
        if isinstance(getattr(profile, field, None), str)
        and str(getattr(profile, field)).strip().casefold() in _PLACEHOLDER_TEXT
    }
    return profile.model_copy(update=updates) if updates else profile


# "지금 월 3만원 내고 있다" — 현재 납부액이지 예산 상한이 아니다.
_CURRENT_FEE_PREFIX_RE = re.compile(
    r"(?:지금|현재|기존|원래|쓰던|쓰는)[^.\n]{0,20}?([\d,]+(?:\.\d+)?)\s*(만원|천원|원)"
)
# 발화에 등장한 금액 표현 전부. 현재 요금 말고 다른 금액을 말했는지 세는 데 쓴다.
_MONEY_AMOUNT_RE = re.compile(r"[\d,]+(?:\.\d+)?\s*(?:만원|천원|원)")
_CURRENT_FEE_SUFFIX_RE = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*(만원|천원|원)[^.\n]{0,14}?(?:내고|내는|냅니|쓰고|쓰는|씁니|사용\s*중|이용\s*중|납부)"
)


def _apply_reference_fee(profile: UserProfile, query: str) -> UserProfile:
    """'지금 월 3만원인데' 를 예산 상한이 아니라 현재 요금으로 읽는다.

    '지금 쓰는 요금제가 월 3만원인데 바꾸는 게 나을까'에 budget_max_won=30,000 이 붙으면
    현재보다 싼 상품만 후보가 된다. 사용자는 상한을 말한 적이 없고, 오히려 지금이 나은지를
    물었다. 상한 표현(이하/까지/미만)을 실제로 말한 경우에는 그대로 둔다
    ('지금 3만원 내는데 2만원 이하로' 는 현재 요금 3만원 + 상한 2만원이 맞다).
    """
    text = query or ""
    matched = _CURRENT_FEE_PREFIX_RE.search(text) or _CURRENT_FEE_SUFFIX_RE.search(text)
    if not matched:
        return profile
    fee = int(float(matched.group(1).replace(",", "")) * _BUDGET_UNIT[matched.group(2)])

    updates: dict[str, object] = {}
    if profile.reference_fee_won is None:
        updates["reference_fee_won"] = fee

    # 발화에 나온 금액이 현재 요금 하나뿐이고 경계 표현('이하', 'N만원대')도 없으면,
    # 프로필에 붙은 예산은 전부 이 금액에서 흘러나온 것이다. '월 2만원에 100GB 쓰고 있어'가
    # budget 20,000~29,999 로 잡혀 후보가 144건까지 줄어든 적이 있다.
    # 금액이 둘 이상이면(예: '지금 3만원 내는데 2만원짜리 있어?') 손대지 않는다.
    amounts = {match.group(0) for match in _MONEY_AMOUNT_RE.finditer(text)}
    bounded = _BUDGET_MAX_RE.search(text) or _BUDGET_MIN_RE.search(text)
    if not bounded and len(amounts) == 1:
        if profile.budget_max_won is not None:
            updates["budget_max_won"] = None
        if profile.budget_min_won is not None:
            updates["budget_min_won"] = None
        if updates.keys() & {"budget_max_won", "budget_min_won"}:
            updates["hard_constraints"] = [
                field
                for field in profile.hard_constraints
                if field not in ("budget_max_won", "budget_min_won")
            ]
    return profile.model_copy(update=updates) if updates else profile


# 수치 없이 "데이터가 넉넉했으면" 하는 희망. 필터 조건은 못 되지만 버리면 안 된다.
_DATA_WISH_RE = re.compile(
    r"(?:데이터|용량)[^.\n]{0,12}(?:넉넉|여유|충분|많[았으은]|빵빵)"
    r"|(?:넉넉|여유|충분)[^.\n]{0,10}(?:데이터|용량)",
)


def _apply_soft_data_preference(profile: UserProfile, query: str) -> UserProfile:
    """'가능하면 데이터가 넉넉했으면' 을 가중치 보정으로 남긴다.

    수치가 없으니 min_data_gb 를 만들 수 없고, LLM 은 조건 필드에 못 넣으면 그냥 버린다.
    그러면 예산만 맞는 10GB 요금제가 상위를 채운다 — 사용자가 말한 것이 사라진 것이다.
    필터가 아니라 data 축 가중치로만 반영한다(필수 조건이 아니므로 후보는 그대로 둔다).
    """
    if not _DATA_WISH_RE.search(query or ""):
        return profile
    if any(
        value is not None
        for value in (profile.min_data_gb, profile.target_data_gb, profile.data_unlimited)
    ):
        return profile  # 사용자가 수치나 무제한을 말했으면 그쪽이 우선이다
    priorities = list(profile.priorities or [])
    if "data" in priorities:
        return profile
    return profile.model_copy(
        update={"priorities": [*priorities, "data"], "priorities_ordered": False}
    )


# 결과 화면의 선택 문장은 LLM이 LTE/5G를 필수 조건으로 오해하지 않도록 코드로 확정한다.
_NETWORK_PREFERENCE_RE = re.compile(
    r"통신\s*세대\s*우선순위\s*(?:는|:)?\s*(LTE|5G|상관\s*없음)", re.IGNORECASE
)


def _apply_network_preference(profile: UserProfile, query: str) -> UserProfile:
    """마지막 세대 우선순위 선택만 순위 선호로 반영한다. 필터와 섞지 않는다."""
    choices = list(_NETWORK_PREFERENCE_RE.finditer(query or ""))
    if not choices:
        return profile
    choice = choices[-1].group(1).replace(" ", "").upper()
    return profile.model_copy(
        update={
            "network_preference": None if choice == "상관없음" else choice,
            "network_gen": None,
        }
    )


# 구조화 출력을 사용자 발화로 되짚어 고치는 보정들. 순서대로 적용한다.
# 프롬프트 지시만으로는 같은 오추출이 계속 재발해서 코드로 못 박는 자리다.
_REPAIRS = (
    # 다른 보정이 'null' 같은 자리표시자를 진짜 값으로 오해하지 않게 맨 먼저 돌린다.
    _drop_placeholder_text,
    _drop_phantom_budget,
    _repair_budget_bounds,
    # 예산 경계 확정 다음에 와야 한다. 현재 납부액이 상한으로 들어갔는지를 그 결과로 판단한다.
    _apply_reference_fee,
    _drop_inferred_priorities,
    # 정렬 요구가 살아남은 뒤에 최신 발화로 축을 교체한다. 앞에 두면 방금 고른 축이 지워진다.
    _repair_latest_priority,
    _repair_latest_unlimited_strictness,
    # _drop_inferred_priorities 다음에 와야 한다. 앞에 두면 방금 넣은 data 가 지워진다.
    _apply_soft_data_preference,
    # 화면에서 고른 세대 우선은 hard filter가 아니다.
    _apply_network_preference,
    _apply_user_age,
    _apply_explicit_qos_requirement,
    _repair_gift_pair_request,
    _apply_benefit_preference_question,
    _drop_unrequested_benefit_followup,
    # 앱·화질 추정값보다 바로 뒤의 명시적 Mbps 설정/해제가 최종 우선권을 갖는다.
    _apply_usage_based_qos,
    _apply_explicit_qos_min,
    _apply_explicit_data_max,
    _repair_explicit_app_usages,
    _apply_smartchoice_usage_rule,
    _repair_general_comparison,
    _repair_reference_plan_name,
    # 정확한 현재 상품명을 복구한 뒤 이름 속 GB와 별도 요구량을 구분한다.
    _drop_reference_name_data_constraint,
    _apply_daily_allowance,
    _apply_ambiguous_reference_fee_reply,
    _drop_current_carrier_scope,
)


# _normalize_profile 은 hard_constraints 를 값 유무로 다시 만든다. 그래서 "값은 있지만
# 필수는 아니다"(선호)라는 판단은 정규화 뒤에 적용해야 한다. 앞에서 빼면 곧바로 되돌아온다.
_POST_NORMALIZE_REPAIRS = (_apply_benefit_constraint_strength,)


def _apply_relaxed_fields(profile: UserProfile, relaxed_fields: list[str] | None) -> UserProfile:
    """0건 화면에서 사용자가 누른 조건을 구조적으로 해제한다.

    대화 전체를 다시 LLM에 넣으면 과거의 강한 조건이 다시 추출될 수 있다. 버튼은 이미
    blocker의 정확한 필드명을 알고 있으므로 자연어 문장 해석보다 이 명시적 신호가 우선한다.
    include_mno는 값 제거가 아니라 기본 알뜰폰 범위를 통신 3사까지 넓히는 특수 조건이다.
    """
    allowed = set(CONSTRAINT_FIELDS) | {"include_mno"}
    requested = list(dict.fromkeys(field for field in (relaxed_fields or []) if field in allowed))
    if not requested:
        return profile

    updates: dict[str, object] = {
        field: (True if field == "include_mno" else None)
        for field in requested
    }
    updates["hard_constraints"] = [
        field for field in profile.hard_constraints if field not in requested
    ]
    if {"wanted_benefits", "wanted_benefit_categories"}.intersection(requested):
        # 최초의 '부가혜택 중요'가 대화 전체에 남아 있어도 방금 혜택 조건을 푼
        # 사용자에게 다시 '어떤 혜택?'을 묻지 않는다. 예산·사용량 등은 그대로 쓴다.
        updates["ambiguous"] = [
            item for item in profile.ambiguous if item != _BENEFIT_PREFERENCE_MARKER
        ]
        if _BENEFIT_FOLLOWUP_RE.search(profile.followup_question or ""):
            updates["needs_user_input"] = False
            updates["followup_question"] = None
    return profile.model_copy(update=updates)


def profiling_node(state: PipelineState, config: RunnableConfig) -> dict:
    messages = [
        message
        for message in state.get("messages", [])
        if isinstance(message, HumanMessage)
    ] or [HumanMessage(content="")]

    prompt = PROFILING_PROMPT + "\n\n" + feedback_block(state)
    llm = get_profile_llm(config).with_structured_output(UserProfile)
    query = user_query(state)

    profile = llm.invoke([SystemMessage(content=prompt), *messages])
    profile = _apply_pending_benefit_reply(profile, state.get("messages", []))
    for repair in _REPAIRS:
        profile = repair(profile, query)
    profile = _normalize_profile(profile)
    for repair in _POST_NORMALIZE_REPAIRS:
        profile = repair(profile, query)
    # 정규화가 hard_constraints를 다시 만들기 때문에 반드시 모든 정규화·보정 뒤에 적용한다.
    profile = _apply_relaxed_fields(profile, state.get("relaxed_fields"))
    if core_signal_missing(profile) and not benefit_preference_missing(profile):
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
    assert _apply_explicit_qos_min(UserProfile(), "qos가 3mbps이상인 요금제").min_qos_mbps == 3
    assert _apply_explicit_qos_requirement(UserProfile(), "qos있는 요금제를 추천해줘").requires_qos is True
    assert _apply_explicit_qos_requirement(UserProfile(), "QoS가 포함된 상품").requires_qos is True
    assert _apply_explicit_qos_requirement(UserProfile(), "소진 후 속도가 있는 요금제").requires_qos is True
    assert _apply_explicit_qos_requirement(UserProfile(), "데이터 소진 후에도 사용할 수 있는 요금제").requires_qos is True
    assert _apply_explicit_qos_requirement(UserProfile(), "QoS는 없어도 돼").requires_qos is None
    assert _apply_explicit_qos_requirement(
        UserProfile(requires_qos=True), "QoS 있는 요금제\nQoS는 없어도 돼"
    ).requires_qos is None
    followup = _apply_explicit_qos_requirement(
        _apply_explicit_data_max(UserProfile(), "50GB이하 요금제 추천해줘\nqos있는 요금제를 추천해줘"),
        "50GB이하 요금제 추천해줘\nqos있는 요금제를 추천해줘",
    )
    assert followup.max_data_gb == 50
    assert followup.requires_qos is True

    vague_benefit = _apply_benefit_preference_question(
        UserProfile(reference_plan_name="초이스90", comparison_goals=["better"]),
        "현재 요금제보다 혜택이 더 괜찮은 요금제를 추천해줘",
    )
    assert vague_benefit.needs_user_input is True
    assert vague_benefit.followup_question == BENEFIT_PREFERENCE_QUESTION
    assert vague_benefit.comparison_goals is None
    assert _BENEFIT_PREFERENCE_MARKER in vague_benefit.ambiguous
    assert _apply_benefit_preference_question(
        UserProfile(reference_plan_name="초이스90"), "혜택을 우선해서 추천해줘"
    ).needs_user_input is True
    assert _apply_benefit_preference_question(
        UserProfile(reference_plan_name="초이스90"), "혜택이 많은 순으로 추천해줘"
    ).needs_user_input is True

    specific_benefit = _apply_benefit_preference_question(
        UserProfile(
            reference_plan_name="초이스90",
            wanted_benefit_categories=["영상/OTT"],
            comparison_goals=["better"],
        ),
        "현재보다 OTT 혜택이 좋은 요금제를 추천해줘",
    )
    assert specific_benefit.needs_user_input is False
    assert specific_benefit.comparison_goals is None
    assert specific_benefit.wanted_benefit_categories == ["영상/OTT"]

    # 'N만원 이하'는 그 금액을 포함한다. 29,999 로 잘리면 정확히 30,000원인 상품이 탈락한다.
    assert _repair_budget_bounds(UserProfile(budget_max_won=29999), "월 3만원 이하로").budget_max_won == 30000
    assert _repair_budget_bounds(UserProfile(), "3만원까지 가능해").budget_max_won == 30000
    assert _repair_budget_bounds(UserProfile(), "3만원 미만으로").budget_max_won == 29999
    assert _repair_budget_bounds(UserProfile(budget_min_won=0), "아무 말").budget_min_won is None
    assert _repair_budget_bounds(UserProfile(budget_max_won=39999), "3만원대로").budget_max_won == 39999
    # 직접 선택 입력이 만드는 문장. 천 단위 쉼표를 놓치면 상한이 0원이 된다.
    assert _repair_budget_bounds(UserProfile(), "월 예산 30,000원 이하").budget_max_won == 30000

    # '3만원 이하'에 하한이 붙으면 정확히 3만원인 상품만 남는다 (후보 578건 -> 4건)
    both = _repair_budget_bounds(
        UserProfile(budget_min_won=30000, budget_max_won=39999), "월 데이터 20GB 이상, 요금 3만원 이하로 추천해줘"
    )
    assert both.budget_min_won is None and both.budget_max_won == 30000, both
    # 사용자가 직접 말한 하한은 지운다
    kept_min = _repair_budget_bounds(
        UserProfile(budget_min_won=20000, budget_max_won=30000), "2만원 이상 3만원 이하로"
    )
    assert kept_min.budget_min_won == 20000 and kept_min.budget_max_won == 30000, kept_min
    assert _repair_budget_bounds(UserProfile(budget_min_won=30000), "3만원대로").budget_min_won == 30000

    # 혜택 조건의 세기: 희망은 선호, '~만'은 필수
    wish = _apply_benefit_constraint_strength(
        UserProfile(wanted_benefits=["넷플릭스"], hard_constraints=["wanted_benefits"]),
        "넷플릭스 포함이면 좋겠어",
    )
    assert wish.wanted_benefits == ["넷플릭스"], wish
    assert "wanted_benefits" not in wish.hard_constraints, wish.hard_constraints
    must = _apply_benefit_constraint_strength(
        UserProfile(wanted_benefits=["넷플릭스"], hard_constraints=[]),
        "넷플릭스 포함 요금제만 원해",
    )
    assert "wanted_benefits" in must.hard_constraints, must.hard_constraints
    # 혜택 조건이 없으면 아무것도 하지 않는다
    assert _apply_benefit_constraint_strength(UserProfile(), "넷플릭스 이용 중이야").hard_constraints == []

    # 'null' 문자열은 값이 아니다. 이걸 요금제명으로 DB 를 뒤지다 파이프라인이 멈춘 적이 있다.
    junk = _drop_placeholder_text(
        UserProfile(reference_plan_name="null", mvno_brand="없음", notes="N/A"), ""
    )
    assert junk.reference_plan_name is None and junk.mvno_brand is None and junk.notes is None
    assert _drop_placeholder_text(UserProfile(reference_plan_name="초이스90"), "").reference_plan_name == "초이스90"

    # 정규화가 선호 완화를 되돌리면 안 된다 (hard_constraints 를 값 유무로 다시 만들기 때문)
    wished = UserProfile(wanted_benefits=["넷플릭스"])
    normalized = _normalize_profile(wished)
    assert "wanted_benefits" in normalized.hard_constraints, "정규화는 값이 있으면 필수로 본다"
    for repair in _POST_NORMALIZE_REPAIRS:
        normalized = repair(normalized, "넷플릭스 포함이면 좋겠어")
    assert "wanted_benefits" not in normalized.hard_constraints, normalized.hard_constraints
    assert normalized.wanted_benefits == ["넷플릭스"], "값 자체는 살아 있어야 점수에 반영된다"
    assert _apply_benefit_constraint_strength not in _REPAIRS, "정규화 전에 돌면 무효가 된다"

    # 현재 납부액은 예산 상한이 아니다
    asked = "지금 쓰는 요금제가 월 3만원인데 바꾸는 게 나을까?"
    kept = _apply_reference_fee(
        UserProfile(budget_max_won=30000, hard_constraints=["budget_max_won"]), asked
    )
    assert kept.reference_fee_won == 30000, kept
    assert kept.budget_max_won is None and kept.hard_constraints == [], kept
    # 상한을 직접 말했으면 그대로 둔다
    both = _apply_reference_fee(
        UserProfile(budget_max_won=20000, hard_constraints=["budget_max_won"]),
        "지금 3만원 내는데 2만원 이하로 줄이고 싶어",
    )
    assert both.reference_fee_won == 30000 and both.budget_max_won == 20000, both
    # 현재 요금 하나만 말했으면 거기서 흘러나온 예산은 전부 지운다
    spec = _apply_reference_fee(
        UserProfile(budget_min_won=20000, budget_max_won=29999,
                    hard_constraints=["budget_min_won", "budget_max_won", "min_data_gb"]),
        "지금 월 2만원에 데이터 100GB 쓰고 있어. 바꾸는 게 나을까?",
    )
    assert spec.reference_fee_won == 20000, spec
    assert spec.budget_min_won is None and spec.budget_max_won is None, spec
    assert spec.hard_constraints == ["min_data_gb"], spec.hard_constraints
    # 금액을 둘 말했으면 예산 쪽은 손대지 않는다
    two = _apply_reference_fee(
        UserProfile(budget_max_won=20000, hard_constraints=["budget_max_won"]),
        "지금 3만원 내는데 2만원짜리 있어?",
    )
    assert two.reference_fee_won == 30000 and two.budget_max_won == 20000, two

    # 현재 요금 언급이 없으면 아무것도 하지 않는다
    plain = _apply_reference_fee(UserProfile(budget_max_won=30000), "3만원 이하로 추천해줘")
    assert plain.reference_fee_won is None and plain.budget_max_won == 30000
    assert _apply_reference_fee(UserProfile(), "월 5만원에 50GB 사용 중").reference_fee_won == 50000

    # 수치 없는 데이터 여유 희망은 버리지 말고 가중치로 남긴다
    wish = _apply_soft_data_preference(UserProfile(), "월 3만원 이하로. 가능하면 데이터가 넉넉했으면 좋겠어")
    assert wish.priorities == ["data"], wish.priorities
    assert wish.min_data_gb is None, "희망은 필터가 아니다"
    # 수치를 말했으면 그쪽이 우선이라 가중치를 덧붙이지 않는다
    assert _apply_soft_data_preference(UserProfile(min_data_gb=20), "데이터 넉넉하게 20GB 이상").priorities is None
    assert _apply_soft_data_preference(UserProfile(), "3만원 이하").priorities is None
    # 보정 순서: 정렬 요구 제거가 먼저, 데이터 희망 반영이 나중
    order = list(_REPAIRS)
    assert order.index(_drop_inferred_priorities) < order.index(_apply_soft_data_preference)

    # 결과 화면의 LTE/5G 우선은 후보 필터가 아니다. 마지막 선택이 앞선 선택을 덮는다.
    preferred = _apply_network_preference(
        UserProfile(network_gen="5G"),
        "통신 세대 우선순위는 LTE로 하고, 5G도 후보에 포함해줘.",
    )
    assert preferred.network_preference == "LTE" and preferred.network_gen is None, preferred
    cleared = _apply_network_preference(
        UserProfile(network_preference="LTE"),
        "통신 세대 우선순위는 LTE로 하고, 5G도 후보에 포함해줘.\n"
        "통신 세대 우선순위는 상관없음으로 하고, LTE와 5G를 동등하게 비교해줘.",
    )
    assert cleared.network_preference is None and cleared.network_gen is None, cleared

    # 예산 문장은 정렬 요구가 아니다 (priorities 가 붙으면 가중치가 한 축으로 쏠린다)
    assert _drop_inferred_priorities(UserProfile(priorities=["price"]), "월 3만원 이하로 추천해주세요").priorities is None
    for text in ("제일 싼 걸로", "가격을 최우선으로", "데이터 많은 순으로", "가성비 좋은 걸로"):
        assert _drop_inferred_priorities(UserProfile(priorities=["price"]), text).priorities == ["price"], text

    # 나이는 가입 자격 판정용으로만 보존한다
    assert _apply_user_age(UserProfile(), "저는 20대예요").user_age is None
    assert _apply_user_age(UserProfile(), "만 24세입니다").user_age == 24
    assert _apply_user_age(UserProfile(), "28살이에요").user_age == 28
    assert _apply_user_age(UserProfile(), "데이터 20GB 정도").user_age is None
    assert _apply_user_age(UserProfile(user_age=30), "20대").user_age is None

    # hard_constraints 는 값이 있는 필터 필드만
    normalized = _normalize_profile(UserProfile(budget_max_won=30000, voice_unlimited=True))
    assert set(normalized.hard_constraints) == {"budget_max_won", "voice_unlimited"}
    assert AGE_UNKNOWN_ASSUMPTION in normalized.assumptions
    assert AGE_UNKNOWN_ASSUMPTION not in _normalize_profile(UserProfile(user_age=25)).assumptions

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
    assert named_video.estimated_monthly_data_gb == 65.5
    assert named_video.min_qos_mbps is None

    print("self-check ok")
