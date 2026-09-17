# -*- coding: utf-8 -*-
"""CSV 로드와 UserProfile 기반 Hard Filtering.

자연어 해석, 조건 완화, 후보 랭킹·축소는 담당하지 않는다.
조건을 만족한 후보 전체를 반환한다.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"  # CSV 는 프로젝트 루트의 data/ 에 둔다

PLANS_CSV = DATA_DIR / "통신요금제_통합데이터_최종.csv"
BENEFITS_CSV = DATA_DIR / "통신요금제_혜택상세_최종.csv"

_plans = None
_load_lock = threading.Lock()


_CARRIER_NAME_PREFIXES = (
    "kt엠모바일",
    "lg헬로비전",
    "sk텔링크",
    "lg유플러스",
    "lguplus",
    "유플러스",
    "skt",
    "kt",
    "lg",
)


def normalize_plan_name(value: object) -> str:
    """공백·기호·통신사 접두어 차이를 제거한 요금제명 비교 키."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = text.replace("플러스", "+")
    text = re.sub(r"요금제\s*$", "", text)
    compact = re.sub(r"[^0-9a-z가-힣]+", "", text)
    for prefix in _CARRIER_NAME_PREFIXES:
        if compact.startswith(prefix) and len(compact) > len(prefix) + 2:
            compact = compact[len(prefix):]
            break
    return compact


def _plan_name_stem(value: object) -> str:
    """혜택 괄호·제휴 suffix를 뺀 기본 상품명 키."""
    base = re.split(r"[(_]", str(value or ""), maxsplit=1)[0]
    return normalize_plan_name(base)


BENEFIT_CATEGORY_ALIASES = {
    "ott": "영상/OTT",
    "영상": "영상/OTT",
    "스트리밍": "영상/OTT",
    "영상 스트리밍": "영상/OTT",
    "영상/ott": "영상/OTT",
    "음악": "음악/오디오",
    "오디오": "음악/오디오",
    "음악 스트리밍": "음악/오디오",
    "음악/오디오": "음악/오디오",
    "도서": "도서/콘텐츠",
    "전자책": "도서/콘텐츠",
    "도서/콘텐츠": "도서/콘텐츠",
    "디지털": "제휴서비스",
    "제휴": "제휴서비스",
    "제휴 서비스": "제휴서비스",
    "제휴서비스": "제휴서비스",
    "디지털/제휴": "제휴서비스",
    "복합": "복합/선택혜택",
    "선택": "복합/선택혜택",
    "선택 혜택": "복합/선택혜택",
    "복합/선택혜택": "복합/선택혜택",
    "교육": "교육/AI서비스",
    "ai": "교육/AI서비스",
    "ai 서비스": "교육/AI서비스",
    "ai 구독": "교육/AI서비스",
    "ai 교육": "교육/AI서비스",
    "모의고사": "교육/AI서비스",
    "교육/ai서비스": "교육/AI서비스",
    "멤버십": "멤버십",
    "스마트기기": "스마트기기",
    "스마트워치": "스마트기기",
    "워치": "스마트기기",
    "태블릿": "스마트기기",
    "추가데이터": "추가데이터",
    "추가 데이터": "추가데이터",
    "사은품": "사은품/페이백",
    "페이백": "사은품/페이백",
    "상품권": "사은품/페이백",
    "캐시백": "사은품/페이백",
    "사은품/페이백": "사은품/페이백",
    "기타": "기타",
}

BENEFIT_NAME_ALIASES = {
    "디즈니플러스": "디즈니+",
    "디즈니 플러스": "디즈니+",
    "밀리의 서재": "밀리의서재",
    "플로": "flo",
    "구글 원": "구글원",
}


def normalize_benefit_category(value: object) -> str | None:
    """사용자 표현 또는 정식 카테고리명을 DB 카테고리명으로 바꾼다."""
    text = str(value or "").strip().casefold().replace("·", "/").replace(".", "/")
    # '복합/선택혜택'처럼 정식 명칭 자체가 '혜택'으로 끝나는 값은 먼저 확정한다.
    exact = BENEFIT_CATEGORY_ALIASES.get(text)
    if exact:
        return exact
    for suffix in ("포함", "혜택", "제공", "되는", "있는", "카테고리"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)].strip()
    return BENEFIT_CATEGORY_ALIASES.get(text)


def has_benefit_category(categories: object, requested: object) -> bool:
    """후보의 카테고리 목록에 요청 카테고리가 정확히 포함되는지 확인한다."""
    category = normalize_benefit_category(requested)
    return bool(category) and category in (categories or [])


def _merge_search_categories(values) -> list[str]:
    """파이프로 저장된 검색 카테고리를 요금제 단위 중복 없는 목록으로 합친다."""
    merged = []
    for value in values.dropna():
        for category in str(value).split("|"):
            category = category.strip()
            if category and category not in merged:
                merged.append(category)
    return merged


def _speed_to_mbps(value) -> float | None:
    """100Kbps/1Mbps 형태의 QoS 값을 Mbps 숫자로 정규화한다."""
    if pd.isna(value):
        return None
    text = str(value).strip().lower().replace(" ", "")
    try:
        if text.endswith("mbps"):
            return float(text.removesuffix("mbps"))
        if text.endswith("kbps"):
            return float(text.removesuffix("kbps")) / 1000
    except ValueError:
        return None
    return None


