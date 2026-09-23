# -*- coding: utf-8 -*-
"""CSV 요금제 행(agent.data._row_summary 결과)을 화면이 쓰는 PlanItem 으로 옮긴다.

여기서만 하는 일: 6개월 총비용 계산, 표시 문자열 조립, 해시태그 규칙.
필터링·랭킹은 agent 쪽 담당.
"""

from __future__ import annotations

# 비교 구간 6개월. 알뜰폰 이용자는 대부분 프로모션이 끝날 때쯤 다른 요금제로 갈아타므로
# 프로모션 종료 후 정가까지 합산하는 12개월 비교는 실제 지출과 오히려 멀어진다. 의도된 값이다.
COMPARE_MONTHS = 6


def six_month_cost(row: dict, months: int = COMPARE_MONTHS) -> int:
    """할인 기간이 비교 구간보다 짧으면 남은 달은 정가로 계산한다.

    discount_period_months 가 없으면 할인 무기한으로 본다(약정 할인 등).
    """
    discounted = int(row["discounted_fee"])
    regular = int(row["monthly_fee"])
    period = row.get("discount_period_months")
    if period is None:
        return discounted * months
    promo = min(int(period), months)
    return discounted * promo + regular * max(0, months - promo)


def _carrier_label(row: dict) -> str:
    parts = [row["carrier"]]
    if row["carrier_type"] == "MVNO" and row["host_mno"]:
        parts.append(f"{row['host_mno']}망")
    if row.get("is_online_only"):
        parts.append("온라인 전용")
    return " · ".join(str(p) for p in parts if p)


def _price_note(row: dict) -> str:
    period = row.get("discount_period_months")
    if row["discounted_fee"] >= row["monthly_fee"]:
        return "프로모션 없음 · 정가 동일"
    promo = f"프로모션 {period}개월" if period else "약정 할인 유지"
    return f"정가 {row['monthly_fee']:,}원 · {promo}"


def _data_label(row: dict) -> str:
    """화면용 데이터 표기. row["data"] 는 QoS 를 문장에 섞어 두는데(프롬프트용),
    화면에는 qos 칸이 따로 있어 그대로 쓰면 소진 후 속도가 두 번 나온다."""
    if row["data_unlimited"]:
        return "무제한"
    gb = row.get("data_gb")
    return f"{gb:g}GB" if gb is not None else row["data"]


def _qos_label(row: dict) -> str:
    if row.get("qos_mbps") is None:
        return "-"
    mbps = row["qos_mbps"]
    return f"+{mbps:g}Mbps" if mbps >= 1 else f"+{mbps * 1000:g}Kbps"


def _hashtags(row: dict, is_cheapest: bool = False) -> list[str]:
    tags = []
    if row.get("is_online_only"):
        tags.append("#온라인전용")
    if row.get("age_condition"):
        tags.append("#청년요금제" if "이하" in str(row["age_condition"]) else "#가입조건")
    if row.get("data_unlimited"):
        tags.append("#완전무제한")
    if row.get("ott_option_count"):
        tags.append("#OTT결합")
    if row.get("voice_unlimited"):
        tags.append("#통화무제한")
    if is_cheapest:
        tags.append("#최저가")
    return tags


def _benefit_text(row: dict, matched_benefits: list[str] | None = None) -> str:
    items = [b for b in (row.get("ott_options", "").split(" | ") if row.get("ott_options") else []) if b]
    items += [b for b in row.get("included_benefits", []) if b not in items]
    # 비교표에서는 사용자가 직접 요청한 조건과 일치하는 혜택을 앞에 고정한다.
    # 원본 순서의 앞 3개만 자르면 네 번째 이후의 필수 혜택이 사라질 수 있다.
    prioritized = [b for b in (matched_benefits or []) if b]
    ordered = prioritized + [b for b in items if b not in prioritized]
    return " · ".join(ordered[:3]) if ordered else "부가 혜택 없음"


