# -*- coding: utf-8 -*-
"""CSV 요금제 행(agent.data._row_summary 결과)을 화면이 쓰는 PlanItem 으로 옮긴다.

여기서만 하는 일: 비교 구간 총비용 계산, 표시 문자열 조립, 해시태그 규칙.
필터링·랭킹은 agent 쪽 담당.
"""

from __future__ import annotations

from agent.mcda import COMPARE_MONTHS, _effective_monthly_fee, _PRICE_HORIZON_MONTHS

__all__ = ["COMPARE_MONTHS", "total_cost", "monthly_fee_schedule", "reference_delta",
           "to_plan_item", "to_plan_items"]


def monthly_fee_schedule(row: dict, months: int = COMPARE_MONTHS) -> list[int]:
    """1개월차부터 months개월차까지의 월별 청구액.

    할인 기간이 끝나는 달부터는 정가로 돌아간다. 총비용과 "할인 종료 시점의 요금
    변화" 그래프가 같은 목록을 쓰게 해서, 표의 합계와 그래프가 어긋나지 않게 한다.

    discount_period_months 가 없으면 할인 무기한으로 본다(약정 할인 등).
    그 경우 costIsEstimate 로 추정임을 함께 내보낸다.

    사용자가 말한 현재 요금처럼 정가를 모르는 행은 현재 청구액이 구간 내내 유지된다고
    본다 - 모르는 값을 0으로 두면 총비용이 실제보다 싸게 나온다.
    """
    discounted = int(row["discounted_fee"])
    regular = int(row.get("monthly_fee") if row.get("monthly_fee") is not None else discounted)
    period = row.get("discount_period_months")
    promo = months if period is None else max(0, min(int(period), months))
    return [discounted if month < promo else regular for month in range(months)]


def total_cost(row: dict, months: int = COMPARE_MONTHS) -> int:
    """비교 구간 총 납부액.

    구간은 agent.mcda.COMPARE_MONTHS 하나뿐이다. 추천의 가격 평가와 화면의 총비용이
    같은 기간을 써야 "순위는 A가 위인데 총비용은 B가 싸다"는 설명이 성립한다.
    """
    return sum(monthly_fee_schedule(row, months))


def _carrier_label(row: dict) -> str:
    parts = [row["carrier"]]
    if row["carrier_type"] == "MVNO" and row["host_mno"]:
        parts.append(f"{row['host_mno']}망")
    if row.get("is_online_only"):
        parts.append("온라인 전용")
    return " · ".join(str(p) for p in parts if p)


def _price_note(row: dict) -> str:
    if row.get('billing_price_known') is False:
        return '페이백 반영 표시가 · 실제 청구액 확인 필요'
    period = row.get("discount_period_months")
    if row["discounted_fee"] >= row["monthly_fee"]:
        return "프로모션 없음 · 정가 동일"
    promo = f"프로모션 {period}개월" if period is not None else "할인 기간 확인 필요"
    return f"정가 {row['monthly_fee']:,}원 · {promo}"


def _data_label(row: dict) -> str:
    """화면용 데이터 표기. row["data"] 는 QoS 를 문장에 섞어 두는데(프롬프트용),
    화면에는 qos 칸이 따로 있어 그대로 쓰면 소진 후 속도가 두 번 나온다."""
    if row.get("data_unlimited"):
        return "무제한"
    gb = row.get("data_gb")
    return f"{gb:g}GB" if gb is not None else str(row.get("data") or "확인 필요")


def _qos_label(row: dict) -> str:
    """소진 후 속도. 값이 없는 것은 '없음'이 아니라 '미수집'이다(822건).

    자료에 없는 것을 '속도 제어 없음'으로 단정하면 사용자는 종량 과금이 없다고 읽는다.
    """
    if row.get("qos_mbps") is None:
        return "확인 필요"
    mbps = row["qos_mbps"]
    return f"+{mbps:g}Mbps" if mbps >= 1 else f"+{mbps * 1000:g}Kbps"