# ── 데이터 등급 ───────────────────────────────────────────────
# 임계값은 usage.py 의 GB/h 계수를 회선 속도로 역산한 값이다.
#   720p 1.95GB/h -> 4.44Mbps / 480p 0.6GB/h -> 1.37Mbps / 240p 0.2GB/h -> 0.46Mbps
# KT 무제한의 100/200Kbps는 과거 수집기의 로밍 fallback 혼입 가능성이 있어
# 국내 QoS 분석에서 제외한다. 화질 등급은 계산상 참고값이지 품질 보장이 아니다.
QOS_HD_MBPS = 4.44
QOS_SD_MBPS = 1.37
QOS_LITE_MBPS = 0.46
# 사용자가 "무제한"이라고 말할 때 받아들일 최소 소진 후 속도.
# 1Mbps 면 소진 뒤에도 메신저·웹·음악·저화질 영상이 끊기지 않는다.
UNLIMITED_QOS_MBPS = 1.0

DATA_TIERS = {
    "unlimited_full": "기본량 무제한",
    "qos_hd": "소진 후 HD",
    "qos_sd": "소진 후 480p",
    "qos_lite": "소진 후 저화질",
    "qos_text": "소진 후 문자·웹",
    "capped": "소진 후 정책 미확인",
}


def data_tier(data_unlimited: object, qos_mbps: float | None) -> str:
    """기본 제공량이 아니라 '소진한 뒤에 무엇을 할 수 있는가'로 나눈 등급."""
    if bool(data_unlimited):
        return "unlimited_full"
    # 미수집(NaN)은 어떤 비교에도 False 라 조용히 마지막 등급으로 떨어진다. 먼저 걸러낸다.
    if qos_mbps is None or qos_mbps != qos_mbps or qos_mbps <= 0:
        return "capped"
    if qos_mbps >= QOS_HD_MBPS:
        return "qos_hd"
    if qos_mbps >= QOS_SD_MBPS:
        return "qos_sd"
    if qos_mbps >= QOS_LITE_MBPS:
        return "qos_lite"
    return "qos_text"


def is_effectively_unlimited(data_unlimited: object, qos_mbps: float | None) -> bool:
    """사용자 표현 '무제한'이 가리키는 범위. 완전 무제한 + 쓸 만한 QoS 상품."""
    return bool(data_unlimited) or (qos_mbps is not None and qos_mbps >= UNLIMITED_QOS_MBPS)


def load() -> None:
    """CSV를 읽어 `_plans`를 채운다.

    여러 요청이 동시에 처음 호출하면(스레드풀 등) `_plans`를 완성 전에 서로
    덮어써서 일부 스레드가 `included_benefits` 없는 프레임을 보는 경합이
    있었다 — 락으로 한 스레드만 조립하게 하고, 완성된 프레임만 마지막에
    한 번 `_plans`에 대입한다(조립 도중 상태가 전역에 노출되지 않는다).
    """
    global _plans
    if _plans is not None:
        return
    with _load_lock:
        if _plans is not None:
            return
        plans = pd.read_csv(PLANS_CSV, dtype={"plan_id": str})
        plans["qos_mbps"] = plans["data_throttle_speed"].map(_speed_to_mbps)
        # 원본 CSV는 보존한다. 재수집 전에는 로밍 혼입 의심 값을 국내 속도로 쓰지 않는다.
        plans["qos_source_suspect"] = (
            (plans["carrier_type"] == "MNO") & (plans["host_mno"] == "KT")
            & plans["data_unlimited"] & plans["qos_mbps"].isin([0.1, 0.2])
        )
        plans.loc[plans["qos_source_suspect"], "qos_mbps"] = float("nan")
        plans["data_tier"] = [
            data_tier(unlimited, qos)
            for unlimited, qos in zip(plans["data_unlimited"], plans["qos_mbps"])
        ]
        plans["effective_unlimited"] = [
            is_effectively_unlimited(unlimited, qos)
            for unlimited, qos in zip(plans["data_unlimited"], plans["qos_mbps"])
        ]
        benefits = pd.read_csv(BENEFITS_CSV, dtype={"plan_id": str})
        benefit_lists = (
            benefits.groupby("plan_id")["benefit_name"]
            .apply(lambda values: [str(value) for value in values.dropna().unique()])
        )
        category_column = (
            "benefit_search_categories"
            if "benefit_search_categories" in benefits.columns
            else "benefit_category"
        )
        # 검색 태그에는 복합 혜택의 실제 구성 요소만 저장한다. 대표 분류도 합쳐서
        # '복합 혜택' 자체를 요청하는 경우와 실제 서비스 유형 검색을 모두 지원한다.
        benefits["_all_benefit_categories"] = benefits.apply(
            lambda row: " | ".join(
                value
                for value in (
                    str(row.get("benefit_category", "")).strip(),
                    str(row.get(category_column, "")).strip(),
                )
                if value and value.casefold() != "nan"
            ),
            axis=1,
        )
        benefit_categories = benefits.groupby("plan_id")["_all_benefit_categories"].apply(
            _merge_search_categories
        )
        benefit_details: dict[str, list[dict[str, object]]] = {}
        for row in benefits.to_dict("records"):
            plan_id = str(row.get("plan_id", ""))
            categories = _merge_search_categories(
                pd.Series([row.get("benefit_category"), row.get(category_column)])
            )
            benefit_details.setdefault(plan_id, []).append(
                {
                    "name": str(row.get("benefit_name") or ""),
                    "categories": categories,
                    # 혜택의 원화 가치. 크롤러가 값을 못 채운 혜택은 None 이고 0 원이 아니다.
                    "value_won": _opt_int(row.get("benefit_value_won")),
                    # 크롤러가 채우는 단위·기간. 옛 스키마의 CSV 에는 없어 빈값이 되고,
                    # 그때는 이름 문자열에서 추론한다(_monthly_worth).
                    "value_basis": _opt_text(row.get("benefit_value_basis")),
                    "months": _opt_int(row.get("benefit_months")),
                    "user_pay_won": _opt_int(row.get("user_pay_won")),
                    # 택1 혜택. 같은 select_group 안에서는 하나만 실제로 받는다.
                    "selectable": bool(row.get("is_selectable")),
                    "select_group": _opt_text(row.get("select_group")),
                }
            )
        plans["included_benefits"] = plans["plan_id"].map(benefit_lists).apply(
            lambda value: value if isinstance(value, list) else []
        )
        plans["benefit_categories"] = plans["plan_id"].map(benefit_categories).apply(
            lambda value: value if isinstance(value, list) else []
        )
        plans["benefit_details"] = plans["plan_id"].map(benefit_details).apply(
            lambda value: value if isinstance(value, list) else []
        )
        plans["benefit_search_text"] = (
            plans["ott_options"].fillna("").astype(str)
            + " | "
            + plans["included_benefits"].apply(" | ".join)
        )
        plans["benefit_value_won"] = plans["benefit_details"].map(monthly_benefit_value)
        _plans = plans


