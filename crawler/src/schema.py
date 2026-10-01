"""통신 3사(KT/SKT/LGU+) + 모요(MVNO) 요금제 통합 스키마.

요금제 하나가 "넷플릭스/유튜브/디즈니+/티빙… 중 택1"처럼 선택지를 최대 14개까지
갖는다. 한 행에 파이프로 밀어넣으면 "넷플릭스 주는 요금제 찾기"조차 문자열
검색이 되므로, 혜택은 별도 long-format 테이블로 분리했다.

- plans.csv   : 요금제 1개 = 1행 (혜택은 요약 컬럼으로만)
- benefits.csv: 요금제 1개 × 혜택 1개 = 1행 (조인키: plan_id)

plan_id 형식
- KT   : ItemCode_요금제명[_연령] (예: 1693_베이직100)
- SKT  : NA로 시작하는 상품코드 (예: NA00009818)
- LGU+ : Z/LPZ로 시작하는 상품코드 (예: Z202605251)
- 모요 : URL의 숫자 ID (예: 30954)
사이트에 고유 코드가 없어 요금제명을 대신 쓴 경우 plan_id_type=name_based.

carrier_type은 "어느 채널에서 수집했나"다. 너겟(LGU+)/다이렉트(SKT)/요고(KT)는
통신사 온라인전용 요금제지만 모요에도 올라와 있어서, 모요판(MVNO)과 통신사
사이트판(MNO) **두 행이 모두 존재**한다(2026-08-13 기준 45건). 요금제 개수·
평균가를 셀 때는 이 중복을 감안해야 한다.
"""
import csv
import re
from pathlib import Path

# 경로는 이 파일 위치 기준으로 잡는다. 상대경로면 프로젝트 루트에서 실행할 때만 동작한다.
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent
DATA_DIR = BASE_DIR / "data"
RAW_CACHE_DIR = DATA_DIR / "raw_cache"   # 사이트 원본 HTML/JSON
INTERIM_DIR = DATA_DIR / "interim"       # 사이트별 중간 CSV
# 크롤러가 만든 최종 CSV. **추천 서비스가 읽는 루트 data/ 와 다른 곳이다.**
# 예전에는 이 값이 PROJECT_DIR/"data" 라서 수집 파이프라인이 추천 모델의 입력을
# 직접 덮어썼다. 수집 결과가 흔들리면 추천 결과도 같이 흔들려 비교가 불가능해진다.
# 루트로의 반영은 사람이 promote 로 명시적으로 한다(refresh_plans.py 참고).
FINAL_DIR = DATA_DIR / "final"            # 합친 최종 CSV (크롤러 출력)
SERVING_DIR = PROJECT_DIR / "data"        # 추천 서비스가 읽는 고정 입력


def cache_dir(site: str) -> Path:
    return RAW_CACHE_DIR / site


def interim_path(name: str) -> Path:
    return INTERIM_DIR / name


def final_path(name: str) -> Path:
    return FINAL_DIR / name


def serving_path(name: str) -> Path:
    """추천 서비스가 실제로 읽는 경로. 수집 파이프라인은 여기에 자동으로 쓰지 않는다."""
    return SERVING_DIR / name


PLAN_COLUMNS = [
    # --- 식별 ---
    "carrier_type",            # MNO / MVNO
    "host_mno",                # 실제 망 제공사
    "mvno_brand",
    "plan_id",
    # 택1 혜택을 선택지별 행으로 펼치기 전 원본 요금제의 id. 펼치지 않은 행은
    # plan_id와 같다. "실제 요금제 수"는 nunique(base_plan_id), "선택지까지
    # 포함한 조합 수"는 행 수로 센다.
    "base_plan_id",
    "plan_id_type",            # official_code / name_based
    "plan_name",
    "selected_option",         # 이 행이 택1에서 고른 선택지 이름. 택1 없으면 빈값
    "plan_category",           # 사이트 내 분류/탭명
    "is_online_only",
    "age_condition",
    # 가입 제한 원문(예: "번호이동만 가입 가능"). 모요만 채운다 - 3사 사이트에는
    # 이런 배너가 없다(제한이 없어서가 아니라 텍스트로 안 적혀 있어서다).
    "signup_notice",
    # --- 스펙 ---
    # 데이터/테더링은 base(나이 조건과 무관한 기본량) + extra(나이 조건 추가분)
    # = 총량 3단으로 나눠 담는다. extra가 붙는 건 KT의 Y덤/스쿨덤/65+덤/75+덤뿐.
    "network_gen",             # 5G / LTE / 3G
    "base_data_gb",            # 무제한이면 빈값
    "extra_data_gb",
    "data_gb",                 # 총 제공량, 무제한이면 빈값
    "data_unlimited",
    "data_throttle_speed",     # 소진 후 제어 속도 (예: 5Mbps)
    "daily_data_gb",
    "base_tethering_gb",
    "extra_tethering_gb",
    "tethering_gb",
    # tethering_gb가 비는 이유가 셋인데 의미가 정반대라 따로 남긴다.
    # quota=별도 한도 있음 / within_data=한도 없이 기본 데이터에서 차감 /
    # unsupported=테더링 못 씀 / undisclosed=사이트가 값을 안 알려줌.
    # 모요만 넷을 구분하고, 3사는 값 유무로 quota/undisclosed만 채운다.
    "tethering_support",
    "voice_unlimited",
    "voice_minutes",
    "voice_extra_minutes",     # 영상/부가통화(분)
    "sms_unlimited",
    "sms_count",
    # --- 가격 ---
    "monthly_fee",             # 정가 월정액(원)
    "discounted_fee",          # 대표 할인가(원) = 실제 청구액
    "discount_type",
    "discount_period_months",
    # 모요 상세의 "페이백 포함하면" 체감가. 청구액이 아니라 페이백을 뺀 표시가라
    # discounted_fee 와 절대 섞지 않는다. 페이백 지급액·기간은 benefits.csv 쪽에
    # 월액(benefit_value_won) + 개월수(benefit_months)로 따로 들어간다.
    "payback_included_fee",
    # 상세의 "월 납부액"을 실제로 읽어 왔는가. False면 목록 카드 값만 있는 것이고
    # 페이백 상품이면 표시가일 수 있다 - 추천/총비용 계산에서 빼는 근거가 된다.
    "billing_price_verified",
    # --- 혜택 요약 (상세는 benefits.csv) ---
    "benefit_count",
    "ott_option_count",
    "ott_options",             # ' | ' 구분
    "membership_grade",
    "smart_device_benefit",
    "extra_data_benefit",
    "gift_benefit",
    # --- 참고 ---
    # 목록 카드의 "N명이 선택". 모요만 채운다. "10+명이 선택"(하한 표기)도 정수로
    # 그대로 저장하므로 값이 실제보다 낮게 잡힐 수 있다.
    "subscriber_count",
    # --- 출처 ---
    "source_url",
    "crawled_at",
]