def _tethering_label(row: dict) -> str:
    """테더링. 전체의 72%(1,981건)가 미수집이라 '미제공'으로 단정할 수 없다."""
    gb = row.get("tethering_gb")
    return f"{gb:g}GB" if gb is not None else "확인 필요"


def _hashtags(row: dict, is_cheapest: bool = False) -> list[str]:
    tags = []
    if row.get("is_online_only"):
        tags.append("#온라인전용")
    if row.get("age_condition"):
        tags.append("#청년요금제" if "이하" in str(row["age_condition"]) else "#가입조건")
    if row.get("data_unlimited"):
        tags.append("#기본량무제한")
    elif row.get("data_tier") == "qos_hd":
        # 소진 후에도 HD 시청이 되는 상품. 완전 무제한(소진 후 100Kbps)보다 빠르다.
        tags.append("#소진후HD")
    if row.get("ott_option_count"):
        tags.append("#OTT결합")
    if row.get("voice_unlimited"):
        tags.append("#통화무제한")
    if row.get("benefit_value_won"):
        tags.append("#혜택환산")
    if is_cheapest:
        tags.append("#목록내최저가")
    return tags


def _benefit_text(row: dict, matched_benefits: list[str] | None = None) -> str:
    items = [b for b in (row.get("ott_options", "").split(" | ") if row.get("ott_options") else []) if b]
    items += [b for b in row.get("included_benefits", []) if b not in items]
    # 비교표에서는 사용자가 직접 요청한 조건과 일치하는 혜택을 앞에 고정한다.
    # 원본 순서의 앞 3개만 자르면 네 번째 이후의 필수 혜택이 사라질 수 있다.
    prioritized = [b for b in (matched_benefits or []) if b]
    ordered = prioritized + [b for b in items if b not in prioritized]
    return " · ".join(ordered[:3]) if ordered else "부가 혜택 없음"


def _known_total(row: dict, months: int) -> int | None:
    """청구액이 확인된 행만 총비용을 만든다. 페이백 반영 표시가는 합산하지 않는다."""
    if row.get("billing_price_known") is False or row.get("discounted_fee") is None:
        return None
    return total_cost(row, months)