def _opt_int(value) -> int | None:
    if value is None or pd.isna(value):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _opt_text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


# 페이백·사은품은 일시금이라 월 단위 비교에 그대로 더하면 과대평가된다.
# 비교 구간(6개월)으로 나눠 월 환산한다. backend.plans.COMPARE_MONTHS 와 같은 값이다.
# 일시금 혜택을 월로 펴 바를 때 쓰는 기본 구간. backend.plans.COMPARE_MONTHS 와 같은 값이다.
BENEFIT_AMORTIZE_MONTHS = 6
_ONE_OFF_CATEGORIES = {"사은품/페이백"}

# benefit_value_won 은 한 컬럼에 두 단위가 섞여 있다.
#   '네이버페이 매달 3.4만원 페이백 (6개월)' -> 204,000 (총액)
#   '네이버페이 매달 5천원 페이백 (평생)'    ->   5,000 (월액)
#   '넷플릭스'                              ->  17,000 (월 구독가)
# 어느 쪽인지는 컬럼에 없고 이름 문자열에만 있다. 그래서 이름을 먼저 읽는다.
_RECURRING_AMOUNT_RE = re.compile(
    r"(?:매달|매월)\s*([\d.,]+)\s*(만원|천원|원)", re.IGNORECASE
)
_BENEFIT_MONTHS_RE = re.compile(r"(\d+)\s*개월")
_MONEY_UNIT = {"만원": 10_000, "천원": 1_000, "원": 1}


def _monthly_worth(
    name: str, worth: float, one_off: bool, basis: str = "", months: int | None = None
) -> float:
    """혜택 하나가 한 달에 얼마짜리인지.

    0. 크롤러가 단위를 적어 뒀으면 그것을 믿는다(benefit_value_basis). 수집 시점에
       판정한 값이라 이름 파싱보다 정확하다.
    1. 이름이 '매달 N원'이라고 말하면 그 값이 곧 월 가치다.
    2. 이름에 기간이 있으면 총액을 그 기간으로 나눈다. 6 으로 고정해서 나누면
       12개월 페이백이 두 배로 부풀려진다(실측: 2.0배).
    3. 기간을 모르는 일시금은 비교 구간으로 편다.
    """
    if basis == "monthly":
        return worth  # 이미 한 달치다. 기간(months)은 얼마나 오래 받는지일 뿐이다.
    if basis == "one_off":
        return worth / BENEFIT_AMORTIZE_MONTHS

    matched = _RECURRING_AMOUNT_RE.search(name)
    if matched:
        amount = float(matched.group(1).replace(",", "")) * _MONEY_UNIT[matched.group(2)]
        if amount > 0:
            return amount

    months = _BENEFIT_MONTHS_RE.search(name)
    if months and int(months.group(1)) > 0:
        return worth / int(months.group(1))

    return worth / BENEFIT_AMORTIZE_MONTHS if one_off else worth


def monthly_benefit_value(details: object) -> int:
    """요금제 하나가 매달 돌려주는 혜택의 원화 가치.

    - value_won 이 비어 있는 혜택은 0 원이 아니라 '가치 미상'이라 합계에서 빠진다.
    - 택1(select_group)은 그룹당 가장 비싼 하나만 센다. 전부 더하면 실제보다 부풀려진다.
    - 총액인지 월액인지는 이름 문자열로 판별한다(_monthly_worth 참고).
    """
    if not isinstance(details, list):
        return 0
    total = 0.0
    best_in_group: dict[str, float] = {}
    for detail in details:
        if not isinstance(detail, dict):
            continue
        value = detail.get("value_won")
        if not value or value <= 0:
            continue
        worth = float(value) - float(detail.get("user_pay_won") or 0)
        if worth <= 0:
            continue
        name = str(detail.get("name") or "")
        one_off = bool(_ONE_OFF_CATEGORIES.intersection(detail.get("categories") or []))
        worth = _monthly_worth(
            name, worth, one_off,
            basis=str(detail.get("value_basis") or ""),
            months=detail.get("months"),
        )
        group = detail.get("select_group") or ""
        if detail.get("selectable") and group:
            best_in_group[group] = max(best_in_group.get(group, 0.0), worth)
        else:
            total += worth
    return int(round(total + sum(best_in_group.values())))