def total_data_gb(row: dict):
    """`data_gb`에 넣을 월 환산 총 제공량 = 사이트 월 총량 + daily_data_gb * 30.

    사이트 표기가 "월 11GB + 매일 2GB"처럼 단위가 섞여 있어서, 월 총량만 담으면
    일 단위 제공분이 통째로 빠진다(11GB로만 보인다). 원래 값은 interim CSV와
    `daily_data_gb`에 남으므로 `data_gb - daily_data_gb * 30`으로 되돌릴 수 있다.
    무제한과 데이터 미제공은 빈값.

    ponytail: 한 달을 30일 고정으로 본다. 일 단위 제공량은 이월이 안 되므로
    실사용 상한은 이 값보다 낮다 - 일 단위 요금제가 과대추천되면 계수를 붙인다.
    """
    if str(row.get("data_unlimited", "")) == "True":
        return ""

    def _num(value):
        text = str(value if value is not None else "").strip()
        return float(text) if text else None

    monthly, daily = _num(row.get("data_gb")), _num(row.get("daily_data_gb"))
    if monthly is None and daily is None:
        return ""
    return round((monthly or 0) + (daily or 0) * 30, 3)


# 4개 사이트 공통 분류 체계. benefit_category에 이 중 하나가 들어간다.
BENEFIT_CATEGORIES = [
    "영상/OTT",
    "음악/오디오",
    "도서/콘텐츠",
    "제휴서비스",
    "복합/선택혜택",
    "교육/AI서비스",
    "멤버십",
    "스마트기기",
    "스마트기기 회선/데이터쉐어링",
    "추가데이터",
    "페이백",
    "포인트/적립",
    "상품권/사은품",
    "쿠폰/할인",
    "유심/배송비",
    "요금할인",
    "로밍",
    "보험/안심",
    "기타",
]

BENEFIT_COLUMNS = [
    "plan_id",             # plans.csv와 조인하는 키
    "host_mno",
    "plan_name",
    "benefit_category",
    # 대표 분류는 하나만 두되, 복합 혜택은 검색될 분류를 파이프로 모두 적는다.
    "benefit_search_categories",
    "benefit_name",        # 사이트에 적힌 혜택명 그대로
    "benefit_service",     # 정규화한 서비스명 (예: "넷플릭스"). 못 찾으면 빈값
    "benefit_tier",        # 구독 등급. 없으면 빈값
    # 혜택 정가/시장가(원) - 모르면 빈값. **1회 지급액 또는 한 달치 금액**이며
    # 총액이 아니다. 총액으로 접으면 기간이 사라져서, 소비하는 쪽이 "6개월로 나누면
    # 되겠지" 하고 12개월짜리 페이백을 두 배로 계산한다(실제로 그랬다).
    "benefit_value_won",
    # 이 금액을 어떻게 받는가. monthly = 매달 반복, one_off = 한 번만.
    # 빈값이면 판단 불가이므로 소비하는 쪽이 보수적으로 처리해야 한다.
    "benefit_value_basis",
    # 반복 혜택이 몇 개월 제공되는지. 빈값 = 무기한/평생 또는 미상.
    # one_off 에는 의미가 없어 비워 둔다.
    "benefit_months",
    # 추가데이터 혜택이 몇 GB인지. 1,077행 중 1,029건이 이름 문자열에만 있었다.
    # 데이터 혜택이 아니면 빈값.
    "benefit_data_gb",
    "user_pay_won",        # 사용자 실부담금(원). 0이면 완전 무료
    "is_selectable",       # true면 같은 select_group 안에서 택1
    "select_group",
    # 이 혜택을 받으려면 충족해야 하는 **배타적 전제조건**. 같은 값을 가진 혜택끼리는
    # 함께 받을 수 있고, 값이 다르면 동시에 못 받는다(유심을 쿠팡에서 사면서 동시에
    # KT 바로유심으로 살 수는 없다). select_group이 "그룹 안에서 택1"인 것과 달리
    # 이 열은 "같은 값끼리 합산, 다른 값끼리 택1"이라 따로 둔다.
    "benefit_condition",
    "benefit_detail",      # 원문 설명
    "source_url",
]

# 같은 서비스를 사이트마다 다르게 적는다(넷플릭스 / Netflix / T 우주 Netflix …).
# 위에서부터 먼저 매칭되는 걸 쓰므로 순서가 중요하다(티빙&웨이브 -> 티빙).
SERVICE_ALIASES = [
    ("넷플릭스", ("넷플릭스", "netflix")),
    ("유튜브 프리미엄", ("유튜브", "youtube")),
    ("디즈니+", ("디즈니",)),
    ("티빙", ("티빙",)),
    ("웨이브", ("웨이브", "wavve")),
    ("왓챠", ("왓챠", "watcha")),
    ("데일리플러스", ("데일리",)),
    ("밀리의서재", ("밀리",)),
    ("지니뮤직", ("지니",)),
    ("FLO", ("flo",)),
    ("구글 원", ("구글 원", "구글원", "google one")),
    ("Google AI", ("google ai", "googleai", "구글 ai", "구글ai", "ai 구독")),
    ("T 우주", ("t 우주", "우주패스")),
    ("위버스", ("위버스",)),
    ("가전구독", ("가전구독",)),
    ("조선일보", ("조선일보",)),
    ("더중앙플러스", ("더중앙플러스",)),
    ("YES24 크레마클럽", ("예스24 크레마클럽", "yes24 크레마클럽", "크레마클럽")),
    ("네이버웹툰", ("네이버웹툰",)),
    ("카카오 이모티콘 플러스", ("카카오이모티콘 플러스", "이모티콘플러스")),
    ("교보문고", ("교보문고",)),
    ("모아진", ("모아진",)),
    ("북앤라이프", ("북앤라이프",)),
    ("폰케어", ("폰케어",)),
    ("삼성 디바이스", ("삼성",)),
    ("애플 디바이스", ("애플",)),
    ("토스미·오픽미", ("토스미", "오픽미")),
    ("SNOW", ("snow 앱", "스노우 앱")),
]