def to_plan_item(
    row: dict,
    rank: int = 0,
    score: int = 0,
    reason: str = "",
    matched_benefits: list[str] | None = None,
    is_cheapest: bool = False,
) -> dict:
    total = six_month_cost(row)
    period = row.get("discount_period_months")
    is_promo = row["discounted_fee"] < row["monthly_fee"]
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
        "score": score,
        "reason": reason,
        "data": _data_label(row),
        "dataNum": row.get("data_gb"),
        "dataUnlimited": row["data_unlimited"],
        "qos": _qos_label(row),
        "call": row["voice"],
        "sms": "무제한" if row["sms_unlimited"] else "기본",
        "tetheringGb": row.get("tethering_gb"),
        "hash": _hashtags(row, is_cheapest),
        "benefit": _benefit_text(row, matched_benefits),
        "total": f"{total:,}원",
        "totalNum": total,
        "compareMonths": COMPARE_MONTHS,
        "promoMonths": int(period) if period else 0,
        "isPromo": is_promo,
        # 할인이 비교 구간 안에 끝나면 화면에 "N+1개월차부터 정가" 경고를 띄운다
        "priceRisesAfter": int(period) if is_promo and period and int(period) < COMPARE_MONTHS else None,
        "isOnlineOnly": bool(row.get("is_online_only")),
        "hasAddon": bool(row.get("ott_option_count")),
        "ageCondition": row.get("age_condition", ""),
        "sourceUrl": row.get("source_url", ""),
    }


def to_plan_items(rows: list[dict], ranked: list[dict] | None = None) -> list[dict]:
    """ranked(plan_id·score·reason 순서)에 맞춰 상세를 붙인다. ranked 없으면 목록 그대로."""
    if not rows:
        return []
    cheapest = min(r["discounted_fee"] for r in rows)
    by_id = {r["plan_id"]: r for r in rows}
    if ranked is None:
        return [
            to_plan_item(r, is_cheapest=r["discounted_fee"] == cheapest)
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
    ranges = {"lt10": (0, 10), "10to30": (10, 30), "30to50": (30, 50), "50to100": (50, 100), "gte100": (100, float("inf"))}
    if bucket in ranges:
        lo, hi = ranges[bucket]
        return lo <= gb < hi
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
    return False


# 그룹 안에서는 OR, 그룹 사이에서는 AND. 시안의 체크박스 동작 그대로.
PRICE_RANGES = {"lt10k": (0, 10000), "10to20k": (10000, 20000), "20to30k": (20000, 30000), "30to50k": (30000, 50000), "50to70k": (50000, 70000), "gte70k": (70000, float("inf"))}


def _in_price_bucket(row: dict, bucket: str) -> bool:
    bounds = PRICE_RANGES.get(bucket)
    return bounds is not None and bounds[0] <= row["discounted_fee"] < bounds[1]


FILTER_GROUPS = {
    "price": (list(PRICE_RANGES), _in_price_bucket),
    "networks": (["SKT", "KT", "LGU+", "MNO"], _in_network),
    "data": (["lt10", "10to30", "30to50", "50to100", "gte100", "unlimited"], _in_data_bucket),
    "voice": (["unlimited", "quota", "none"], _in_voice),
    "flags": (["online_only", "addon", "promo"], _has_flag),
}

SORTS = {
    "fee_asc": (lambda r: r["discounted_fee"], False),
    "fee_desc": (lambda r: r["discounted_fee"], True),
    "data_desc": (lambda r: (r["data_unlimited"], r.get("data_gb") or 0), True),
    "total_asc": (lambda r: six_month_cost(r), False),
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
    # 할인 기간이 비교 구간보다 짧으면 남은 달은 정가
    assert six_month_cost({"discounted_fee": 19800, "monthly_fee": 24800, "discount_period_months": 7}) == 118800
    assert six_month_cost({"discounted_fee": 19800, "monthly_fee": 24800, "discount_period_months": 2}) == 138800
    # 할인 기간 미기재 = 무기한
    assert six_month_cost({"discounted_fee": 17500, "monthly_fee": 17500, "discount_period_months": None}) == 105000

    from agent.data import get_plan, all_plans

    rows = all_plans()
    assert len(rows) > 2000
    item = to_plan_item(rows[0], rank=1, score=90)
    assert item["best"] and item["total"].endswith("원") and isinstance(item["hash"], list)

    # 데이터 표기에 소진 후 속도가 섞여 들어가면 화면에서 두 번 나온다
    throttled = next(r for r in rows if not r["data_unlimited"] and r.get("qos_mbps") and r.get("data_gb"))
    assert "소진" not in to_plan_item(throttled)["data"]
    assert to_plan_item(throttled)["data"].endswith("GB")
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