# 사용자가 혜택 이름 뒤에 붙이는 수식어. 붙은 채로 substring 매칭하면 무조건 0건이 된다.
# ('유튜브 프리미엄 포함' 은 어떤 혜택 문구에도 들어 있지 않다)
BENEFIT_SUFFIXES = ("포함", "혜택", "제공", "되는", "있는", "구독")


def normalize_benefit(benefit: object) -> str:
    """비교용 혜택 문자열. 수식어를 떼고 접어서 돌려준다."""
    text = str(benefit).strip()
    changed = True
    while changed:
        changed = False
        for suffix in BENEFIT_SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)].strip()
                changed = True
    folded = text.casefold()
    return BENEFIT_NAME_ALIASES.get(folded, folded)


def has_benefit(search_text: str, benefit: object) -> bool:
    """혜택 문구 안에 요구 혜택이 들어 있는지. 필터와 검증이 같은 규칙을 쓴다."""
    needle = normalize_benefit(benefit)
    haystack = str(search_text).casefold()
    for alias, canonical in BENEFIT_NAME_ALIASES.items():
        haystack = haystack.replace(alias, canonical)
    return bool(needle) and needle in haystack


# 가입 자격 조건. 전체의 12%(331건)가 연령·신분 전용 상품이다.
_AGE_LIMIT_RE = re.compile(r"만\s*(\d+)\s*세\s*(이하|이상|미만|초과)")


def age_eligible(age_condition: object, user_age: int | None) -> bool:
    """가입 자격을 만족하는지. 나이를 모르면 자격 조건이 붙은 상품은 통과시키지 않는다.

    자격 없는 상품을 추천하면 사용자는 가입 단계에서 튕긴다. 조건이 비어 있는
    상품(88%)은 누구나 가입 가능하므로 항상 통과한다.
    """
    condition = str(age_condition or "").strip()
    if not condition or condition.casefold() == "nan":
        return True
    if user_age is None:
        return False
    matched = _AGE_LIMIT_RE.search(condition)
    if not matched:
        # '현역병사'처럼 나이로 판정할 수 없는 조건. 사용자가 직접 고르게 두고 자동 추천에서는 뺀다.
        return False
    limit, comparator = int(matched.group(1)), matched.group(2)
    if comparator == "이하":
        return user_age <= limit
    if comparator == "미만":
        return user_age < limit
    if comparator == "이상":
        return user_age >= limit
    return user_age > limit


def filter_candidates(profile: dict) -> list[dict]:
    """명시 조건을 그대로 적용하고 조건을 만족한 후보 전체를 반환한다."""
    load()
    df = _plans

    # 예산 판단 기준은 discounted_fee(할인 후 실제 납부액).
    # 할인이 없는 요금제는 discounted_fee 가 monthly_fee 와 같고, 0원은 실제 0원 프로모션이다.
    if profile.get("budget_min_won") is not None:
        df = df[df["discounted_fee"] >= profile["budget_min_won"]]
    if profile.get("budget_max_won") is not None:
        df = df[df["discounted_fee"] <= profile["budget_max_won"]]

    # unlimited 계열이 False 인 것은 "무제한이 필수는 아니다" 라는 뜻이지
    # "무제한이면 안 된다" 가 아니다. True 일 때만 필터한다. (sms/voice 도 동일)
    #
    # 일반 '무제한'은 기본량 무제한 또는 1Mbps 이상 QoS형이라는 서비스 정책이다.
    # 1Mbps는 EDA로 입증한 정답이 아닌 명시적인 운영 기준이다.
    if profile.get("data_unlimited") is True:
        df = df[df["effective_unlimited"]] if not profile.get("require_full_unlimited") else df[df["data_unlimited"]]

    # 사용자가 직접 말한 "NGB 이상"만 Hard Constraint로 적용한다.
    # "NGB 정도"와 앱 사용량 추정값은 목표치이므로 후보를 제거하지 않고
    # mcda.py에서 부족/과잉 공급 적합도로 평가한다.
    if profile.get("min_data_gb") is not None:
        required_data_gb = float(profile["min_data_gb"])
        # 무제한은 data_gb가 비어 있어 수치 비교가 성립하지 않는다 → 예상량을 충족으로 본다.
        df = df[df["data_unlimited"] | (df["data_gb"] >= required_data_gb)]

    if profile.get("max_data_gb") is not None:
        # '100GB 이하'는 기본 제공 데이터의 상한이다. 제공량을 특정할 수 없는
        # 무제한 요금제는 상한을 만족한다고 볼 수 없으므로 함께 제외한다.
        df = df[
            ~df["data_unlimited"]
            & df["data_gb"].notna()
            & (df["data_gb"] <= profile["max_data_gb"])
        ]

    if profile.get("min_qos_mbps") is not None:
        df = df[df["qos_mbps"] >= profile["min_qos_mbps"]]
    if profile.get("requires_qos") is True:
        df = df[df["qos_mbps"].notna() & (df["qos_mbps"] > 0)]
    if profile.get("min_tethering_gb") is not None:
        df = df[df["tethering_gb"] >= profile["min_tethering_gb"]]

    if profile.get("voice_unlimited") is True:
        df = df[df["voice_unlimited"]]
    elif profile.get("min_voice_minutes") is not None:
        df = df[
            df["voice_unlimited"]
            | (df["voice_minutes"] >= profile["min_voice_minutes"])
        ]

    if profile.get("sms_unlimited") is True:
        df = df[df["sms_unlimited"]]

    for field in ("carrier_type", "host_mno", "network_gen"):
        if profile.get(field) is not None:
            df = df[df[field] == profile[field]]

    # age_condition 이 비어 있는 요금제는 "가입 조건 없음"(누구나 가입) 이다.
    # 전체의 88% 가 여기 해당하므로 동일 조건만 남기면 후보가 사실상 사라진다.
    if profile.get("age_condition") is not None:
        df = df[df["age_condition"].isna() | (df["age_condition"] == profile["age_condition"])]
    else:
        # 나이를 모르면 전용 상품은 뺀다. 자격 없는 상품을 1순위로 올리면
        # (키즈 요금제가 성인에게 추천된 적이 있다) 추천 전체를 못 믿게 된다.
        user_age = profile.get("user_age")
        df = df[
            df["age_condition"].map(lambda value, age=user_age: age_eligible(value, age))
        ]

    if profile.get("mvno_brand") is not None:
        brand = str(profile["mvno_brand"]).strip().casefold()
        normalized = df["mvno_brand"].fillna("").astype(str).str.strip().str.casefold()
        df = df[normalized == brand]

    benefit_masks = [
        df["benefit_search_text"].map(lambda text, value=benefit: has_benefit(text, value))
        for benefit in profile.get("wanted_benefits") or []
    ]
    for category in profile.get("wanted_benefit_categories") or []:
        normalized = normalize_benefit_category(category)
        if normalized is None:
            return []
        benefit_masks.append(
            df["benefit_categories"].map(
                lambda values, value=normalized: value in values
            )
        )
    if benefit_masks:
        matches = benefit_masks[0]
        if profile.get("benefit_match_mode") == "any":
            for mask in benefit_masks[1:]:
                matches = matches | mask
        else:
            for mask in benefit_masks[1:]:
                matches = matches & mask
        df = df[matches]

    if profile.get("min_discount_period_months") is not None:
        df = df[
            df["discount_period_months"] >= profile["min_discount_period_months"]
        ]

    return [_row_summary(row) for _, row in df.iterrows()]