def normalize_service(benefit_name: str) -> str:
    """혜택명 -> 대표 서비스명. 매칭 안 되면 빈 문자열."""
    text = (benefit_name or "").lower()
    for canonical, keywords in SERVICE_ALIASES:
        if any(k in text for k in keywords):
            return canonical
    return ""


# 같은 서비스라도 등급에 따라 시장가가 3배까지 차이난다(넷플릭스 광고형 5,500원 /
# 스탠다드 13,500원 / 프리미엄 17,000원). 긴 것부터 찾아야 "광고형 스탠다드"가
# "스탠다드"로 잘리지 않는다.
BENEFIT_TIERS = ("광고형 스탠다드", "광고형", "프리미엄", "스탠다드", "베이직", "Lite")

# "유튜브 프리미엄"/"YouTube Premium"의 '프리미엄'은 등급이 아니라 상품명 자체다.
# 이걸 등급으로 뽑으면 "넷플릭스 프리미엄"(진짜 상위 등급)과 같은 층위로 묶여버린다.
_PRODUCT_NAME_PREMIUM_RE = re.compile(r"유튜브\s*프리미엄|YouTube\s*Premium", re.IGNORECASE)


# 통신사 멤버십 등급. 혜택명에만 있고 benefit_tier 는 비어 있었다(350행 전부).
# 긴 것부터 찾아야 VVIP 가 VIP 로 잘리지 않는다.
MEMBERSHIP_TIERS = ("VVIP", "VIP", "골드", "실버", "일반")

# "공유데이터 100GB", "데이터쿠폰 20GB", "추가 데이터 10GB 증정" 등.
_BENEFIT_GB_RE = re.compile(r"([\d.]+)\s*GB", re.IGNORECASE)


def extract_data_gb(benefit_name: str, benefit_category: str = "") -> object:
    """추가데이터 혜택의 GB 수량. 데이터 혜택이 아니면 빈 문자열.

    수량이 이름에만 있으면 소비하는 쪽이 정규식을 다시 짜야 하고, 표기가 바뀌면
    조용히 0 이 된다. 수집 시점에 뽑아 컬럼으로 박는다.
    """
    if benefit_category and benefit_category != "추가데이터":
        return ""
    matched = _BENEFIT_GB_RE.search(benefit_name or "")
    if not matched:
        return ""
    try:
        value = float(matched.group(1).rstrip("."))
    except ValueError:
        return ""
    return value if value > 0 else ""


def extract_membership_tier(benefit_name: str) -> str:
    """혜택명에서 멤버십 등급만 뽑는다. 없으면 빈 문자열."""
    text = (benefit_name or "").upper()
    for tier in MEMBERSHIP_TIERS:
        if tier.upper() in text:
            return tier
    return ""


def extract_tier(benefit_name: str, benefit_service: str = "") -> str:
    """혜택명에서 구독 등급만 뽑는다. 등급 표기가 없으면 빈 문자열."""
    text = benefit_name or ""
    service = benefit_service or normalize_service(text)
    if service == "유튜브 프리미엄":
        # 상품명에 박힌 "프리미엄"을 지우고 남은 데서만 등급을 찾는다
        # (그래야 "YouTube Premium Lite"의 Lite는 살아남는다).
        text = _PRODUCT_NAME_PREMIUM_RE.sub(" ", text)
    lowered = text.lower()
    for tier in BENEFIT_TIERS:
        if tier.lower() in lowered:
            return tier
    return ""


# 가입 연령 조건은 표기가 제각각이라 원문 그대로 두면 16종으로 갈라진다
# (KT "청년(Y덤)" / SKT "청년(만 34세 이하)" / LGU+ "만 19세 ~ 35세 미만").
# **상한 나이 기준**으로 통일한다 - 추천에서 실제로 걸리는 건 상한이다.
# 연령이 아닌 자격(외국인, 복지카드)은 그대로 둔다. "만"은 사이트마다 붙기도
# 빠지기도 해서("65세 이상") 필수로 보지 않는다.
_AGE_UPPER_RE = re.compile(r"(\d+)\s*세\s*(이하|미만)")
_AGE_LOWER_RE = re.compile(r"(\d+)\s*세\s*이상")


def normalize_age_condition(text: str) -> str:
    """가입 연령 조건을 "만 N세 이하/이상"으로 통일. 연령이 아니면 원문 유지."""
    text = (text or "").strip()
    if not text:
        return ""

    # "만 4세 ~ 13세 미만"처럼 구간으로 적힌 것은 **상한만** 남긴다.
    # 상한은 마지막에 나오는 "N세 이하/미만"이다(앞쪽 "만 4세"는 하한).
    uppers = _AGE_UPPER_RE.findall(text)
    if uppers:
        num, bound = uppers[-1]
        # "13세 미만" = "12세 이하"로 맞춘다
        age = int(num) - (1 if bound == "미만" else 0)
        prefix = "외국인, " if "외국인" in text else ""
        return f"{prefix}만 {age}세 이하"
    lower = _AGE_LOWER_RE.search(text)
    if lower:
        return f"만 {lower.group(1)}세 이상"
    return text


# 멤버십 등급도 표기가 6종으로 갈라져서("T 멤버십 VIP 혜택" / "24개월간 VIP 등급")
# 등급만 뽑아 통일한다. 원문은 benefit_name에 그대로 남는다.
# VVIP를 먼저 봐야 한다 - "VVIP"에도 "VIP"가 들어있다.
MEMBERSHIP_GRADES = ("VVIP", "VIP")


def normalize_membership_grade(text: str) -> str:
    """'24개월간 VIP 등급' -> 'VIP'. 등급 표기가 없으면 원문을 그대로 돌려준다."""
    upper = (text or "").upper()
    for grade in MEMBERSHIP_GRADES:
        if grade in upper:
            return grade
    return (text or "").strip()