def reference_delta(reference: dict | None, row: dict, months: int = COMPARE_MONTHS) -> dict | None:
    """현재 쓰는 요금제와 후보 하나의 차이. 상품명을 몰라도 계산한다.

    reference 는 카탈로그에서 찾은 행일 수도 있고, 사용자가 말한 월 납부액·데이터량만
    담긴 dict 일 수도 있다(agent.agents.recommend._reference_from_profile). 둘 다
    `discounted_fee` 하나만 있으면 비용 비교가 성립한다.

    지키는 규칙 세 가지.
    1. 모르는 값을 0으로 계산하지 않는다. 현재 납부액을 모르면 비용 차이는 None 이다.
    2. 위약금·결합할인 손실은 수집 데이터에 없다. 그래서 여기 나오는 차이는 '확정
       절약액'이 아니라 '요금만 비교한 차이'다 - `unknowns` 로 무엇이 빠졌는지 밝힌다.
    3. 현재 청구액은 비교 구간 내내 유지된다고 가정한다(사용자의 현재 할인 종료 시점을
       모른다). 가정은 `assumption` 으로 함께 내려보내 화면이 그대로 쓰게 한다.
    """
    if not reference:
        return None
    current_fee = reference.get("discounted_fee")
    candidate_fee = row.get("discounted_fee")
    current_schedule = monthly_fee_schedule(reference, months) if current_fee is not None else None
    candidate_schedule = monthly_fee_schedule(row, months) if row.get("billing_price_known") is not False else None

    current_total = _known_total(reference, months)
    candidate_total = _known_total(row, months)
    both_known = current_total is not None and candidate_total is not None

    period = row.get("discount_period_months")
    is_promo = candidate_fee is not None and row.get("monthly_fee") is not None and candidate_fee < row["monthly_fee"]

    current_gb = None if reference.get("data_unlimited") else reference.get("data_gb")
    candidate_gb = None if row.get("data_unlimited") else row.get("data_gb")
    data_diff = (candidate_gb - current_gb) if (current_gb is not None and candidate_gb is not None) else None

    unknowns = ["해지 위약금", "결합·가족할인 손실", "현재 요금제의 할인 종료 시점"]
    if current_fee is None:
        unknowns.insert(0, "현재 월 납부액")
    if candidate_schedule is None:
        unknowns.insert(0, "후보의 실제 청구액")
    if is_promo and period is None:
        unknowns.append("후보의 할인 제공 기간")

    return {
        "months": months,
        "currentMonthlyFee": current_fee,
        "candidateMonthlyFee": candidate_fee,
        "monthlyDiff": (candidate_fee - current_fee) if (current_fee is not None and candidate_schedule) else None,
        "currentTotal": current_total,
        "candidateTotal": candidate_total,
        "totalDiff": (candidate_total - current_total) if both_known else None,
        # 할인이 끝나는 달과 그 다음 달 요금. 할인이 없으면 둘 다 None 이다.
        "discountEndsAfterMonths": int(period) if (is_promo and period is not None) else None,
        "feeAfterDiscount": int(row["monthly_fee"]) if is_promo else None,
        "currentData": _data_label(reference),
        "candidateData": _data_label(row),
        "dataDiffGb": data_diff,
        "currentQos": _qos_label(reference),
        "candidateQos": _qos_label(row),
        # 월별 요금 변화 그래프용. 값이 없는 쪽은 None 으로 두고 선을 그리지 않는다.
        "schedule": [
            {
                "month": index + 1,
                "current": current_schedule[index] if current_schedule else None,
                "candidate": candidate_schedule[index] if candidate_schedule else None,
            }
            for index in range(months)
        ],
        "unknowns": unknowns,
        "assumption": f"현재 월 납부액이 {months}개월 동안 그대로 유지된다고 가정한 비교입니다. "
                      "위약금과 결합할인 손실은 수집 데이터에 없어 반영하지 않았습니다.",
    }


