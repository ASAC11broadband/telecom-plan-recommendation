# -*- coding: utf-8 -*-
"""CSV 로드와 UserProfile 기반 Hard Filtering.

자연어 해석, 조건 완화, 후보 랭킹·축소는 담당하지 않는다.
조건을 만족한 후보 전체를 반환한다.
"""

from __future__ import annotations

import re
import threading
from functools import lru_cache
import unicodedata
from pathlib import Path

import pandas as pd

from .mcda import COMPARE_MONTHS

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"  # CSV 는 프로젝트 루트의 data/ 에 둔다

PLANS_CSV = DATA_DIR / "통신요금제_통합데이터_최종.csv"
BENEFITS_CSV = DATA_DIR / "통신요금제_혜택상세_최종.csv"
# 모요 상세페이지에서 실제 "월 납부액"을 확인해 만든 페이백 상품 정정표.
# crawler/src/verify_payback_prices.py 가 만들고, 사람이 확인한 뒤 여기로 옮긴다.
# 이 표에 있는 상품만 청구액이 확인된 것으로 보고 추천 계산에 넣는다.
VERIFIED_BILLING_CSV = DATA_DIR / "페이백_청구액_검증.csv"

# ── 산출과 적용의 분리 ────────────────────────────────────────
# 서비스가 읽는 PLANS_CSV 는 **최신 수집본**이다(매 수집마다 상품이 들고 난다).
# 반면 기준을 **유도**하는 계산 - 가중치, 무제한 문턱, 등급 임계값 - 은 데이터가
# 바뀔 때마다 답이 흔들리면 안 된다. 그래서 유도는 아래 고정 분석본으로 한다.
#
#   기준 유도(재현되어야 함)  -> BASELINE_DIR   고정
#   서비스 적용·현황 진단     -> PLANS_CSV      최신
#
# 가중치는 이미 weight_bootstrap.json 에 박제돼 있다(노트북 산출물). 이 상수는
# 나머지 산출(분포·문턱 민감도)이 같은 원칙을 따르게 하는 자리다.
BASELINE_DATE = "2026-08-21"
BASELINE_DIR = DATA_DIR / "baseline" / BASELINE_DATE
BASELINE_PLANS_CSV = BASELINE_DIR / "통신요금제_통합데이터_최종.csv"

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
    "스마트기기 회선": "스마트기기 회선/데이터쉐어링",
    "스마트기기 회선/데이터쉐어링": "스마트기기 회선/데이터쉐어링",
    "데이터쉐어링": "스마트기기 회선/데이터쉐어링",
    "데이터 쉐어링": "스마트기기 회선/데이터쉐어링",
    "추가데이터": "추가데이터",
    "추가 데이터": "추가데이터",
    "사은품": "상품권/사은품",
    "상품권": "상품권/사은품",
    "상품권/사은품": "상품권/사은품",
    "페이백": "페이백",
    "캐시백": "페이백",
    "포인트": "포인트/적립",
    "적립": "포인트/적립",
    "포인트/적립": "포인트/적립",
    "쿠폰": "쿠폰/할인",
    "할인쿠폰": "쿠폰/할인",
    "쿠폰/할인": "쿠폰/할인",
    "유심": "유심/배송비",
    "무료 유심": "유심/배송비",
    "배송비": "유심/배송비",
    "유심/배송비": "유심/배송비",
    "요금할인": "요금할인",
    "요금 할인": "요금할인",
    "로밍": "로밍",
    "보험": "보험/안심",
    "안심": "보험/안심",
    "보험/안심": "보험/안심",
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
    """단일 혜택 유형을 검사한다. 스마트기기는 회선 혜택도 포괄한다."""
    category = normalize_benefit_category(requested)
    if category == "스마트기기":
        return bool({"스마트기기", "스마트기기 회선/데이터쉐어링"} & set(categories or []))
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
# 사용자가 "무제한"이라고 말할 때 받아들일 최소 기준. 규제값이 아니라 **서비스 정책값**이다.
# 두 조건을 **함께** 본다.
#  - 속도 10Mbps: 통신 3사는 100GB대 + 5Mbps 상품을 무제한이라 부르지 않는다. 알뜰폰에는 기본량
#    무제한이 없어, 소진 후 10Mbps 군집이 알뜰폰의 '무제한'에 해당하는 최상위 등급이다.
#  - 제공량 100GB: 속도만 보면 '4.5GB + 1Mbps' 같은 소량 상품이 무제한으로 들어온다.
UNLIMITED_MIN_GB = 100.0
UNLIMITED_QOS_MBPS = 10.0