def classify_benefit_name(name: str, default: str = "기타") -> str:
    """혜택 **이름 하나**를 보고 카테고리를 정한다. 못 정하면 default.

    표 헤더로 그룹 전체를 한 카테고리로 묶으면 틀린다. KT "초이스(택1)"에는
    넷플릭스(구독)·폰케어(보험)·삼성 디바이스(기기)가 섞여 있다. 그래서 그룹
    카테고리는 default로만 쓰고, 이름에 단서가 있으면 그걸 우선한다.
    """
    text = _clean(name or "")
    folded = text.casefold()

    # 기존 통합 카테고리를 폴백으로 다시 남기지 않는다. 이름에 더 구체적인 단서가
    # 없던 과거 KT 초이스/플러스 혜택은 제휴서비스로 이관한다. 직전 버전의
    # '디지털/제휴'도 같은 뜻의 레거시 값으로 받아 재크롤링·병합 시 남지 않게 한다.
    if default in {"OTT/구독", "디지털/제휴"}:
        default = "제휴서비스"

    # 한 행 안에서 서로 다른 종류를 고르는 묶음은 첫 번째 서비스 카테고리로
    # 단정하지 않는다. 예: '티빙/지니/밀리'는 영상·음악·도서 중 택1이다.
    # 이런 혼합형은 어느 한 서비스 유형으로 대표시키지 않고 별도 분류한다.
    if any(keyword.casefold() in folded for keyword in MIXED_DIGITAL_KEYWORDS):
        return "복합/선택혜택"

    # AI 학습/시험 서비스는 영상 OTT 선택지가 아니다. 교육 단서를 먼저 확인해야
    # "토스미·오픽미 AI 모의고사"가 디지털 제휴로 섞이지 않는다.
    if any(keyword.casefold() in folded for keyword in EDUCATION_AI_KEYWORDS):
        return "교육/AI서비스"

    if any(keyword.casefold() in folded for keyword in VIDEO_OTT_KEYWORDS):
        return "영상/OTT"
    if any(keyword.casefold() in folded for keyword in MUSIC_AUDIO_KEYWORDS):
        return "음악/오디오"
    if any(keyword.casefold() in folded for keyword in BOOK_CONTENT_KEYWORDS):
        return "도서/콘텐츠"

    # KT 초이스 표에서 옵션명이 서비스명 한 단어로만 오는 경우를 처리한다.
    if folded.strip() in {"삼성", "애플"}:
        return "스마트기기"

    # Google AI/Google One 같은 디지털 서비스와 결합 쿠폰은 영상 OTT와 분리한다.
    if any(keyword.casefold() in folded for keyword in DIGITAL_PARTNER_KEYWORDS):
        return "제휴서비스"

    # '구독'만으로 음악·도서·AI를 영상 OTT로 묶지 않는다.
    if "ott" in folded:
        return "영상/OTT"
    if "구독" in folded:
        return "제휴서비스"
    if any(k in text for k in ("로밍", "해외 eSIM")):
        return "로밍"
    if any(k in text for k in ("보험", "폰케어", "안심박스", "청소년 보호", "집지킴", "돌봄이")):
        return "보험/안심"
    if any(k in text for k in OTHER_KEYWORDS):
        return "기타"
    if any(k in text for k in ("데이터쉐어링", "데이터 쉐어링", "스마트기기 월정액", "스마트기기 이용 요금", "스마트기기 1회선", "스마트기기 2회선")):
        return "스마트기기 회선/데이터쉐어링"
    # 기기값·할부금 할인이 아니라 연결 회선의 월정액 할인이다.
    if re.search(r"\d+\s*대\s*월정액", text):
        return "스마트기기 회선/데이터쉐어링"
    if any(k in text for k in ("디바이스", "워치", "태블릿", "액션캠", "스마트기기")):
        return "스마트기기"
    # 대상이 생략된 숫자 선택 문구만으로 제휴서비스라고 추정하지 않는다.
    # 동일 행의 디바이스 할인 본문은 위 규칙으로 분류하고, 설명 조각은 별도 제거한다.
    if re.search(r"\d+\s*개\s*선택\s*시", text):
        return "기타"
    if any(k in text.upper() for k in MEMBERSHIP_KEYWORDS):
        return "멤버십"
    # 사은품 판정도 추가데이터보다 뒤다. "데이터쿠폰 20GB"(모요 23행)처럼 데이터를
    # 더 주는 혜택에 '쿠폰'이 붙는 이름이 있다.
    if EXTRA_DATA_RE.search(text):
        return "추가데이터"
    # 지급 형태는 서로 대체할 수 없다. 네이버페이 포인트나 쿠폰만 주는 상품을
    # 사용자의 '페이백' 요청에 포함시키지 않는다.
    if any(k in text for k in ("페이백", "캐시백")):
        return "페이백"
    # 네이버페이는 지급 수단이다. '요금 환급'은 페이백으로 보되,
    # 포인트로 환급한다고 명시한 경우는 현금 차감 대상으로 분류하지 않는다.
    if "환급" in text and not (
        any(k in text for k in ("포인트", "적립"))
        or re.search(r"\d[\d,]*\s*P\b", text, re.IGNORECASE)
    ):
        return "페이백"
    if re.search(r"유심(?!사)|배송비|\bUSIM\b", text, re.IGNORECASE):
        return "유심/배송비"
    if "요금할인" in text or "요금 할인" in text:
        return "요금할인"
    if any(k in text for k in ("상품권", "사은품", "증정", "에어팟")):
        return "상품권/사은품"
    if any(k in text for k in ("네이버페이", "포인트", "적립", "S-머니", "Npay")):
        return "포인트/적립"
    if any(k in text for k in ("쿠폰", "할인")):
        # '1대 월정액 할인', '1개 선택 시 할인'처럼 이름에 대상이 생략된
        # 선택지는 원래 크롤러 표의 유형을 유지한다.
        if "쿠폰" not in text and default in {"스마트기기", "제휴서비스"}:
            return default
        return "쿠폰/할인"
    if default == "사은품/페이백":
        return "상품권/사은품"
    return default


def is_duplicate_device_discount_fragment(row: dict) -> bool:
    """KT 디바이스 할인 본문을 다시 쪼갠 선택 수/금액 설명 행만 식별한다."""
    name = str(row.get("benefit_name") or "").strip()
    detail = str(row.get("benefit_detail") or "")
    return bool(
        re.fullmatch(r"[12]개\s*선택\s*시\s*(?:각\s*)?최대\s*\d+천원\s*할인", name)
        and "디바이스 할인" in detail
    )