def to_plan_item(
    row: dict,
    rank: int = 0,
    score: int = 0,
    reason: str = "",
    matched_benefits: list[str] | None = None,
    is_cheapest: bool = False,
    criteria_fit: dict | None = None,
    expected_rank: float | None = None,
    first_rank_acceptability: float | None = None,
    reference: dict | None = None,
) -> dict:
    total = total_cost(row)
    period = row.get("discount_period_months")
    is_promo = row["discounted_fee"] < row["monthly_fee"]
    benefit_value = int(row.get("benefit_value_won") or 0)
    # 납부 총액에서 빼는 것은 '조건 없는 현금성 혜택'뿐이다(agent.data.benefit_summary).
    # 넷플릭스 같은 구독형은 사용자가 그 서비스를 실제로 쓰고 직접 결제 중일 때만 절약이
    # 되는데 이용 여부는 수집 데이터에 없다. 확정 절약액으로 빼면 OTT 를 안 보는 사용자의
    # 실부담이 실제보다 싸게 보이고, 알뜰폰＋별도 구독 조합과의 비교도 무너진다.
    # benefit_value_won 은 조건을 확인해야 하는 참고값으로 표시만 한다.
    #
    # 페이백 반영 표시가에서는 다시 차감하지 않는다. 확인된 가격만 차감 참고값을 만든다.
    billing_known = row.get('billing_price_known', True)
    deductible = int(row.get("benefit_deductible_won") or 0) if billing_known else 0
    raw_effective = total - deductible * COMPARE_MONTHS
    effective_total = max(0, raw_effective)
    return {
        "id": row["plan_id"],
        "rank": rank,
        "best": rank == 1,
        "name": row["plan_name"],
        "carrier": _carrier_label(row),
        "carrierType": row["carrier_type"],
        "network": row["host_mno"],
        "networkGen": row.get("network_gen", ""),
        "price": f"{row['discounted_fee']:,}",
        "priceNum": row["discounted_fee"],
        "originalPrice": row["monthly_fee"],
        "priceNote": _price_note(row),
        "billingPriceKnown": billing_known,
        "score": score,
        "reason": reason,
        "data": _data_label(row),
        "dataNum": row.get("data_gb"),
        "dataUnlimited": row["data_unlimited"],
        # 소진 후에 무엇을 할 수 있는지가 실제 체감을 가른다. agent.data.data_tier 참고.
        "dataTier": row.get("data_tier", "capped"),
        "dataTierLabel": row.get("data_tier_label", ""),
        "effectiveUnlimited": bool(row.get("effective_unlimited")),
        "dailyDataGb": row.get("daily_data_gb"),
        "qos": _qos_label(row),
        "qosKnown": row.get("qos_mbps") is not None,
        "call": row["voice"],
        "sms": "무제한" if row["sms_unlimited"] else "기본",
        "tethering": _tethering_label(row),
        "tetheringGb": row.get("tethering_gb"),
        "hash": _hashtags(row, is_cheapest),
        "benefit": _benefit_text(row, matched_benefits),
        "benefitValue": benefit_value,
        "benefitDeductible": deductible,
        # 제공 기간이 확인되지 않은 혜택이 섞여 있으면 월 환산액은 추정이다.
        "benefitValueEstimated": bool(row.get("benefit_value_estimated")),
        # 카드 실적·별도 가입 같은 조건이 붙은 혜택 수. 자동 차감하지 않은 것들이다.
        "benefitConditionalCount": int(row.get("benefit_conditional_count") or 0),
        "criteriaFit": criteria_fit or {},
        "expectedRank": expected_rank,
        "firstRankAcceptability": first_rank_acceptability,
        "rankingMonths": _PRICE_HORIZON_MONTHS,
        "rankingAverageFee": round(_effective_monthly_fee(row)) if billing_known else None,
        "costIsEstimate": bool(is_promo and period is None),
        "dataWarnings": (["로밍 속도 혼입이 의심되어 국내 QoS 값에서 제외했습니다. 원문 확인이 필요합니다."]
                         if row.get("qos_source_suspect") else []),
        "signupNotice": row.get("signup_notice", ""),
        "total": f"{total:,}원" if billing_known else '청구액 확인 필요',
        "totalNum": total if billing_known else None,
        "effectiveTotalNum": effective_total if billing_known else None,
        "effectiveTotal": f"{effective_total:,}원" if billing_known else '계산 제외',
        # 혜택 금액이 요금을 넘어선 경우. 조건을 확인해야 한다는 신호로만 쓴다.
        "benefitExceedsFee": raw_effective < 0,
        "compareMonths": COMPARE_MONTHS,
        # 현재 쓰는 요금제가 있을 때만 채운다. 상품명 없이 납부액만 알려준 경우도 포함한다.
        "referenceDelta": reference_delta(reference, row),
        "promoMonths": int(period) if period else 0,
        "isPromo": is_promo,
        # 할인이 비교 구간 안에 끝나면 화면에 "N+1개월차부터 정가" 경고를 띄운다
        "priceRisesAfter": int(period) if is_promo and period and int(period) < COMPARE_MONTHS else None,
        # 비교 구간 밖에서 오르는 경우도 알려야 한다. 구간 안 총비용만 보면 구간이 끝난 뒤
        # 요금이 몇 배가 되는 상품을 "제일 싸다"고 읽게 된다.
        "priceRisesLater": bool(is_promo and period and int(period) >= COMPARE_MONTHS),
        "promoDiscountRate": (
            round(1 - row["discounted_fee"] / row["monthly_fee"], 3) if row["monthly_fee"] else 0.0
        ),
        "isOnlineOnly": bool(row.get("is_online_only")),
        "hasAddon": bool(row.get("ott_option_count")),
        "ageCondition": row.get("age_condition", ""),
        "sourceUrl": row.get("source_url", ""),
    }


