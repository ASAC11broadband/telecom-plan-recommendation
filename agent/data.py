# -*- coding: utf-8 -*-
"""CSV 로드와 UserProfile 기반 Hard Filtering.

자연어 해석, 조건 완화, 후보 랭킹·축소는 담당하지 않는다.
조건을 만족한 후보 전체를 반환한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"  # CSV 는 프로젝트 루트의 data/ 에 둔다

PLANS_CSV = DATA_DIR / "통신요금제_통합데이터_최종.csv"
BENEFITS_CSV = DATA_DIR / "통신요금제_혜택상세_최종.csv"

_plans = None


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
    global _plans
    if _plans is not None:
        return
    _plans = pd.read_csv(PLANS_CSV, dtype={"plan_id": str})
    # 예산 판단 기준은 discounted_fee(할인 후 실제 납부액).
    # 할인이 없는 요금제는 discounted_fee 가 monthly_fee 와 같고, 0원은 실제 0원 프로모션이다.
    _plans["effective_fee"] = _plans["discounted_fee"]
    _plans["qos_mbps"] = _plans["data_throttle_speed"].map(_speed_to_mbps)
    benefits = pd.read_csv(BENEFITS_CSV, dtype={"plan_id": str})
    benefit_lists = (
        benefits.groupby("plan_id")["benefit_name"]
        .apply(lambda values: [str(value) for value in values.dropna().unique()])
    )
    _plans["included_benefits"] = _plans["plan_id"].map(benefit_lists).apply(
        lambda value: value if isinstance(value, list) else []
    )
    _plans["benefit_search_text"] = (
        _plans["ott_options"].fillna("").astype(str)
        + " | "
        + _plans["included_benefits"].apply(" | ".join)
    )


def filter_candidates(profile: dict) -> list[dict]:
    """명시 조건을 그대로 적용하고 조건을 만족한 후보 전체를 반환한다."""
    load()
    df = _plans

    if profile.get("budget_min_won") is not None:
        df = df[df["effective_fee"] >= profile["budget_min_won"]]
    if profile.get("budget_max_won") is not None:
        df = df[df["effective_fee"] <= profile["budget_max_won"]]

    data_unlimited = profile.get("data_unlimited")
    if data_unlimited is not None:
        df = df[df["data_unlimited"] == data_unlimited]

    if profile.get("min_data_gb") is not None:
        # 무제한은 data_gb 가 비어 있어 수치 비교가 성립하지 않는다 → 최소량 조건은 충족으로 본다.
        df = df[df["data_unlimited"] | (df["data_gb"] >= profile["min_data_gb"])]

    if profile.get("min_qos_mbps") is not None:
        df = df[df["qos_mbps"] >= profile["min_qos_mbps"]]
    if profile.get("min_tethering_gb") is not None:
        df = df[df["tethering_gb"] >= profile["min_tethering_gb"]]

    voice_unlimited = profile.get("voice_unlimited")
    if voice_unlimited is True:
        df = df[df["voice_unlimited"]]
    else:
        if profile.get("min_voice_minutes") is not None:
            df = df[
                df["voice_unlimited"]
                | (df["voice_minutes"] >= profile["min_voice_minutes"])
            ]
        if voice_unlimited is False:
            df = df[~df["voice_unlimited"]]

    sms_unlimited = profile.get("sms_unlimited")
    if sms_unlimited is True:
        df = df[df["sms_unlimited"]]
    elif sms_unlimited is False:
        df = df[~df["sms_unlimited"]]

    for field in ("carrier_type", "host_mno", "network_gen", "age_condition"):
        if profile.get(field) is not None:
            df = df[df[field] == profile[field]]

    if profile.get("mvno_brand") is not None:
        brand = str(profile["mvno_brand"]).strip().casefold()
        normalized = df["mvno_brand"].fillna("").astype(str).str.strip().str.casefold()
        df = df[normalized == brand]

    for benefit in profile.get("wanted_benefits") or []:
        df = df[
            df["benefit_search_text"].str.contains(
                str(benefit), case=False, na=False, regex=False
            )
        ]

    if profile.get("min_discount_period_months") is not None:
        df = df[
            df["discount_period_months"] >= profile["min_discount_period_months"]
        ]

    return [_row_summary(row) for _, row in df.iterrows()]


def find_candidate(candidates: list[dict], plan_id: str) -> dict | None:
    """고유 plan_id로 후보를 찾는다."""
    return next((candidate for candidate in candidates if candidate["plan_id"] == plan_id), None)


def find_plans_by_name(plan_name_query: str) -> list[dict]:
    """정확 일치를 우선하고 없으면 부분 일치로 기준 요금제를 찾는다."""
    load()
    query = plan_name_query.strip().casefold()
    if not query:
        return []

    names = _plans["plan_name"].fillna("").astype(str)
    exact = _plans[names.str.strip().str.casefold() == query]
    matched = exact if not exact.empty else _plans[
        names.str.contains(plan_name_query.strip(), case=False, na=False, regex=False)
    ]
    return [_row_summary(row) for _, row in matched.iterrows()]


def _row_summary(r) -> dict:
    if r["data_unlimited"]:
        data = "무제한"
        if pd.notna(r.get("data_throttle_speed")):
            data += f" (QoS {r['data_throttle_speed']})"
    elif pd.notna(r.get("data_throttle_speed")):
        data = f"{r['data_gb']}GB + 소진 후 {r['data_throttle_speed']}"
    else:
        data = f"{r['data_gb']}GB"
    return {
        "plan_id": r["plan_id"],
        "plan_name": r["plan_name"],
        "selected_option": r["selected_option"] if pd.notna(r["selected_option"]) else "",
        "carrier": r["mvno_brand"] if r["carrier_type"] == "MVNO" and pd.notna(r["mvno_brand"]) else r["host_mno"],
        "carrier_type": r["carrier_type"],
        "host_mno": r["host_mno"],
        "mvno_brand": r["mvno_brand"] if pd.notna(r["mvno_brand"]) else "",
        "network": r["network_gen"] if pd.notna(r["network_gen"]) else "",
        "network_gen": r["network_gen"] if pd.notna(r["network_gen"]) else "",
        "data": data,
        "data_gb": float(r["data_gb"]) if pd.notna(r["data_gb"]) else None,
        "data_unlimited": bool(r["data_unlimited"]),
        "qos_mbps": float(r["qos_mbps"]) if pd.notna(r["qos_mbps"]) else None,
        "tethering_gb": float(r["tethering_gb"]) if pd.notna(r["tethering_gb"]) else None,
        "voice": "무제한" if r["voice_unlimited"] else f"{r['voice_minutes']}분",
        "voice_minutes": int(r["voice_minutes"]) if pd.notna(r["voice_minutes"]) else None,
        "voice_unlimited": bool(r["voice_unlimited"]),
        "sms_unlimited": bool(r["sms_unlimited"]),
        "monthly_fee": int(r["monthly_fee"]),
        "discounted_fee": int(r["discounted_fee"]),
        "effective_fee": int(r["effective_fee"]),
        "discount_type": r["discount_type"] if pd.notna(r["discount_type"]) else "",
        "discount_period_months": int(r["discount_period_months"])
        if pd.notna(r["discount_period_months"])
        else None,
        "post_discount_fee": int(r["monthly_fee"]),
        "ott_options": r["ott_options"] if pd.notna(r["ott_options"]) else "",
        "included_benefits": list(r["included_benefits"]),
        "age_condition": r["age_condition"] if pd.notna(r["age_condition"]) else "",
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
    "age_condition",
)


def slim(rows: list[dict]) -> list[dict]:
    """프롬프트에 넣을 필드만 남긴다."""
    return [{field: row.get(field) for field in SLIM_FIELDS} for row in rows]


if __name__ == "__main__":
    c = filter_candidates(
        {"budget_max_won": 50000, "data_unlimited": True}
    )
    assert len(c) > 0
    assert all(x["discounted_fee"] <= 50000 for x in c)

    # 무제한은 data_gb 가 비어 있어도 최소량 조건에서 탈락하지 않는다
    assert len(filter_candidates({"data_unlimited": True, "min_data_gb": 30})) > 0

    assert "data_gb" not in slim(c)[0] and slim(c)[0]["plan_name"] == c[0]["plan_name"]
    print(f"self-check ok: {len(c)} candidates")