# 사용자에게 보여줄 조건 이름. 필드명을 그대로 띄우면 읽을 수 없다.
CONSTRAINT_LABELS = {
    "budget_min_won": "최소 요금",
    "budget_max_won": "예산 상한",
    "min_data_gb": "최소 데이터",
    "max_data_gb": "최대 데이터",
    "data_unlimited": "데이터 무제한",
    "require_full_unlimited": "완전 무제한만",
    "min_qos_mbps": "소진 후 최소 속도",
    "requires_qos": "소진 후 사용 가능",
    "min_tethering_gb": "최소 테더링",
    "min_voice_minutes": "최소 통화",
    "voice_unlimited": "통화 무제한",
    "sms_unlimited": "문자 무제한",
    "carrier_type": "사업자 유형",
    "host_mno": "통신망",
    "mvno_brand": "알뜰폰 브랜드",
    "network_gen": "네트워크 세대",
    "age_condition": "가입 대상",
    "wanted_benefits": "요청 혜택",
    "wanted_benefit_categories": "요청 혜택 유형",
    "min_discount_period_months": "최소 할인 기간",
}


def diagnose_empty(profile: dict) -> list[dict]:
    """후보가 0건일 때 어느 조건이 막았는지 조건을 하나씩 빼 보고 알아낸다.

    "조건에 맞는 요금제가 없습니다"만 띄우면 사용자는 무엇을 고쳐야 할지 모른다.
    조건 하나를 풀었을 때 후보가 살아나면 그 조건이 병목이고, 예산이 병목이면
    실제로 가능한 최저 금액까지 같이 알려 준다.
    """
    if filter_candidates(profile):
        return []  # 후보가 있으면 병목도 없다

    blockers: list[dict] = []
    for field in CONSTRAINT_LABELS:
        if profile.get(field) in (None, [], ""):
            continue
        relaxed = {key: value for key, value in profile.items() if key != field}
        survivors = filter_candidates(relaxed)
        if not survivors:
            continue
        entry = {
            "field": field,
            "label": CONSTRAINT_LABELS[field],
            "value": profile[field],
            "candidates": len(survivors),
        }
        if field == "budget_max_won":
            # 이 조건만 걸림돌이면 얼마부터 가능한지가 유일하게 쓸모 있는 답이다.
            entry["minimum_fee"] = min(row["discounted_fee"] for row in survivors)
        blockers.append(entry)
    return sorted(blockers, key=lambda item: -item["candidates"])


def find_candidate(candidates: list[dict], plan_id: str) -> dict | None:
    """고유 plan_id로 후보를 찾는다."""
    return next((candidate for candidate in candidates if candidate["plan_id"] == plan_id), None)


def get_plan(plan_id: str) -> dict | None:
    """plan_id 로 요금제 한 건. 없으면 None."""
    load()
    matched = _plans[_plans["plan_id"] == str(plan_id)]
    return _row_summary(matched.iloc[0]) if not matched.empty else None


def all_plans() -> list[dict]:
    """전체 요금제. 탐색 화면용."""
    load()
    return [_row_summary(row) for _, row in _plans.iterrows()]