def to_plan_items(rows: list[dict], ranked: list[dict] | None = None,
                  reference: dict | None = None) -> list[dict]:
    """ranked(plan_id·score·reason 순서)에 맞춰 상세를 붙인다. ranked 없으면 목록 그대로.

    reference 가 있으면 각 항목에 현재 요금제 대비 변화(referenceDelta)를 함께 담는다.
    """
    if not rows:
        return []
    cheapest = min(r["discounted_fee"] for r in rows)
    by_id = {r["plan_id"]: r for r in rows}
    if ranked is None:
        return [
            to_plan_item(r, is_cheapest=r["discounted_fee"] == cheapest, reference=reference)
            for r in rows
        ]
    items = []
    for rank, scored in enumerate(ranked, start=1):
        row = by_id.get(scored["plan_id"])
        if row is None:
            continue
        items.append(
            to_plan_item(
                row,
                rank=rank,
                score=scored.get("score", 0),
                reason=scored.get("reason", ""),
                matched_benefits=scored.get("matched_benefits", []),
                is_cheapest=row["discounted_fee"] == cheapest,
                criteria_fit=scored.get("criteria_fit", {}),
                expected_rank=scored.get("expected_rank"),
                first_rank_acceptability=scored.get("first_rank_acceptability"),
                reference=reference,
            )
        )
    return items



# ─────────────── 탐색 화면용 필터 ───────────────
# 키는 프론트가 그대로 쓰는 식별자. 라벨은 화면에서 붙인다.

def _in_data_bucket(row: dict, bucket: str) -> bool:
    if bucket == "unlimited":
        return row["data_unlimited"]
    gb = row.get("data_gb")
    if row["data_unlimited"] or gb is None:
        return False
    if bucket == "lt3":
        return gb < 3
    if bucket == "3to10":
        return 3 <= gb < 10
    if bucket == "10to20":
        return 10 <= gb < 20
    if bucket == "gte20":
        return gb >= 20
    return False


def _in_network(row: dict, key: str) -> bool:
    # MNO 직영은 망이 아니라 사업자 유형이다.
    return row["carrier_type"] == "MNO" if key == "MNO" else row["host_mno"] == key


def _in_voice(row: dict, key: str) -> bool:
    if key == "unlimited":
        return row["voice_unlimited"]
    has_quota = bool(row.get("voice_minutes"))
    return has_quota if key == "quota" else not (row["voice_unlimited"] or has_quota)


def _has_flag(row: dict, key: str) -> bool:
    if key == "online_only":
        return bool(row.get("is_online_only"))
    if key == "addon":
        return bool(row.get("ott_option_count"))
    if key == "promo":
        return row["discounted_fee"] < row["monthly_fee"]
    if key == "no_age_limit":
        return not row.get("age_condition")
    if key == "benefit_value":
        return bool(row.get("benefit_value_won"))
    return False


_PRICE_BANDS = {
    "lt10k": (0, 9_999),
    "10to20k": (10_000, 19_999),
    "20to30k": (20_000, 29_999),
    "30to50k": (30_000, 49_999),
    "gte50k": (50_000, 10**9),
}


def _in_price(row: dict, key: str) -> bool:
    band = _PRICE_BANDS.get(key)
    return bool(band) and band[0] <= row["discounted_fee"] <= band[1]


def _in_tier(row: dict, key: str) -> bool:
    """소진 후 무엇이 되는지로 거른다. '무제한' 라벨보다 이쪽이 체감에 가깝다."""
    return row.get("data_tier") == key


def _in_gen(row: dict, key: str) -> bool:
    return str(row.get("network_gen") or "") == key