# 결합·추가·공유로 데이터를 더 주는 혜택. (데이터|결합) 뒤에 (추가|공유|쉐어|결합|용량)이
# 오는 형태로 잡는다. 용량(\d+G)까지 단서로 쓰는 건 '데이터'라는 말이 없는
# "솔로결합(+20GB)" 때문이고, 앞에 '결합/데이터'를 요구하므로 "네이버페이
# 5,000원" 같은 사은품은 걸리지 않는다.
EXTRA_DATA_RE = re.compile(
    r"(?:데이터|결합).*?(?:추가|공유|쉐어|결합|\d+\s*G)"
    r"|(?:추가|공유|쉐어).*?데이터"
)


def canonical_spelling(text: str) -> str:
    """문장 안의 서비스 표기를 대표 표기로 통일한 **비교용** 문자열을 만든다.

      "Netflix 광고형 스탠다드"  ->  "넷플릭스 광고형 스탠다드"

    사람에게 보여줄 값이 아니라 중복 판정용이다.
    """
    out = text or ""
    for canonical, keywords in SERVICE_ALIASES:
        for keyword in keywords:
            out = re.sub(re.escape(keyword), canonical, out, flags=re.IGNORECASE)
    return out


# 구독/제휴 혜택을 실제 서비스 성격별로 나눈다. 각 통신사 크롤러가 같은 목록을
# 공유해야 갱신할 때마다 카테고리가 다시 합쳐지지 않는다.
EDUCATION_AI_KEYWORDS = (
    "토스미", "오픽미", "모의고사",
    "Google AI", "GoogleAI", "구글 AI", "구글AI", "AI프로", "AI 구독",
)

# 서로 다른 카테고리의 선택지가 한 혜택명에 합쳐진 원문 표기. 단일 컬럼에
# 억지로 영상/음악/도서 중 하나만 넣으면 나머지 검색이 왜곡되므로 복합 묶음으로 둔다.
MIXED_DIGITAL_KEYWORDS = (
    "티빙/지니/밀리",
    "지니뮤직, 밀리의서재, 구글원",
    "OTT 1개 또는 디바이스",
)

DIGITAL_PARTNER_KEYWORDS = (
    "Google One", "구글 원", "구글원", "우주", "데일리", "위버스",
    "카카오이모티콘 플러스", "이모티콘플러스", "SNOW 앱",
)

VIDEO_OTT_KEYWORDS = (
    "넷플릭스", "Netflix", "유튜브", "YouTube", "디즈니", "Disney", "티빙",
    "웨이브", "Wavve", "왓챠", "Watcha",
)

MUSIC_AUDIO_KEYWORDS = (
    "지니뮤직", "지니 뮤직", "지니 스마트 음악감상", "FLO", "플로",
    "Spotify", "스포티파이", "VIBE", "바이브",
)

BOOK_CONTENT_KEYWORDS = (
    "밀리의서재", "밀리의 서재", "교보문고", "모아진",
    "조선일보", "종이신문", "더중앙플러스", "중앙일보",
    "예스24", "YES24", "크레마클럽", "네이버웹툰",
    "북앤라이프", "도서문화상품권",
)

# 별도 카테고리가 없는 통신/보호 부가 혜택. 이름에 쿠폰·할인이 같이 있어도
# 지급 수단이 아니라 실제 혜택 성격(로밍/보험/안심 서비스)을 우선한다.
OTHER_KEYWORDS = (
    "보험", "폰케어", "로밍", "안심박스", "청소년 보호",
    "집지킴", "돌봄이",
)

MEMBERSHIP_KEYWORDS = (
    "멤버십", "VIP 등급", "VVIP 등급",
)


def infer_benefit_search_categories(
    name: str, primary_category: str | None = None
) -> list[str]:
    """대표 분류만으로 찾을 수 없는 실제 추가 검색 유형만 돌려준다.

    `benefit_category`는 대표 분류 하나를 유지한다. 대신 이름 하나에 영상·음악·
    도서 등이 함께 있는 복합 혜택은 이 목록에 관련 분류를 모두 넣어, 어느 쪽으로
    질문해도 누락되지 않게 한다.
    """
    text = name or ""
    folded = text.casefold()
    primary = primary_category or classify_benefit_name(text)
    # 일반 행의 대표 분류를 태그에 반복하지 않는다. 복합/선택혜택은 구성
    # 서비스별로 검색되어야 하므로 그 실제 유형만 태그로 기록한다.
    matched: set[str] = set()

    def contains_any(keywords) -> bool:
        return any(keyword.casefold() in folded for keyword in keywords)

    if contains_any(EDUCATION_AI_KEYWORDS):
        matched.add("교육/AI서비스")
    if contains_any(VIDEO_OTT_KEYWORDS) or "ott" in folded:
        matched.add("영상/OTT")
    if contains_any(MUSIC_AUDIO_KEYWORDS):
        matched.add("음악/오디오")
    if contains_any(BOOK_CONTENT_KEYWORDS):
        matched.add("도서/콘텐츠")
    if contains_any(DIGITAL_PARTNER_KEYWORDS):
        matched.add("제휴서비스")
    # 데이터쿠폰은 데이터 제공량이지 금전 할인 쿠폰이 아니다.
    if "쿠폰" in text and primary != "추가데이터":
        matched.add("쿠폰/할인")
    if "상품권" in text:
        matched.add("상품권/사은품")
    # '네이버페이 페이백'의 네이버페이는 지급 수단이다. 페이백을 포인트 적립
    # 혜택으로도 검색되게 하면 서로 다른 지급 방식을 혼동한다.
    if primary != "페이백" and any(
        token in text for token in ("네이버페이", "포인트", "적립", "S-머니")
    ):
        matched.add("포인트/적립")
    if primary != "스마트기기 회선/데이터쉐어링" and (folded.strip() in {"삼성", "애플"} or any(
        keyword.casefold() in folded
        for keyword in ("디바이스", "워치", "태블릿", "액션캠", "스마트기기")
    )):
        matched.add("스마트기기")

    # KT가 세 서비스명을 줄여 쓴 원문. '지니'와 '밀리'만으로 전역 키워드를
    # 넓히면 사람 이름·일반 문구까지 오탐할 수 있어 이 복합 표기에서만 보충한다.
    if "티빙/지니/밀리" in text:
        matched.update({"영상/OTT", "음악/오디오", "도서/콘텐츠"})

    return [category for category in BENEFIT_CATEGORIES if category in matched and category != primary]