def find_plans_by_name(plan_name_query: str) -> list[dict]:
    """원문·정규화 이름의 정확 일치를 우선하고 없으면 부분 일치한다."""
    load()
    query = plan_name_query.strip().casefold()
    if not query:
        return []

    names = _plans["plan_name"].fillna("").astype(str)
    exact = _plans[names.str.strip().str.casefold() == query]
    if not exact.empty:
        return [_row_summary(row) for _, row in exact.iterrows()]

    query_key = normalize_plan_name(plan_name_query)
    if not query_key:
        return []
    name_keys = names.map(normalize_plan_name)
    stem_keys = names.map(_plan_name_stem)
    normalized_exact = _plans[(name_keys == query_key) | (stem_keys == query_key)]
    if not normalized_exact.empty:
        return [_row_summary(row) for _, row in normalized_exact.iterrows()]

    raw_contains = names.str.contains(plan_name_query.strip(), case=False, na=False, regex=False)
    normalized_contains = name_keys.str.contains(query_key, regex=False) | stem_keys.str.contains(
        query_key, regex=False
    )
    matched = _plans[raw_contains | normalized_contains]
    return [_row_summary(row) for _, row in matched.iterrows()]


def find_plans_mentioned_in_text(text: str) -> list[dict]:
    """문장 안에 실제 DB 요금제명이 있으면 가장 구체적인 이름으로 찾는다."""
    load()
    text_key = normalize_plan_name(text)
    if not text_key:
        return []

    names = _plans["plan_name"].fillna("").astype(str)
    stems = names.map(_plan_name_stem)
    mentioned = stems.map(lambda key: len(key) >= 4 and key in text_key)
    if not mentioned.any():
        return []
    longest = max(stems[mentioned].map(len))
    matched = _plans[mentioned & (stems.map(len) == longest)]
    return [_row_summary(row) for _, row in matched.iterrows()]


def _fmt(value) -> str:
    """20.0 -> "20", 4.5 -> "4.5". 사람이 읽는 문자열과 프롬프트 양쪽에 쓴다."""
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _row_summary(r) -> dict:
    if r["data_unlimited"]:
        data = "무제한"
        if pd.notna(r.get("data_throttle_speed")) and not r.get("qos_source_suspect", False):
            data += f" (QoS {r['data_throttle_speed']})"
    elif pd.notna(r.get("data_throttle_speed")):
        data = f"{_fmt(r['data_gb'])}GB + 소진 후 {r['data_throttle_speed']}"
    else:
        data = f"{_fmt(r['data_gb'])}GB"
    return {
        "plan_id": r["plan_id"],
        "plan_name": r["plan_name"],
        "carrier": r["mvno_brand"] if r["carrier_type"] == "MVNO" and pd.notna(r["mvno_brand"]) else r["host_mno"],
        "carrier_type": r["carrier_type"],
        "host_mno": r["host_mno"],
        "mvno_brand": r["mvno_brand"] if pd.notna(r["mvno_brand"]) else "",
        "network_gen": r["network_gen"] if pd.notna(r["network_gen"]) else "",
        "data": data,
        "data_gb": float(r["data_gb"]) if pd.notna(r["data_gb"]) else None,
        "data_unlimited": bool(r["data_unlimited"]),
        "data_tier": r["data_tier"],
        "data_tier_label": DATA_TIERS[r["data_tier"]],
        "effective_unlimited": bool(r["effective_unlimited"]),
        "daily_data_gb": float(r["daily_data_gb"]) if pd.notna(r.get("daily_data_gb")) else None,
        "qos_mbps": float(r["qos_mbps"]) if pd.notna(r["qos_mbps"]) else None,
        "qos_source_suspect": bool(r.get("qos_source_suspect", False)),
        "crawled_at": _opt_text(r.get("crawled_at")),
        "tethering_gb": float(r["tethering_gb"]) if pd.notna(r["tethering_gb"]) else None,
        "voice": "무제한"
        if r["voice_unlimited"]
        else (f"{_fmt(r['voice_minutes'])}분" if pd.notna(r["voice_minutes"]) else "미제공"),
        "voice_minutes": int(r["voice_minutes"]) if pd.notna(r["voice_minutes"]) else None,
        "voice_unlimited": bool(r["voice_unlimited"]),
        "sms_unlimited": bool(r["sms_unlimited"]),
        "sms_count": int(r["sms_count"]) if pd.notna(r["sms_count"]) else None,
        "monthly_fee": int(r["monthly_fee"]),
        "discounted_fee": int(r["discounted_fee"]),
        "discount_type": r["discount_type"] if pd.notna(r["discount_type"]) else "",
        "discount_period_months": int(r["discount_period_months"])
        if pd.notna(r["discount_period_months"])
        else None,
        "ott_options": r["ott_options"] if pd.notna(r["ott_options"]) else "",
        "included_benefits": list(r["included_benefits"]),
        "benefit_categories": list(r["benefit_categories"]),
        "benefit_details": list(r["benefit_details"]),
        "benefit_value_won": int(r["benefit_value_won"]),
        "age_condition": r["age_condition"] if pd.notna(r["age_condition"]) else "",
        "signup_notice": r["signup_notice"] if pd.notna(r.get("signup_notice")) else "",
        "plan_category": r["plan_category"] if pd.notna(r.get("plan_category")) else "",
        "source_url": r["source_url"] if pd.notna(r["source_url"]) else "",
        "is_online_only": bool(r["is_online_only"]),
        "ott_option_count": int(r["ott_option_count"]) if pd.notna(r["ott_option_count"]) else 0,
    }