DATA_TIERS = {
    "unlimited_full": "기본량 무제한",
    "qos_hd": "5Mbps",
    "qos_sd": "3Mbps",
    "qos_lite": "1Mbps",
    "qos_text": "400Kbps",
    "capped": "QoS 없음",
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


def is_effectively_unlimited(
    data_unlimited: object, qos_mbps: float | None, data_gb: float | None = None
) -> bool:
    """사용자 표현 '무제한'이 가리키는 범위.

    기본량 무제한이거나, **제공량과 소진 후 속도를 둘 다 충족**하는 QoS형이다.
    속도만 보면 소량 요금제가 섞이고, 제공량만 보면 소진 뒤 문자만 되는 상품이 섞인다.

    제공량을 모르면 무제한으로 치지 않는다. 지금 CSV 에는 그런 행이 없지만
    (크롤러가 일일 제공량을 월 환산해 채운다) 모르는 값을 통과시키지는 않는다.
    """
    if bool(data_unlimited):
        return True
    if qos_mbps is None or qos_mbps < UNLIMITED_QOS_MBPS:
        return False
    return data_gb is not None and float(data_gb) >= UNLIMITED_MIN_GB


@lru_cache(maxsize=1)
def baseline_plans() -> "pd.DataFrame":
    """기준 유도용 고정 분석본. 최신 수집본이 바뀌어도 이 값은 그대로다.

    문턱을 다시 뽑거나 민감도를 보여줄 때 쓴다. 서비스 추천에는 쓰지 않는다 -
    추천은 항상 최신 수집본(`load()`)으로 한다.

    혜택은 붙이지 않는다. 문턱 유도에 필요한 것은 제공량·소진 후 속도·요금뿐이고,
    혜택 조인은 무겁기만 하다. 필요해지면 그때 붙이면 된다.
    """
    frame = pd.read_csv(BASELINE_PLANS_CSV, dtype={"plan_id": str})
    frame["qos_mbps"] = frame["data_throttle_speed"].map(_speed_to_mbps)
    # 최신본과 같은 규칙을 적용해야 두 기준이 비교 가능하다(로밍 혼입 의심 값 제외).
    suspect = (
        (frame["carrier_type"] == "MNO") & (frame["host_mno"] == "KT")
        & frame["data_unlimited"] & frame["qos_mbps"].isin([0.1, 0.2])
    )
    frame.loc[suspect, "qos_mbps"] = float("nan")
    frame["data_tier"] = [
        data_tier(unlimited, qos)
        for unlimited, qos in zip(frame["data_unlimited"], frame["qos_mbps"])
    ]
    frame["effective_unlimited"] = [
        is_effectively_unlimited(unlimited, qos, gb)
        for unlimited, qos, gb in zip(
            frame["data_unlimited"], frame["qos_mbps"], frame["data_gb"]
        )
    ]
    return frame


def _apply_verified_billing(plans) -> None:
    """페이백 상품의 `discounted_fee`를 원문에서 확인한 청구액으로 바로잡는다.

    수집 CSV의 페이백 상품 가격은 모요 목록 카드의 **체감가**(페이백을 뺀 표시가)라
    청구액이 아니다(36334: 카드 7,000원 / 실제 청구 49,000원). 표시가에 페이백을
    도로 더하거나 정가로 치환하면 안 되므로, 상세페이지 "월 납부액"을 실제로 읽어
    온 상품만 정정표를 통해 되돌린다.

    정정표에 없는 페이백 상품은 `billing_price_known=False`로 남아 추천·총비용
    계산에서 빠진다(탐색 목록에는 그대로 보인다).

    페이백 지급액·기간은 요금 할인 기간과 다르므로 가격에 섞지 않는다. 지급 일정은
    `payback_schedule`에 문자열로 남기고, 차감 여부는 혜택 쪽 규칙이 판단한다.
    """
    plans["billing_price_known"] = ~(
        plans["discount_type"].fillna("").str.contains("페이백", regex=False)
    )
    plans["payback_schedule"] = ""
    plans["payback_included_fee"] = None
    if not VERIFIED_BILLING_CSV.exists():
        return
    fixes = pd.read_csv(VERIFIED_BILLING_CSV, dtype={"plan_id": str},
                        encoding="utf-8-sig").set_index("plan_id")
    fixes = fixes[~fixes.index.duplicated()]
    verified = plans["plan_id"].isin(fixes.index)
    if not verified.any():
        return
    for column in ("monthly_fee", "discounted_fee", "discount_type", "discount_period_months",
                   "payback_included_fee", "payback_schedule"):
        mapped = plans["plan_id"].map(fixes[column])
        plans.loc[verified, column] = mapped[verified]
    plans["payback_schedule"] = plans["payback_schedule"].fillna("")
    plans.loc[verified, "billing_price_known"] = True


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
        _apply_verified_billing(plans)
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
            is_effectively_unlimited(unlimited, qos, gb)
            for unlimited, qos, gb in zip(
                plans["data_unlimited"], plans["qos_mbps"], plans["data_gb"]
            )
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
                    "primary_category": _opt_text(row.get("benefit_category")),
                    # 혜택의 원화 가치. 크롤러가 값을 못 채운 혜택은 None 이고 0 원이 아니다.
                    "value_won": _opt_int(row.get("benefit_value_won")),
                    # 크롤러가 채우는 단위·기간. 옛 스키마의 CSV 에는 없어 빈값이 되고,
                    # 그때는 이름 문자열에서 추론한다(_monthly_worth).
                    "value_basis": _opt_text(row.get("benefit_value_basis")),
                    "months": _opt_int(row.get("benefit_months")),
                    "user_pay_won": _opt_int(row.get("user_pay_won")),
                    # 택1 혜택. 같은 select_group 안에서는 하나만 실제로 받는다.
                    "selectable": str(row.get("is_selectable") or "").strip().lower() in {"true", "1", "yes"},
                    "select_group": _opt_text(row.get("select_group")),
                    # 카드 실적·별도 가입 같은 추가 조건. 자동 차감 여부를 여기서 가른다.
                    "condition": _opt_text(row.get("benefit_condition")),
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
        summaries = plans["benefit_details"].map(benefit_summary)
        plans["benefit_value_won"] = summaries.map(lambda s: s["monthly_won"])
        plans["benefit_deductible_won"] = summaries.map(lambda s: s["deductible_won"])
        plans["benefit_value_estimated"] = summaries.map(lambda s: s["estimated"])
        plans["benefit_conditional_count"] = summaries.map(lambda s: s["conditional"])
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
# 비교 구간으로 나눠 월 환산한다. 구간은 agent.mcda.COMPARE_MONTHS 하나로 통일돼 있다
# (추천 가격 효용·화면 총비용·혜택 월 환산이 같은 기간을 써야 서로 비교된다).
BENEFIT_AMORTIZE_MONTHS = COMPARE_MONTHS
_ONE_OFF_CATEGORIES = {"페이백", "포인트/적립", "상품권/사은품", "쿠폰/할인", "유심/배송비"}
# 현금으로 돌려받는 혜택. 사용자가 그 서비스를 쓰는지와 무관하게 납부 총액이 줄어든다.
_CASH_CATEGORIES = {"페이백"}
# 기간을 '끝이 없다'고 못 박은 표현. '기간 미상'과 구분해야 한다.
_INDEFINITE_RE = re.compile(r"평생|무기한|무제한\s*제공|계속\s*제공|약정\s*내내")
# 추가 실적·별도 가입이 필요한 혜택. 자동으로 절약액에서 빼면 안 된다.
_CONDITIONAL_RE = re.compile(
    r"카드|실적|제휴|결합|약정|가입\s*시|신규|번호이동|응모|추첨|이벤트|선착순|별도\s*신청|쿠폰"
)

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


def _benefit_duration(
    name: str, basis: str, months: int | None, one_off: bool = False
) -> tuple[int | None, bool]:
    """(제공 개월 수, 확인됨) — 기간 미상과 무기한 제공은 다른 값이다.

    미상이면 (None, False). 무기한이면 (비교 구간, True) — 비교 구간 내내 받는다.
    일시금은 (1, True) — 한 번 받고 끝이라 기간이 확인된 것과 같다.
    """
    if basis == "one_off":
        return 1, True  # 금액 단위가 명시된 일시금은 이름의 개월 수와 무관하다.
    if months is not None and months > 0:
        return months, True
    if _INDEFINITE_RE.search(name):
        return BENEFIT_AMORTIZE_MONTHS, True
    matched = _BENEFIT_MONTHS_RE.search(name)
    if matched and int(matched.group(1)) > 0:
        return int(matched.group(1)), True
    if basis == "monthly" or re.search(r"매달|매월", name):
        return None, False  # 사은품 카테고리여도 반복 지급 기간을 추정하지 않는다.
    if one_off:
        return 1, True
    return None, False


def _monthly_worth(
    name: str, worth: float, one_off: bool, basis: str = "", months: int | None = None
) -> float:
    """혜택 하나가 비교 구간 동안 한 달 평균 얼마짜리인지.

    0. 크롤러가 단위를 적어 뒀으면 그것을 믿는다(benefit_value_basis). 수집 시점에
       판정한 값이라 이름 파싱보다 정확하다.
    1. 이름이 '매달 N원'이라고 말하면 그 값이 곧 한 달 지급액이다.
    2. 이름에 기간이 있으면 총액을 그 기간으로 나눠 한 달 지급액을 구한다.
    3. 그렇게 구한 월 지급액을 '받는 개월 수 / 비교 구간'만큼만 인정한다.
       6개월만 주는 페이백을 12개월 비교에 그대로 곱하면 지급액이 두 배가 된다.
    4. 기간을 모르면 구간 내내 받는다고 보되, 호출자가 추정임을 표시한다.
    """
    per_month = None
    if basis == "monthly":
        per_month = worth  # 이미 한 달치다.
    elif basis == "one_off":
        per_month = worth  # 일시금 총액. 아래에서 기간 1개월로 펴진다.
    else:
        matched = _RECURRING_AMOUNT_RE.search(name)
        if matched:
            amount = float(matched.group(1).replace(",", "")) * _MONEY_UNIT[matched.group(2)]
            if amount > 0:
                per_month = amount
        if per_month is None:
            matched_months = _BENEFIT_MONTHS_RE.search(name)
            if matched_months and int(matched_months.group(1)) > 0:
                per_month = worth / int(matched_months.group(1))
        if per_month is None:
            per_month = worth

    duration, _known = _benefit_duration(name, basis, months, one_off)
    if duration is None:
        return per_month  # 기간 미상 — 구간 내내로 본다(추정)
    return per_month * min(duration, BENEFIT_AMORTIZE_MONTHS) / BENEFIT_AMORTIZE_MONTHS


def benefit_summary(details: object) -> dict:
    """혜택 금액을 '참고값'과 '실제로 빼도 되는 금액'으로 갈라 월 환산한다.

    - value_won 이 비어 있는 혜택은 0 원이 아니라 '가치 미상'이라 합계에서 빠진다.
    - 택1(select_group)은 그룹당 가장 비싼 하나만 센다. 전부 더하면 실제보다 부풀려진다.
    - 총액인지 월액인지, 몇 달 주는지는 크롤러 컬럼과 이름 문자열로 판별한다(_monthly_worth).

    deductible_won 은 납부 총액에서 빼도 되는 금액만 담는다. 조건은 두 가지다.
      (1) 현금으로 돌려받는 혜택일 것 — 구독형(넷플릭스 등)은 사용자가 그 서비스를
          실제로 쓰고 직접 결제 중일 때만 절약이 된다. 이용 여부는 수집 데이터에 없다.
      (2) 카드 실적·별도 가입 같은 추가 조건이 없을 것.
    나머지는 monthly_won 에만 남아 화면에 '조건 확인이 필요한 참고값'으로 표시된다.
    """
    empty = {"monthly_won": 0, "deductible_won": 0, "estimated": False, "conditional": 0}
    if not isinstance(details, list):
        return empty

    totals = {"monthly_won": 0.0, "deductible_won": 0.0}
    groups: dict[str, dict[str, float]] = {}
    estimated = False
    conditional = 0
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
        categories = detail.get("categories") or []
        basis = str(detail.get("value_basis") or "")
        one_off = bool(_ONE_OFF_CATEGORIES.intersection(categories))
        worth = _monthly_worth(
            name, worth, one_off, basis=basis, months=detail.get("months")
        )
        _, known_duration = _benefit_duration(name, basis, detail.get("months"), one_off)
        if not known_duration:
            estimated = True

        condition = str(detail.get("condition") or "")
        has_condition = bool(_CONDITIONAL_RE.search(f"{condition} {name}"))
        if has_condition:
            conditional += 1
        is_cash = bool(_CASH_CATEGORIES.intersection(categories))
        deductible = worth if (is_cash and not has_condition and known_duration) else 0.0

        group = detail.get("select_group") or ""
        if detail.get("selectable") and group:
            # 택1은 그룹당 하나. 참고값이 가장 큰 것을 대표로 삼고 그 항목의 차감액을 쓴다.
            best = groups.get(group)
            if best is None or worth > best["monthly_won"]:
                groups[group] = {"monthly_won": worth, "deductible_won": deductible}
        else:
            totals["monthly_won"] += worth
            totals["deductible_won"] += deductible

    for picked in groups.values():
        totals["monthly_won"] += picked["monthly_won"]
        totals["deductible_won"] += picked["deductible_won"]
    return {
        "monthly_won": int(round(totals["monthly_won"])),
        "deductible_won": int(round(totals["deductible_won"])),
        "estimated": estimated,
        "conditional": conditional,
    }


def monthly_benefit_value(details: object) -> int:
    """요금제 하나의 혜택 월 환산 참고값. 납부액에서 빼도 되는 금액이 아니다."""
    return benefit_summary(details)["monthly_won"]


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


def benefit_requests_match(
    plan: object, wanted_names: list[str], wanted_categories: list[str], mode: str = "all"
) -> bool:
    """택1 그룹과 한 행의 대체 선택지를 고려해 혜택 요구를 검증한다."""
    requirements = [
        ("name", normalize_benefit(name)) for name in wanted_names if normalize_benefit(name)
    ]
    categories = [normalize_benefit_category(value) for value in wanted_categories]
    if any(value is None for value in categories):
        return False
    requirements.extend(("category", value) for value in categories)
    requirements = list(dict.fromkeys(requirements))
    if not requirements:
        return True

    details = [item for item in (plan.get("benefit_details") or []) if isinstance(item, dict)]
    broad_smart_with_line = mode != "any" and "스마트기기 회선/데이터쉐어링" in categories

    def category_matches(values: object, value: str) -> bool:
        options = set(values or [])
        if value == "스마트기기" and not broad_smart_with_line:
            return bool(options & {"스마트기기", "스마트기기 회선/데이터쉐어링"})
        return value in options

    if not details:
        text = f"{plan.get('ott_options') or ''} | " + " | ".join(plan.get("included_benefits") or [])
        checks = [
            has_benefit(text, value) if kind == "name"
            else category_matches(plan.get("benefit_categories"), value)
            for kind, value in requirements
        ]
        return any(checks) if mode == "any" else all(checks)

    def matches(detail: dict, requirement: tuple[str, str]) -> bool:
        kind, value = requirement
        return (
            has_benefit(detail.get("name", ""), value)
            if kind == "name"
            else category_matches(detail.get("categories"), value)
        )

    choices = [
        [index for index, detail in enumerate(details) if matches(detail, request)]
        for request in requirements
    ]
    if mode == "any":
        return any(choices)
    if any(not options for options in choices):
        return False

    used_rows: set[int] = set()
    chosen_groups: dict[str, int] = {}
    # 제약이 큰 조건부터 배치하면 '택1' 충돌을 빠르게 판별할 수 있다.
    ordered = sorted(choices, key=len)

    def assign(position: int) -> bool:
        if position == len(ordered):
            return True
        for index in ordered[position]:
            detail = details[index]
            group = str(detail.get("select_group") or "") if detail.get("selectable") else ""
            if group and group in chosen_groups and chosen_groups[group] != index:
                continue
            row_categories = detail.get("categories") or []
            name = str(detail.get("name") or "")
            # 한 행에 나열된 대체 선택지는 검색할 수는 있지만 동시에 받을 수는 없다.
            exclusive_row = bool(re.search(r"또는|택\s*1|중\s*1|\bor\b", name, re.IGNORECASE)) or (
                len(row_categories) > 1 and (
                    detail.get("primary_category") == "복합/선택혜택"
                    or not re.search(r"\+|\s및\s|함께", name)
                )
            )
            if exclusive_row and index in used_rows:
                continue
            added_row = index not in used_rows
            if added_row:
                used_rows.add(index)
            if group:
                chosen_groups[group] = index
            if assign(position + 1):
                return True
            if added_row:
                used_rows.remove(index)
            if group and group not in (
                str(details[other].get("select_group") or "")
                for other in used_rows
            ):
                chosen_groups.pop(group, None)
        return False

    return assign(0)


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


# 모요에 올라온 통신 3사 직판(너겟·요고·다이렉트 등). carrier_type 은 MVNO 로 수집되지만 알뜰폰이 아니다.
BIG3_DIRECT_BRANDS = ("SKT", "KT", "LG U+", "LGU+", "air by SK telecom")


def wants_big3(profile: dict) -> bool:
    """사용자가 통신 3사 상품을 직접 찾았는가. 아니면 추천 대상은 알뜰폰이다."""
    return bool(profile.get("include_mno") or profile.get("carrier_type") == "MNO" or profile.get("mvno_brand"))


def filter_candidates(profile: dict) -> list[dict]:
    """명시 조건을 그대로 적용하고 조건을 만족한 후보 전체를 반환한다."""
    load()
    df = _plans

    # 추천 대상은 알뜰폰이다. 가중치를 알뜰폰 시장의 선택 데이터로만 배웠고(통신 3사는 가입자 수가 없다),
    # 통신 3사 안에서의 요금제 변경은 결합할인·약정이 좌우하는데 그 정보가 우리 데이터에 없다.
    # 통신 3사 요금제는 탐색·비교함과 '현재 요금제' 비교에는 그대로 쓰인다(find_plans_by_name).
    if not wants_big3(profile):
        df = df[df["carrier_type"].eq("MVNO") & ~df["mvno_brand"].isin(BIG3_DIRECT_BRANDS)]

    # 청구액이 확인되지 않은 페이백 표시가는 예산·순위 판단의 기준이 될 수 없다.
    # 정정표로 청구액이 확인된 상품은 여기서 다시 후보가 된다(_apply_verified_billing).
    # 전체 탐색용 all_plans 에는 미확인 상품도 그대로 남는다.
    df = df[df["billing_price_known"]]

    # 예산 판단 기준은 discounted_fee(할인 후 실제 납부액).
    # 할인이 없는 요금제는 discounted_fee 가 monthly_fee 와 같고, 0원은 실제 0원 프로모션이다.
    if profile.get("budget_min_won") is not None:
        df = df[df["discounted_fee"] >= profile["budget_min_won"]]
    if profile.get("budget_max_won") is not None:
        df = df[df["discounted_fee"] <= profile["budget_max_won"]]

    # unlimited 계열이 False 인 것은 "무제한이 필수는 아니다" 라는 뜻이지
    # "무제한이면 안 된다" 가 아니다. True 일 때만 필터한다. (sms/voice 도 동일)
    #
    # 일반 '무제한'의 범위는 is_effectively_unlimited 한 곳에서만 정한다 - 기본량 무제한이거나
    # 제공량과 소진 후 속도를 **둘 다** 넘긴 QoS형이다. 문턱(UNLIMITED_MIN_GB /
    # UNLIMITED_QOS_MBPS)은 EDA 로 찾은 상품 군집 경계이자 서비스 정책이지 입증된 정답이 아니다.
    if profile.get("data_unlimited") is True:
        df = df[df["effective_unlimited"]] if not profile.get("require_full_unlimited") else df[df["data_unlimited"]]

    # 사용자가 직접 말한 "NGB 이상"만 Hard Constraint로 적용한다.
    # "NGB 정도"와 앱 사용량 추정값은 목표치이므로 후보를 제거하지 않고
    # mcda.py에서 부족/과잉 공급 적합도로 평가한다.
    if profile.get("min_data_gb") is not None:
        required_data_gb = float(profile["min_data_gb"])
        # 무제한은 data_gb가 비어 있어 수치 비교가 성립하지 않는다 → 예상량을 충족으로 본다.
        df = df[df["data_unlimited"] | (df["data_gb"] >= required_data_gb)]

    if profile.get("min_daily_data_gb") is not None:
        # 월 총량이 같아도 매일 다시 지급하는 구조가 아니면 이 조건을 만족하지 않는다.
        df = df[df["daily_data_gb"] >= float(profile["min_daily_data_gb"])]
    if profile.get("min_monthly_base_data_gb") is not None:
        # 크롤러의 data_gb는 월 기본량 + 일 제공량*30으로 환산되어 있다.
        # 일 제공량이 없는 상품은 이 조합 조건을 만족하지 않는다.
        daily = df["daily_data_gb"]
        monthly_base = df["data_gb"] - daily * 30
        df = df[daily.notna() & (monthly_base >= float(profile["min_monthly_base_data_gb"]) - 1e-6)]

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

    # 혜택 조건은 필수일 때만 후보를 지운다. '넷플릭스면 좋겠어'(선호)까지 필터로 걸면
    # 포함하지 않은 상품이 통째로 사라져 알뜰폰＋별도 구독 같은 대안을 비교할 수 없다.
    # 선호는 mcda._benefit_fit 이 점수로만 반영한다. hard_constraints 가 아예 없는
    # 호출(직접 dict 를 넘기는 테스트·스크립트)은 예전처럼 필수로 본다.
    hard = profile.get("hard_constraints")
    benefit_is_hard = hard is None or bool(
        {"wanted_benefits", "wanted_benefit_categories"}.intersection(hard)
    )

    benefit_masks = [
        df["benefit_search_text"].map(lambda text, value=benefit: has_benefit(text, value))
        for benefit in (profile.get("wanted_benefits") or [] if benefit_is_hard else [])
    ]
    for category in (profile.get("wanted_benefit_categories") or [] if benefit_is_hard else []):
        normalized = normalize_benefit_category(category)
        if normalized is None:
            return []
        benefit_masks.append(
            df["benefit_categories"].map(
                lambda values, value=normalized: has_benefit_category(values, value)
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
        # 요금제 전체 태그는 택1 선택지와 한 행에 적힌 'A 또는 B'를 합쳐 버린다.
        # 여러 혜택을 모두 요구할 때는 실제로 동시 선택 가능한 조합인지 다시 확인한다.
        if profile.get("benefit_match_mode") != "any" and len(benefit_masks) > 1:
            df = df[df.apply(
                lambda row: benefit_requests_match(
                    row,
                    profile.get("wanted_benefits") or [],
                    profile.get("wanted_benefit_categories") or [],
                    "all",
                ), axis=1
            )]

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
    "min_monthly_base_data_gb": "월 기본 데이터",
    "min_daily_data_gb": "매일 제공 데이터",
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
    if not wants_big3(profile):
        widened = filter_candidates({**profile, "include_mno": True})
        if widened:
            # 화면의 '이 조건 풀기' 버튼이 "추천 범위(알뜰폰) 조건은 빼고…" 문장을 보낸다(프로파일링 규칙과 짝).
            blockers.append({"field": "include_mno", "label": "추천 범위(알뜰폰)", "value": False, "candidates": len(widened)})
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
    full_names = names.map(normalize_plan_name)

    # 괄호 안 제휴명까지 사용자가 말했으면 전체 이름을 먼저 본다. stem부터 비교하면
    # 'TOP 11GB 기본 (CU할인)'도 'TOP 11GB 기본'으로 잘려 네이버페이·밀리의서재 등
    # 같은 본체를 가진 모든 변형이 잡히고, 데이터 순서상 첫 상품으로 잘못 교체된다.
    full_mentioned = full_names.map(lambda key: len(key) >= 4 and key in text_key)
    if full_mentioned.any():
        longest = max(full_names[full_mentioned].map(len))
        matched = _plans[full_mentioned & (full_names.map(len) == longest)]
        return [_row_summary(row) for _, row in matched.iterrows()]

    # '데이터 100GB'라는 스펙을 '데이터100G(밀리의서재)+'라는 상품으로 오인하지 않는다.
    generic_spec = stems.str.fullmatch(r"(?:데이터)?\d+(?:g|gb|기가)(?:무제한)?", case=False)
    mentioned = stems.map(lambda key: len(key) >= 4 and key in text_key) & (
        ~generic_spec | full_names.map(lambda key: bool(key) and key in text_key)
    )
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
        # 일 제공형은 data_gb가 월 환산 총량이다. 상품명 속 '11GB'를 현재 데이터로
        # 잘못 추출했을 때 원래 월 기본량인지 판별할 수 있도록 원본도 보존한다.
        "base_data_gb": float(r["base_data_gb"]) if pd.notna(r.get("base_data_gb")) else None,
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
        "billing_price_known": bool(r["billing_price_known"]),
        # 페이백을 뺀 체감 표시가와 지급 일정. 청구액(discounted_fee)과 섞지 않는다.
        "payback_included_fee": _opt_int(r.get("payback_included_fee")),
        "payback_schedule": str(r.get("payback_schedule") or ""),
        "discount_period_months": int(r["discount_period_months"])
        if pd.notna(r["discount_period_months"])
        else None,
        "ott_options": r["ott_options"] if pd.notna(r["ott_options"]) else "",
        "included_benefits": list(r["included_benefits"]),
        "benefit_categories": list(r["benefit_categories"]),
        "benefit_details": list(r["benefit_details"]),
        "benefit_value_won": int(r["benefit_value_won"]),
        # 납부 총액에서 실제로 빼도 되는 부분만. 구독형·조건부 혜택은 여기 들어오지 않는다.
        "benefit_deductible_won": int(r["benefit_deductible_won"]),
        "benefit_value_estimated": bool(r["benefit_value_estimated"]),
        "benefit_conditional_count": int(r["benefit_conditional_count"]),
        "age_condition": r["age_condition"] if pd.notna(r["age_condition"]) else "",
        "signup_notice": r["signup_notice"] if pd.notna(r.get("signup_notice")) else "",
        "plan_category": r["plan_category"] if pd.notna(r.get("plan_category")) else "",
        "source_url": r["source_url"] if pd.notna(r["source_url"]) else "",
        "is_online_only": bool(r["is_online_only"]),
        "ott_option_count": int(r["ott_option_count"]) if pd.notna(r["ott_option_count"]) else 0,
    }


# LLM 에게 보여줄 필드. 필터용 파생 숫자(data_gb/qos_mbps/voice_minutes 등)는
# 사람이 읽는 data/voice 와 같은 사실의 중복 표현이라 판정을 헷갈리게 해서 뺀다.
#
# 다만 사람이 읽는 대응 필드가 아예 없는 사실(테더링·문자·사업자 유형·망 세대 등)까지
# 빼면 안 된다. Report Agent 는 후보 원본 전체를 받아 쓰는데 Evaluation Agent 는 이
# 목록만 받으므로, 여기 없는 사실을 리포트가 인용하면 평가가 "데이터에 없는 기능을
# 단정했다"고 오판한다. 실제로 테더링 40GB 를 적은 멀쩡한 리포트가 재시도 2회를
# 태우고도 미통과로 끝났다. 두 에이전트가 보는 사실의 범위를 같게 맞춘다.
SLIM_FIELDS = (
    "plan_id",
    "plan_name",
    "carrier",
    "carrier_type",
    "network_gen",
    "data",
    "data_tier_label",
    "daily_data_gb",
    "tethering_gb",
    "voice",
    "sms_unlimited",
    # 문자 건수는 voice 처럼 사람이 읽는 대응 필드가 없다. 빼 두면 리포트가 적은
    # '문자 300건'을 평가가 "데이터에 없는 기능"으로 잡는다(실측: 재시도 1회 소모).
    "sms_count",
    "is_online_only",
    "plan_category",
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
    # 혜택 매칭 자체를 보는 검사라 추천 범위(알뜰폰)와 무관하게 전체 카탈로그에서 확인한다.
    whole = lambda profile: filter_candidates({**profile, "include_mno": True})  # noqa: E731
    plain = len(whole({"wanted_benefits": ["유튜브 프리미엄"]}))
    assert plain > 0
    assert len(whole({"wanted_benefits": ["유튜브 프리미엄 포함"]})) == plain

    # 포괄적 유형은 카테고리로, 고유 서비스명은 기존 문자열 검색으로 구분한다.
    music = whole({"wanted_benefit_categories": ["음악"]})
    assert music and all("음악/오디오" in row["benefit_categories"] for row in music)
    assert len(music) > len(whole({"wanted_benefits": ["음악"]}))
    genie = whole({"wanted_benefits": ["지니뮤직"]})
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
        for row in whole(
            {"wanted_benefit_categories": ["도서/콘텐츠"]}
        )
    }

    disney = whole({"wanted_benefits": ["디즈니플러스"]})
    books = whole({"wanted_benefit_categories": ["도서/콘텐츠"]})
    either = whole(
        {
            "wanted_benefits": ["디즈니플러스"],
            "wanted_benefit_categories": ["도서/콘텐츠"],
            "benefit_match_mode": "any",
        }
    )
    both = whole(
        {
            "wanted_benefits": ["디즈니플러스"],
            "wanted_benefit_categories": ["도서/콘텐츠"],
            "benefit_match_mode": "all",
        }
    )
    ids = lambda rows: {row["plan_id"] for row in rows}
    assert ids(either) == ids(disney) | ids(books)
    assert ids(both) == ids(disney) & ids(books)
    # 추천 대상은 알뜰폰이다. 통신 3사(모요의 3사 직판 포함)는 사용자가 직접 찾을 때만 후보가 된다.
    default = filter_candidates({"budget_max_won": 80000})
    assert default and all(r["carrier_type"] == "MVNO" and r["mvno_brand"] not in BIG3_DIRECT_BRANDS for r in default)
    assert any(r["carrier_type"] == "MNO" for r in filter_candidates({"budget_max_won": 80000, "include_mno": True}))
    assert all(r["carrier_type"] == "MNO" for r in filter_candidates({"carrier_type": "MNO"}))
    # 알뜰폰에 없는 상품을 찾으면 0건 대신 "통신 3사까지 넓히면 N건"을 알려 준다.
    assert not filter_candidates({"data_unlimited": True, "require_full_unlimited": True})
    widen = [b for b in diagnose_empty({"data_unlimited": True, "require_full_unlimited": True}) if b["field"] == "include_mno"]
    assert widen and widen[0]["candidates"] > 0

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
    # 속도만 빠른 소량 상품은 '무제한'이 아니다. 제공량도 함께 넘겨야 통과한다.
    assert is_effectively_unlimited(False, 10.0, 180.0)
    assert not is_effectively_unlimited(False, 5.0, 150.0)  # 대용량+5Mbps: 통신 3사도 무제한이라 부르지 않는다
    assert not is_effectively_unlimited(False, 10.0, 4.5)
    assert not is_effectively_unlimited(False, 1.0, 150.0)
    assert not is_effectively_unlimited(False, 10.0, None)
    assert is_effectively_unlimited(True, None, None)

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
    # 나이 전용 상품은 대부분 통신 3사에 있다. 자격 판정만 보는 검사라 범위를 넓혀서 확인한다.
    youth = filter_candidates({"budget_max_won": 30000, "user_age": 28, "include_mno": True})
    assert any(row["age_condition"] == "만 34세 이하" for row in youth)
    assert not any(row["age_condition"] == "만 12세 이하" for row in youth)

    # 혜택 가치: 택1은 그룹당 하나, 일시금은 월 환산, 값 없는 혜택은 0 원 취급하지 않는다
    # 0건일 때 어느 조건이 막았는지 짚어 준다 (넷플릭스 혜택 요금제는 최저 59,000원이다)
    # 넷플릭스 혜택은 통신 3사 상품에만 있다. 병목 진단 자체를 보는 검사라 범위를 넓혀 둔다.
    impossible = {"budget_max_won": 30000, "wanted_benefits": ["넷플릭스"], "user_age": 28, "include_mno": True}
    assert filter_candidates(impossible) == []
    blockers = diagnose_empty(impossible)
    fields = {item["field"] for item in blockers}
    assert fields == {"budget_max_won", "wanted_benefits"}, fields
    budget_blocker = next(item for item in blockers if item["field"] == "budget_max_won")
    assert budget_blocker["minimum_fee"] > 30000
    assert diagnose_empty({"budget_max_won": 30000}) == []  # 후보가 있으면 병목도 없다

    assert BENEFIT_AMORTIZE_MONTHS == COMPARE_MONTHS == 12
    assert monthly_benefit_value([{"name": "A", "value_won": 12000, "categories": ["멤버십"]}]) == 12000
    assert monthly_benefit_value([{"name": "A", "value_won": None, "categories": []}]) == 0
    # 일시금은 비교 구간(12개월)으로 편다. 60,000원 -> 월 5,000원.
    assert monthly_benefit_value(
        [{"name": "페이백", "value_won": 60000, "categories": ["페이백"]}]
    ) == 5000

    # 월 지급액·제공 기간·일시금을 각각 구분한다. 비교 구간은 12개월.
    # 6개월만 주는 페이백을 12개월 내내 받는 것으로 계산하면 지급액이 두 배가 된다.
    assert _monthly_worth("네이버페이 매달 3.4만원 페이백 (6개월)", 204_000, True) == 17_000
    assert _monthly_worth("네이버페이 매달 8천원 페이백 (12개월)", 96_000, True) == 8_000
    # 평생 제공은 구간 내내 받는다. '기간 미상'과 같은 값이 나오더라도 의미가 다르다.
    assert _monthly_worth("네이버페이 매달 5천원 페이백 (평생)", 5_000, True) == 5_000
    assert _benefit_duration("네이버페이 매달 5천원 페이백 (평생)", "", None) == (12, True)
    assert _benefit_duration("넷플릭스", "", None) == (None, False)
    assert _benefit_duration("페이백 (6개월)", "", None) == (6, True)
    assert _monthly_worth("넷플릭스", 17_000, False) == 17_000
    assert round(_monthly_worth("마트 상품권 2만원", 20_000, True)) == 1_667
    # 크롤러가 단위를 적어 주면 이름 파싱 없이 그대로 쓴다(새 스키마).
    assert _monthly_worth("아무 이름", 8_000, True, basis="monthly", months=12) == 8_000
    assert _monthly_worth("아무 이름", 8_000, True, basis="monthly", months=6) == 4_000
    assert round(_monthly_worth("아무 이름", 20_000, True, basis="one_off")) == 1_667
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

    # 차감 가능한 금액과 참고값을 가른다.
    cash = benefit_summary(
        [{"name": "페이백 (12개월)", "value_won": 120_000, "categories": ["페이백"]}]
    )
    assert cash["deductible_won"] == 10_000 and cash["estimated"] is False
    gift = benefit_summary(
        [{"name": "마트 상품권 2만원", "value_won": 20_000, "categories": ["상품권/사은품"]}]
    )
    assert gift["monthly_won"] > 0 and gift["deductible_won"] == 0

    # 구독형은 이용 여부를 확인할 수 없어 차감하지 않는다.
    ott = benefit_summary([{"name": "넷플릭스", "value_won": 17_000, "categories": ["영상/OTT"]}])
    assert ott["monthly_won"] == 17_000 and ott["deductible_won"] == 0
    assert ott["estimated"] is True  # 제공 기간 미확인

    # 카드 실적·가입 조건이 붙은 현금 혜택도 자동 차감하지 않는다.
    carded = benefit_summary(
        [{
            "name": "페이백 (12개월)", "value_won": 120_000, "categories": ["페이백"],
            "condition": "제휴카드 전월 실적 30만원",
        }]
    )
    assert carded["deductible_won"] == 0 and carded["conditional"] == 1

    assert any(row["benefit_value_won"] > 0 for row in anyone)
    assert any(row["benefit_deductible_won"] > 0 for row in anyone)
    # 차감액은 언제나 참고값 이하다
    assert all(
        row["benefit_deductible_won"] <= row["benefit_value_won"] for row in anyone
    )
    print(f"self-check ok: {len(c)} candidates")