# "혜택" 칸에 적혀 있지만 실제로는 "별도로 더 주는 건 없다"는 뜻인 문구들
# ("기본 제공 데이터 내 사용", "테더링+쉐어링 기본 제공량 내"). 혜택으로 잡으면
# 공유데이터를 실제로 별도 제공하는 요금제와 구분이 안 된다. 뒤에 용량이 붙는
# LGU+ 변형("기본 제공량 내 60GB")도 한도 표기일 뿐이고 그 한도는 이미
# tethering_gb에 들어간다.
NON_BENEFIT_PATTERN = re.compile(
    r"기본\s*제공\s*데이터\s*내"
    r"|기본\s*제공\s*량?\s*내"
    r"|기본\s*데이터\s*내"
)


def is_non_benefit(text: str) -> bool:
    """'별도 제공 없음'을 뜻하는 문구면 True."""
    return bool(NON_BENEFIT_PATTERN.search(text or ""))


# 혜택명에 딸려 들어온 링크/버튼 글자.
# ("65세 이상 안심박스 자세히보기" -> "65세 이상 안심박스")
UI_LABEL_RE = re.compile(r"\s*(?:자세히\s*보기|자세히보기|바로\s*가기|바로가기|신청하기|더\s*보기|더보기)\s*$")


def strip_ui_label(text: str) -> str:
    cleaned = UI_LABEL_RE.sub("", text or "").strip()
    return cleaned or (text or "").strip()  # 라벨만 있던 값이면 원문 유지


def to_gb(text: str):
    """'100GB' -> 100.0, '600MB' -> 0.586(GB로 환산), '무제한'/빈값 -> None."""
    text = (text or "").strip()
    if not text or "무제한" in text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*GB", text)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*MB", text)
    if m:
        return round(float(m.group(1)) / 1024, 3)
    return None


def to_won(text: str):
    """'100,000원' -> 100000. `[\\d,]+`로만 찾으면 "네이버페이, 3대..."의 콤마까지
    잡혀 int()가 터지므로 숫자로 시작하는 덩어리만 매칭한다."""
    m = re.search(r"\d[\d,]*", text or "")
    return int(m.group(0).replace(",", "")) if m else None


def extract_speed(text: str) -> str:
    """'다 쓰면 최대 5Mbps' -> '5Mbps'. 없으면 빈 문자열.

    사이트마다 대소문자가 제각각이라(400Kbps / 400kbps) 표기를 맞춰서 내보낸다.
    """
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kbps|mbps|gbps)", text or "", re.I)
    if not m:
        return ""
    unit = m.group(2).lower()
    return f"{m.group(1)}{unit[0].upper()}{unit[1:]}"


def agreement_discount(fee):
    """선택약정 25% 할인가. 사이트가 할인가를 안 알려줄 때 쓰는 3사 공통 표준 요율."""
    if fee is None:
        return "", ""
    return round(fee * 0.75), "선택약정 25% 할인"


# 혜택 금액이 "매달 얼마"인지 "한 번에 얼마"인지, 몇 개월 제공되는지.
# 사이트마다 표기가 달라 한 곳에서만 판정한다.
#   "네이버페이 매달 3.4만원 페이백 (6개월)" -> monthly, 6
#   "네이버페이 매달 5천원 페이백 (평생)"    -> monthly, ""   (무기한)
#   "넷플릭스"                              -> monthly, ""   (구독 정가)
#   "3대 마트 상품권, 네이버페이 2만원"       -> one_off, ""
_RECURRING_WORDS = re.compile(r"매달|매월|월정액|월 ?할인|구독")
_ONE_OFF_WORDS = re.compile(r"상품권|사은품|증정|지급|쿠폰|캐시백|유심|배송비|이벤트")
_MONTHS_RE = re.compile(r"(\d+)\s*개월")
_INDEFINITE_RE = re.compile(r"평생|무기한|계속|약정\s*기간\s*내")


def parse_value_period(benefit_name: str, months: object = "") -> tuple[str, object]:
    """(benefit_value_basis, benefit_months) 를 돌려준다.

    기간을 이름 문자열에만 남겨두면 소비하는 쪽이 파싱을 다시 해야 하고, 표기가
    바뀌면 조용히 틀린다. 수집 시점에 한 번만 판정해서 컬럼으로 박는다.
    """
    text = benefit_name or ""
    if not months:
        matched = _MONTHS_RE.search(text)
        # '평생'이 함께 적혀 있으면 개월 수는 다른 뜻이다(예: 최초 N개월 안내).
        months = int(matched.group(1)) if matched and not _INDEFINITE_RE.search(text) else ""

    if _RECURRING_WORDS.search(text):
        return "monthly", months
    if _ONE_OFF_WORDS.search(text):
        return "one_off", ""
    # 서비스명만 적힌 구독 혜택(넷플릭스 등)은 월 정가로 본다.
    return ("monthly", months) if normalize_service(text) else ("", months)


def make_benefit_row(
    plan_id, host_mno, plan_name, benefit_category, benefit_name, *,
    value_won="", value_basis="", months="", pay_won="",
    selectable=False, select_group="", condition="", detail="", source_url="",
):
    """BENEFIT_COLUMNS 순서에 맞는 혜택 행 하나.

    value_basis/months 를 넘기지 않으면 혜택명에서 추론한다. 크롤러마다 표기가 달라
    한 곳에서 처리하는 편이 안전하다(parse_value_period).
    """
    name = strip_ui_label(benefit_name)
    if value_won != "" and not value_basis:
        value_basis, months = parse_value_period(name, months)
    return {
        "plan_id": plan_id,
        "host_mno": host_mno,
        "plan_name": plan_name,
        "benefit_category": benefit_category,
        # 혜택명에서만 UI 라벨을 뗀다. benefit_detail은 원문 그대로 남긴다.
        "benefit_name": name,
        "benefit_value_won": value_won,
        "benefit_value_basis": value_basis,
        "benefit_months": months,
        "benefit_data_gb": extract_data_gb(name, benefit_category),
        "user_pay_won": pay_won,
        "is_selectable": selectable,
        "select_group": select_group,
        "benefit_condition": condition,
        "benefit_detail": detail,
        "source_url": source_url,
    }