# 그룹 안에서는 OR, 그룹 사이에서는 AND. 시안의 체크박스 동작 그대로.
FILTER_GROUPS = {
    "networks": (["SKT", "KT", "LGU+", "MNO"], _in_network),
    "data": (["lt3", "3to10", "10to20", "gte20", "unlimited"], _in_data_bucket),
    "tier": (["unlimited_full", "qos_hd", "qos_sd", "qos_lite", "qos_text", "capped"], _in_tier),
    "price": (list(_PRICE_BANDS), _in_price),
    "gen": (["5G", "LTE"], _in_gen),
    "voice": (["unlimited", "quota", "none"], _in_voice),
    "flags": (["online_only", "addon", "promo", "no_age_limit", "benefit_value"], _has_flag),
}

SORTS = {
    "fee_asc": (lambda r: r["discounted_fee"], False),
    "fee_desc": (lambda r: r["discounted_fee"], True),
    "data_desc": (lambda r: (r["data_unlimited"], r.get("data_gb") or 0), True),
    "total_asc": (lambda r: total_cost(r) if r.get('billing_price_known', True) else float('inf'), False),
    # 조건 없는 현금성 혜택만 뺀 실부담. 구독형·조건부 혜택은 빼지 않는다.
    "effective_asc": (
        lambda r: total_cost(r) - int(r.get("benefit_deductible_won") or 0) * COMPARE_MONTHS
        if r.get('billing_price_known', True) else float('inf'),
        False,
    ),
    "qos_desc": (lambda r: r.get("qos_mbps") or 0, True),
}


def apply_filters(rows: list[dict], selected: dict[str, list[str]], query: str | None) -> list[dict]:
    if query:
        needle = query.strip().casefold()
        rows = [
            r
            for r in rows
            if needle in r["plan_name"].casefold() or needle in str(r["carrier"]).casefold()
        ]
    for group, keys in selected.items():
        if not keys:
            continue
        _, predicate = FILTER_GROUPS[group]
        rows = [r for r in rows if any(predicate(r, key) for key in keys)]
    return rows


def facet_counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    """필터 항목별 건수. 전체 데이터 기준으로 한 번만 센다."""
    return {
        group: {key: sum(1 for r in rows if predicate(r, key)) for key in keys}
        for group, (keys, predicate) in FILTER_GROUPS.items()
    }