# LLM 에게 보여줄 필드. 필터용 파생 숫자(data_gb/qos_mbps/voice_minutes 등)는
# 사람이 읽는 data/voice 와 같은 사실의 중복 표현이라 판정을 헷갈리게 해서 뺀다.
SLIM_FIELDS = (
    "plan_id",
    "plan_name",
    "carrier",
    "data",
    "data_tier_label",
    "voice",
    "monthly_fee",
    "discounted_fee",
    "discount_type",
    "discount_period_months",
    "ott_options",
    "included_benefits",
    "benefit_categories",
    "benefit_details",
    "benefit_value_won",
    "age_condition",
    "signup_notice",
    "qos_source_suspect",
)


def slim(rows: list[dict]) -> list[dict]:
    """프롬프트에 넣을 필드만 남긴다."""
    return [{field: row.get(field) for field in SLIM_FIELDS} for row in rows]


if __name__ == "__main__":
    assert normalize_plan_name("SKT 초이스 90 요금제") == normalize_plan_name("초이스90")
    assert normalize_plan_name("베스트 99") == normalize_plan_name("베스트99")
    assert find_plans_by_name("쉐이크 5G 110GB+")
    assert find_plans_by_name("KT엠모바일 모두다 맘껏 100GB+")

    c = filter_candidates(
        {"budget_max_won": 50000, "data_unlimited": True}
    )
    assert len(c) > 0
    assert all(x["discounted_fee"] <= 50000 for x in c)

    # 무제한은 data_gb 가 비어 있어도 최소량 조건에서 탈락하지 않는다
    assert len(filter_candidates({"data_unlimited": True, "min_data_gb": 30})) > 0

    # 이용 패턴 추정값은 목표 적합도이므로 단독으로 후보를 제거하지 않는다.
    estimated = filter_candidates({"estimated_monthly_data_gb": 64.4})
    assert estimated
    assert any(not x["data_unlimited"] and (x.get("data_gb") or 0) < 64.4 for x in estimated)
    stricter = filter_candidates({"min_data_gb": 100, "estimated_monthly_data_gb": 64.4})
    assert all(x["data_unlimited"] or (x.get("data_gb") or 0) >= 100 for x in stricter)
    capped = filter_candidates({"max_data_gb": 100})
    assert capped and all(
        not x["data_unlimited"] and (x.get("data_gb") or 0) <= 100
        for x in capped
    )

    assert "data_gb" not in slim(c)[0] and slim(c)[0]["plan_name"] == c[0]["plan_name"]

    # 사용자 어투의 수식어가 붙어도 같은 요금제를 찾아야 한다
    assert normalize_benefit("유튜브 프리미엄 포함") == "유튜브 프리미엄"
    assert normalize_benefit("넷플릭스 혜택 제공") == "넷플릭스"
    assert has_benefit("유튜브 프리미엄 | 지니뮤직", "유튜브 프리미엄 포함")
    assert not has_benefit("지니뮤직", "유튜브 프리미엄")
    assert not has_benefit("유튜브 프리미엄", "")  # 빈 요구는 매칭하지 않는다
    plain = len(filter_candidates({"wanted_benefits": ["유튜브 프리미엄"]}))
    assert plain > 0
    assert len(filter_candidates({"wanted_benefits": ["유튜브 프리미엄 포함"]})) == plain

    # 포괄적 유형은 카테고리로, 고유 서비스명은 기존 문자열 검색으로 구분한다.
    music = filter_candidates({"wanted_benefit_categories": ["음악"]})
    assert music and all("음악/오디오" in row["benefit_categories"] for row in music)
    assert len(music) > len(filter_candidates({"wanted_benefits": ["음악"]}))
    genie = filter_candidates({"wanted_benefits": ["지니뮤직"]})
    assert genie and len(genie) < len(music)
    assert normalize_benefit_category("스마트워치 혜택") == "스마트기기"
    assert normalize_benefit_category("AI 구독") == "교육/AI서비스"
    assert normalize_benefit_category("복합/선택혜택") == "복합/선택혜택"

    # 복합 혜택은 대표 분류와 별개로 포함된 각 서비스 유형으로도 검색된다.
    mixed_plan_ids = set(
        benefits_id
        for benefits_id in _plans[
            _plans["included_benefits"].map(lambda values: "티빙/지니/밀리" in values)
            # 가입 자격이 붙은 상품은 나이를 모르는 프로필에서 후보로 나오지 않는다
            & _plans["age_condition"].isna()
        ]["plan_id"]
    )
    assert mixed_plan_ids
    assert mixed_plan_ids <= {row["plan_id"] for row in music}
    assert mixed_plan_ids <= {
        row["plan_id"]
        for row in filter_candidates(
            {"wanted_benefit_categories": ["도서/콘텐츠"]}
        )
    }

    disney = filter_candidates({"wanted_benefits": ["디즈니플러스"]})
    books = filter_candidates({"wanted_benefit_categories": ["도서/콘텐츠"]})
    either = filter_candidates(
        {
            "wanted_benefits": ["디즈니플러스"],
            "wanted_benefit_categories": ["도서/콘텐츠"],
            "benefit_match_mode": "any",
        }
    )
    both = filter_candidates(
        {
            "wanted_benefits": ["디즈니플러스"],
            "wanted_benefit_categories": ["도서/콘텐츠"],
            "benefit_match_mode": "all",
        }
    )
    ids = lambda rows: {row["plan_id"] for row in rows}
    assert ids(either) == ids(disney) | ids(books)
    assert ids(both) == ids(disney) & ids(books)
    assert normalize_benefit_category("도서.콘텐츠") == "도서/콘텐츠"

    # 등급: 소진 후 무엇을 할 수 있는가로 나눈다
    assert data_tier(True, 0.1) == "unlimited_full"
    assert data_tier(False, 5.0) == "qos_hd"
    assert data_tier(False, 3.0) == "qos_sd"
    assert data_tier(False, 1.0) == "qos_lite"
    assert data_tier(False, 0.4) == "qos_text"
    assert data_tier(False, None) == "capped"
    assert data_tier(False, float("nan")) == "capped"  # 미수집이 조용히 등급을 받으면 안 된다
    tier_counts = pd.Series([r["data_tier"] for r in all_plans()]).value_counts()
    assert tier_counts.get("capped", 0) > 500, tier_counts.to_dict()
    assert is_effectively_unlimited(False, 5.0) and not is_effectively_unlimited(False, 0.4)

    # '무제한' 요청은 완전 무제한과 QoS형을 함께 본다. 라벨만 보면 3만원 이하가 1건뿐이다.
    strict = [r for r in all_plans() if r["data_unlimited"] and r["discounted_fee"] <= 30000]
    loose = filter_candidates({"data_unlimited": True, "budget_max_won": 30000})
    assert len(loose) > len(strict) * 50, (len(loose), len(strict))
    assert all(r["effective_unlimited"] for r in loose)

    # 가입 자격: 나이를 모르면 전용 상품은 후보에서 빠진다
    assert age_eligible("", None) and age_eligible(None, None)
    assert not age_eligible("만 12세 이하", None)
    assert age_eligible("만 12세 이하", 10) and not age_eligible("만 12세 이하", 30)
    assert age_eligible("만 65세 이상", 70) and not age_eligible("만 65세 이상", 30)
    assert not age_eligible("현역병사", 22)  # 나이로 판정 불가 -> 자동 추천에서 제외
    anyone = filter_candidates({"budget_max_won": 30000})
    assert all(not row["age_condition"] for row in anyone)
    youth = filter_candidates({"budget_max_won": 30000, "user_age": 28})
    assert any(row["age_condition"] == "만 34세 이하" for row in youth)
    assert not any(row["age_condition"] == "만 12세 이하" for row in youth)

    # 혜택 가치: 택1은 그룹당 하나, 일시금은 월 환산, 값 없는 혜택은 0 원 취급하지 않는다
    # 0건일 때 어느 조건이 막았는지 짚어 준다 (넷플릭스 혜택 요금제는 최저 59,000원이다)
    impossible = {"budget_max_won": 30000, "wanted_benefits": ["넷플릭스"], "user_age": 28}
    assert filter_candidates(impossible) == []
    blockers = diagnose_empty(impossible)
    fields = {item["field"] for item in blockers}
    assert fields == {"budget_max_won", "wanted_benefits"}, fields
    budget_blocker = next(item for item in blockers if item["field"] == "budget_max_won")
    assert budget_blocker["minimum_fee"] > 30000
    assert diagnose_empty({"budget_max_won": 30000}) == []  # 후보가 있으면 병목도 없다

    assert monthly_benefit_value([{"name": "A", "value_won": 12000, "categories": ["멤버십"]}]) == 12000
    assert monthly_benefit_value([{"name": "A", "value_won": None, "categories": []}]) == 0
    assert monthly_benefit_value(
        [{"name": "페이백", "value_won": 60000, "categories": ["사은품/페이백"]}]
    ) == 10000
    # benefit_value_won 은 총액과 월액이 섞여 있다. 기간을 6으로 고정해 나누면
    # 12개월 페이백이 정확히 두 배로 부풀려진다(실측 확인).
    assert _monthly_worth("네이버페이 매달 3.4만원 페이백 (6개월)", 204_000, True) == 34_000
    assert _monthly_worth("네이버페이 매달 8천원 페이백 (12개월)", 96_000, True) == 8_000
    assert _monthly_worth("네이버페이 매달 5천원 페이백 (평생)", 5_000, True) == 5_000
    assert _monthly_worth("넷플릭스", 17_000, False) == 17_000
    assert round(_monthly_worth("마트 상품권 2만원", 20_000, True)) == 3_333
    # 크롤러가 단위를 적어 주면 이름 파싱 없이 그대로 쓴다(새 스키마).
    assert _monthly_worth("아무 이름", 8_000, True, basis="monthly", months=12) == 8_000
    assert round(_monthly_worth("아무 이름", 20_000, True, basis="one_off")) == 3_333
    # 옛 스키마(총액 저장)와 새 스키마(월액 저장)가 같은 월 가치로 수렴해야 한다.
    assert _monthly_worth("네이버페이 매달 8천원 페이백 (12개월)", 96_000, True) == _monthly_worth(
        "네이버페이 매달 8천원 페이백 (12개월)", 8_000, True, basis="monthly", months=12
    )
    picked = monthly_benefit_value(
        [
            {"name": "A", "value_won": 9000, "selectable": True, "select_group": "g1", "categories": []},
            {"name": "B", "value_won": 13500, "selectable": True, "select_group": "g1", "categories": []},
        ]
    )
    assert picked == 13500, picked
    assert any(row["benefit_value_won"] > 0 for row in anyone)
    print(f"self-check ok: {len(c)} candidates")