def _clean(value):
    """HTML에서 가져온 값의 줄바꿈·연속공백을 한 줄로 정리한다."""
    if isinstance(value, str):
        # 제로폭 문자(U+200B 등)는 \s에 안 걸려서 눈에 안 보이는 채로 값 끝에
        # 남는다(LGU+ 공유데이터 문구). 남으면 문자열 비교/그룹핑이 조용히 어긋난다.
        value = re.sub(r"[​-‍﻿]", "", value)
        return re.sub(r"\s+", " ", value).strip()
    return value


def _write(rows, path, columns):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for row in rows:
            w.writerow({c: _clean(row.get(c, "")) for c in columns})
    print(f"{Path(path).name}: {len(rows)}행 저장")


def write_plans(rows, path):
    # base_*는 나이 조건별 추가 제공(KT Y덤 등)이 있는 크롤러만 직접 채운다.
    # 그런 개념이 없는 곳은 추가분 없음 = 기본이 곧 총량이라 여기서 채워준다.
    for row in rows:
        if row.get("base_data_gb", "") == "":
            row["base_data_gb"] = row.get("data_gb", "")
        if row.get("base_tethering_gb", "") == "":
            row["base_tethering_gb"] = row.get("tethering_gb", "")
        # 3사는 "지원/미지원" 섹션이 없어서 값 유무로 quota/undisclosed까지만
        # 말할 수 있다. 없는 걸 unsupported라고 단정하면 안 된다 - 미공시일 뿐이다.
        if not row.get("tethering_support"):
            # `!= ""` 로만 보면 파서가 못 찾아 넣은 None이 quota로 찍힌다(MNO 151건).
            has_quota = row.get("tethering_gb") not in ("", None)
            row["tethering_support"] = "quota" if has_quota else "undisclosed"
        # 펼치지 않은 행도 base_plan_id를 채워야 요금제 수를 nunique 하나로 센다.
        if not row.get("base_plan_id"):
            row["base_plan_id"] = row.get("plan_id", "")
    _write(rows, path, PLAN_COLUMNS)


def write_benefits(rows, path):
    rows = [row for row in rows if not is_duplicate_device_discount_fragment(row)]
    for row in rows:
        # 통신사별 파서가 표 헤더에서 추정한 카테고리는 폴백일 뿐이다. 저장 직전에
        # 모든 행을 공통 규칙으로 다시 분류해야 새 크롤링에서도 예전 분류가 살아나지
        # 않는다. KT/SKT/LGU+/모요 크롤러가 모두 이 함수를 거친다.
        row["benefit_category"] = classify_benefit_name(
            row.get("benefit_name", ""), row.get("benefit_category", "") or "기타"
        )
        row["benefit_search_categories"] = " | ".join(
            infer_benefit_search_categories(
                row.get("benefit_name", ""), row["benefit_category"]
            )
        )
        if not row.get("benefit_service"):
            row["benefit_service"] = normalize_service(row.get("benefit_name", ""))
        if not row.get("benefit_tier"):
            name = row.get("benefit_name", "")
            # 멤버십은 구독 등급(광고형/스탠다드)이 아니라 통신사 등급(VVIP/VIP)이다.
            # 같은 컬럼을 쓰되 뽑는 규칙이 다르다.
            row["benefit_tier"] = (
                extract_membership_tier(name)
                if row.get("benefit_category") == "멤버십"
                else extract_tier(name, row["benefit_service"])
            )
        if not row.get("benefit_data_gb"):
            row["benefit_data_gb"] = extract_data_gb(
                row.get("benefit_name", ""), row.get("benefit_category", "")
            )
    _write(rows, path, BENEFIT_COLUMNS)


def expand_select_variants(plan: dict, benefits: list[dict]) -> list[tuple[dict, list[dict]]]:
    """"택1" 혜택을 선택지 하나당 요금제 행 하나로 펼친다.

    SKT는 이미 선택지별로 별도 상품(prodId)을 만들어 놨는데(베스트 Max(넷플릭스)
    NA00009815 …) KT 초이스·LGU+ 프리미엄플러스는 한 요금제 안에 목록으로만
    들어있다. 그대로 두면 "SKT는 요금제마다 OTT 1개, KT는 9개"처럼 보여서 파싱
    방식 때문에 생긴 왜곡이 모델에 들어간다.

    택1 그룹이 여러 개면 **가장 선택지가 많은 그룹 하나만** 펼친다. 전 조합
    (9x3=27)으로 펼치면 행이 급격히 불어나는데, SKT가 쓰는 입도는 "주 OTT 하나"다.

    KT의 "플러스" 그룹은 **절대 primary가 되지 않는다** - 부가 선택지인데,
    경쟁하는 큰 그룹이 없는 페이지에서 "하나뿐이니 가장 크다"는 논리로 primary가
    돼 plan_id가 불필요하게 쪼개졌었다(KT 상품 4개 x 플러스 4개 = 16행).
    LGU+의 "프리미엄플러스"는 "플러스"로 시작하지 않으므로 영향 없다.

    펼친 행의 혜택에는 **고른 선택지 하나 + 택1이 아닌 나머지**만 남는다. 그래서
    혜택 금액을 붙일 때 그냥 sum 하면 되고 "택1은 max로" 같은 예외가 없어진다.
    """
    groups = {}
    for b in benefits:
        if b.get("is_selectable") and b.get("select_group"):
            groups.setdefault(b["select_group"], []).append(b)
    groups = {g: items for g, items in groups.items() if len(items) >= 2}
    expandable = {g: items for g, items in groups.items() if not g.startswith("플러스")}
    if not expandable:
        return [(plan, benefits)]

    primary = max(expandable, key=lambda g: len(expandable[g]))
    chosen_ids = {id(b) for b in groups[primary]}
    others = [b for b in benefits if id(b) not in chosen_ids]

    out = []
    for opt in groups[primary]:
        option_name = opt.get("benefit_name", "")
        option_slug = re.sub(r"\s+", "", option_name)
        new_id = f"{plan['plan_id']}_{option_slug}"
        # SKT가 쓰는 "베스트 Max(넷플릭스)" 표기를 따라간다.
        new_name = f"{plan.get('plan_name', '')} ({option_name})"
        new_benefits = [
            dict(b, plan_id=new_id, plan_name=new_name) for b in ([opt] + others)
        ]
        new_plan = dict(
            plan,
            plan_id=new_id,
            # 연령 변형 행을 다시 펼치는 경우 기존 base_plan_id를 유지한다.
            # 덮어쓰면 진짜 원본("초이스130")까지 한 번에 되짚을 수 없다.
            base_plan_id=plan.get("base_plan_id") or plan["plan_id"],
            selected_option=option_name,
            plan_name=new_name,
        )
        new_plan.update(summarize_benefits(new_benefits))
        out.append((new_plan, new_benefits))
    return out


