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
        _plans = plans


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
    if profile.get("data_unlimited") is True:
        df = df[df["data_unlimited"]]

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
        if pd.notna(r.get("data_throttle_speed")):
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
        "qos_mbps": float(r["qos_mbps"]) if pd.notna(r["qos_mbps"]) else None,
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
        "age_condition": r["age_condition"] if pd.notna(r["age_condition"]) else "",
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
    "voice",
    "monthly_fee",
    "discounted_fee",
    "discount_type",
    "discount_period_months",
    "ott_options",
    "included_benefits",
    "benefit_categories",
    "benefit_details",
    "age_condition",
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
            _plans["included_benefits"].map(
                lambda values: "티빙/지니/밀리" in values
            )
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
    print(f"self-check ok: {len(c)} candidates")