if __name__ == "__main__":
    # 할인 기간이 비교 구간보다 짧으면 남은 달은 정가 (구간 12개월)
    assert COMPARE_MONTHS == 12
    assert total_cost({"discounted_fee": 19800, "monthly_fee": 24800, "discount_period_months": 7}) == 19800 * 7 + 24800 * 5
    assert total_cost({"discounted_fee": 19800, "monthly_fee": 24800, "discount_period_months": 2}) == 19800 * 2 + 24800 * 10
    # 할인 기간 미기재 = 무기한 (costIsEstimate 로 추정임을 표시한다)
    assert total_cost({"discounted_fee": 17500, "monthly_fee": 17500, "discount_period_months": None}) == 17500 * 12

    from agent.data import get_plan, all_plans

    rows = all_plans()
    assert len(rows) > 2000
    item = to_plan_item(rows[0], rank=1, score=90)
    assert item["best"] and item["total"].endswith("원") and isinstance(item["hash"], list)

    # 데이터 표기에 소진 후 속도가 섞여 들어가면 화면에서 두 번 나온다
    throttled = next(r for r in rows if not r["data_unlimited"] and r.get("qos_mbps") and r.get("data_gb"))
    assert "소진" not in to_plan_item(throttled)["data"]
    assert to_plan_item(throttled)["data"].endswith("GB")

    # 자료에 없는 값은 '없음'이 아니라 '확인 필요'다
    assert _qos_label({"qos_mbps": None}) == "확인 필요"
    assert _qos_label({"qos_mbps": 5.0}) == "+5Mbps"
    assert _tethering_label({"tethering_gb": None}) == "확인 필요"
    assert _tethering_label({"tethering_gb": 10.0}) == "10GB"

    # 차감 가능한 현금성 혜택이 있으면 실부담이 요금 합계보다 작다
    deductible_row = next(r for r in rows if r.get("benefit_deductible_won")
                          and r.get('billing_price_known') and r['discounted_fee'] > 0)
    item_valued = to_plan_item(deductible_row)
    assert item_valued["effectiveTotalNum"] < item_valued["totalNum"]
    assert item_valued["benefitDeductible"] > 0

    # 구독형·조건부 혜택은 금액이 잡혀 있어도 납부액에서 빼지 않는다
    subscription_only = next(
        r for r in rows if r.get("benefit_value_won") and not r.get("benefit_deductible_won")
    )
    item_sub = to_plan_item(subscription_only)
    assert item_sub["effectiveTotalNum"] == item_sub["totalNum"], item_sub["name"]
    assert item_sub["benefitValue"] > 0

    # 확인된 가격의 차감 참고값은 음수가 되지 않는다. 미확인 비용은 None이다.
    assert all(to_plan_item(r)["effectiveTotalNum"] >= 0 for r in rows if r['billing_price_known'])
    generous = to_plan_item({**deductible_row, "benefit_deductible_won": 10**7})
    assert generous["effectiveTotalNum"] == 0 and generous["benefitExceedsFee"] is True

    # 비교 구간 밖에서 오르는 프로모션도 알린다
    long_promo = {
        "plan_id": "x", "plan_name": "x", "carrier": "x", "carrier_type": "MVNO", "host_mno": "KT",
        "mvno_brand": "", "data": "10GB", "data_gb": 10.0, "data_unlimited": False,
        "voice": "무제한", "voice_unlimited": True, "sms_unlimited": False,
        "monthly_fee": 17600, "discounted_fee": 10, "discount_period_months": 12,
    }
    assert to_plan_item(long_promo)["priceRisesAfter"] is None
    assert to_plan_item(long_promo)["priceRisesLater"] is True
    # 제공 기간이 확인되지 않은 할인은 추정으로 표시한다
    assert to_plan_item({**long_promo, "discount_period_months": None})["costIsEstimate"] is True
    assert to_plan_item({**long_promo, "discount_period_months": 3})["priceRisesAfter"] == 3
    assert _data_label({"data_unlimited": True, "data": "무제한 (QoS 1Mbps)"}) == "무제한"
    assert _data_label({"data_unlimited": False, "data_gb": 4.5, "data": "x"}) == "4.5GB"
    assert _data_label({"data_unlimited": False, "data_gb": 20.0, "data": "x"}) == "20GB"

    one = get_plan(rows[0]["plan_id"])
    assert one is not None and one["plan_name"] == rows[0]["plan_name"]
    assert get_plan("존재하지-않는-id") is None

    ranked = [{"plan_id": rows[1]["plan_id"], "score": 88, "reason": "테스트"}]
    items = to_plan_items(rows[:5], ranked)
    assert len(items) == 1 and items[0]["rank"] == 1 and items[0]["score"] == 88

    everything = list(rows)
    assert apply_filters(everything, {"data": ["unlimited"]}, None) != []
    only_unlimited = apply_filters(everything, {"data": ["unlimited"]}, None)
    assert all(r["data_unlimited"] for r in only_unlimited)
    # 그룹 안에서는 OR — 버킷 두 개를 고르면 후보가 늘어난다
    two = apply_filters(everything, {"data": ["10to20", "gte20"]}, None)
    one = apply_filters(everything, {"data": ["gte20"]}, None)
    assert len(two) > len(one)
    # 그룹 사이에서는 AND — 조건을 더하면 줄어든다
    both = apply_filters(everything, {"data": ["gte20"], "flags": ["online_only"]}, None)
    assert len(both) <= len(one)
    counts = facet_counts(everything)
    assert counts["networks"]["KT"] > 0 and sum(counts["data"].values()) > 0
    assert counts["voice"]["unlimited"] + counts["voice"]["quota"] + counts["voice"]["none"] == len(everything)

    print(f"self-check ok: {len(rows)} plans, sample total={item['total']}, KT={counts['networks']['KT']}")