def summarize_benefits(benefit_rows: list[dict]) -> dict:
    """혜택 long rows -> plans.csv에 넣을 요약 컬럼들."""
    benefit_rows = [
        row for row in benefit_rows if not is_duplicate_device_discount_fragment(row)
    ]
    # ott_option_*은 이름 그대로 영상 OTT만 요약한다. 음악·전자책·AI 구독은
    # 별도 카테고리이며 OTT 개수에 더하지 않는다.
    ott = [b for b in benefit_rows if b["benefit_category"] == "영상/OTT"]
    membership = [b for b in benefit_rows if b["benefit_category"] == "멤버십"]
    smart = [b for b in benefit_rows if b["benefit_category"] in {
        "스마트기기", "스마트기기 회선/데이터쉐어링"
    }]
    data = [b for b in benefit_rows if b["benefit_category"] == "추가데이터"]
    gift_categories = {"페이백", "포인트/적립", "상품권/사은품", "쿠폰/할인", "유심/배송비", "요금할인"}
    gift = [b for b in benefit_rows if b["benefit_category"] in gift_categories]

    def names(rows):
        return " | ".join(dict.fromkeys(r["benefit_name"] for r in rows if r.get("benefit_name")))

    # write_benefits가 benefit_service를 채우는 건 이 함수가 끝난 뒤라, 여기서는
    # 항상 이름에서 새로 뽑아야 한다.
    ott_services = dict.fromkeys(
        filter(None, (normalize_service(r.get("benefit_name", "")) for r in ott))
    )
    grades = dict.fromkeys(
        filter(None, (normalize_membership_grade(r.get("benefit_name", "")) for r in membership))
    )

    return {
        "benefit_count": len(benefit_rows),
        "ott_option_count": len(ott),
        "ott_options": " | ".join(ott_services) or names(ott),
        "membership_grade": " | ".join(grades),
        "smart_device_benefit": names(smart),
        "extra_data_benefit": names(data),
        "gift_benefit": names(gift),
    }


if __name__ == "__main__":
    # 순서가 결과를 바꾸는 규칙이라(OTT vs 사은품, 사은품 vs 추가데이터) 실제로
    # 걸렸던 이름들을 그대로 박아 둔다.
    CASES = [
        # AI·교육·제휴 서비스는 영상 OTT와 구분한다.
        ("구글 AI프로+도미노피자 할인쿠폰", "사은품/페이백", "교육/AI서비스"),
        ("토스미·오픽미 AI 모의고사", "사은품/페이백", "교육/AI서비스"),
        ("GoogleAI Plus (400GB)", "OTT/구독", "교육/AI서비스"),
        ("위버스", "OTT/구독", "제휴서비스"),
        # 영상·음악·도서 구독을 각각 분리한다.
        ("밀리의 서재 평생 구독 0원", "사은품/페이백", "도서/콘텐츠"),
        ("지니 스마트 음악감상", "OTT/구독", "음악/오디오"),
        ("티빙 광고형 스탠다드 제공 (12개월)", "사은품/페이백", "영상/OTT"),
        ("삼성", "OTT/구독", "스마트기기"),
        # 결합/추가 데이터 - '데이터'라는 말이 없는 표기까지
        ("솔로결합(+20GB)", "사은품/페이백", "추가데이터"),
        ("SOLO결합 데이터 10GB 지급", "사은품/페이백", "추가데이터"),
        ("추가데이터 10GB 제공", "사은품/페이백", "추가데이터"),
        ("헬로모바일 결합시, 추가 데이터 10GB 증정", "사은품/페이백", "추가데이터"),
        ("데이터쿠폰 20GB", "사은품/페이백", "추가데이터"),  # '쿠폰'보다 데이터가 먼저
        # 같은 상위 분류였던 포인트·유심·쿠폰·사은품을 구별한다.
        ("네이버페이 5,000원", "사은품/페이백", "포인트/적립"),
        ("일반유심/배송비 무료", "사은품/페이백", "유심/배송비"),
        ("쇼핑라운지 할인쿠폰 5천원권", "기타", "쿠폰/할인"),
        ("에어팟4", "사은품/페이백", "상품권/사은품"),
        ("U+ 멤버십 VIP콕(24개월 간 매월 제공)", "사은품/페이백", "멤버십"),
        ("스마트기기 이용 요금 50% 할인 (1회선)", "사은품/페이백", "스마트기기 회선/데이터쉐어링"),
        ("폰케어 서비스", "기타", "보험/안심"),
    ]
    for name, default, expected in CASES:
        got = classify_benefit_name(name, default)
        assert got == expected, f"{name!r} -> {got} (기대: {expected})"
    print(f"classify_benefit_name 점검 {len(CASES)}건 통과")

    # 크롤러는 실수/None으로, merge는 CSV에서 읽은 문자열로 같은 함수를 부른다.
    GB_CASES = [
        ({"data_gb": 11.0, "daily_data_gb": 2.0}, 71.0),
        ({"data_gb": "11.0", "daily_data_gb": "2.0"}, 71.0),  # merge가 넘기는 문자열
        ({"data_gb": "", "daily_data_gb": 5.0}, 150.0),       # 일 단위만 있는 모요 카드
        ({"data_gb": 100.0, "daily_data_gb": ""}, 100.0),
        ({"data_gb": 0, "daily_data_gb": ""}, 0.0),           # 0GB는 "값 없음"이 아니다
        ({"data_gb": "", "daily_data_gb": "", "data_unlimited": "True"}, ""),
        ({"data_gb": None, "daily_data_gb": None}, ""),       # 데이터 미제공
    ]
    for row, expected in GB_CASES:
        got = total_data_gb(row)
        assert got == expected, f"{row} -> {got} (기대: {expected})"
    print(f"total_data_gb 점검 {len(GB_CASES)}건 통과")
