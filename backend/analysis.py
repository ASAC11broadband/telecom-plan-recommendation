"""동일 CSV로 다시 계산하는 EDA와 방법론 카드. LLM은 이 수치를 생성하지 않는다."""
from collections import Counter
from functools import lru_cache
from hashlib import sha256
import json
from statistics import mean, median

from agent.agents.recommend import TOP_N
from agent.data import (all_plans, baseline_plans, BASELINE_DATE, PLANS_CSV, BENEFITS_CSV,
                        DATA_TIERS, UNLIMITED_MIN_GB, UNLIMITED_QOS_MBPS)
from agent.mcda import (CRITERIA, _BASE_WEIGHTS, _WEIGHT_DATA_PATH,
                        _PRICE_HORIZON_MONTHS, _utility_rows)

LABELS = dict(zip(CRITERIA, ["가격", "데이터", "소진 후 속도", "혜택", "통화", "테더링"]))


# 발표에서 SMAA-2 를 "더 나은 추천을 만든 장치"로 말하면 "평균 가중치와 뭐가 다르냐"는
# 질문에 무너진다. 실제로 거의 다르지 않기 때문이다. 대신 그 사실을 먼저 내보인다 -
# **가중치를 300세트 흔들어도 상위 후보가 그대로**라는 것이 이 방법이 낼 수 있는 결론이고,
# "우리가 가중치를 임의로 골라서 나온 순위가 아니다"의 근거가 된다.
#
# 고정 분석본으로 계산한다. 최신 수집본으로 재면 수집할 때마다 발표 숫자가 흔들린다.
_ROBUSTNESS_CASES = (
    ("무제한 요청", {"data_unlimited": True}),
    ("데이터 20GB 이상·3만원 이하", {"min_data_gb": 20, "budget_max_won": 30000}),
    ("조건 없음", {}),
)


def _mean_weight() -> list[float]:
    return [mean(vector[i] for vector in _BASE_WEIGHTS) for i in range(len(CRITERIA))]


def _rank_ids(candidates: list[dict], weights: list[float], profile: dict) -> list[str]:
    utilities = _utility_rows(candidates, profile)
    totals = [sum(w * u for w, u in zip(weights, row)) for row in utilities]
    order = sorted(range(len(candidates)),
                   key=lambda i: (-totals[i], str(candidates[i]["plan_id"])))
    return [str(candidates[i]["plan_id"]) for i in order]


def _weight_robustness(baseline_rows: list[dict], top_n: int) -> list[dict]:
    """가중치 표본 300세트의 순위와 평균 가중치 하나의 순위를 견준다.

    둘이 같으면 "가중치 선택에 민감하지 않은 추천"이라는 뜻이다. 다르면 어느 조건에서
    갈리는지가 그대로 드러난다. 어느 쪽이든 발표에서 말할 수 있는 사실이 된다.
    """
    average = _mean_weight()
    report = []
    for label, profile in _ROBUSTNESS_CASES:
        pool = [row for row in baseline_rows if _eligible(row, profile)]
        if len(pool) < top_n:
            continue
        sampled = [_rank_ids(pool, weight, profile) for weight in _BASE_WEIGHTS]
        averaged = _rank_ids(pool, average, profile)
        # 표본마다 상위 top_n 이 평균 가중치의 상위 top_n 과 몇 개나 겹치는지
        overlaps = [len(set(order[:top_n]) & set(averaged[:top_n])) for order in sampled]
        report.append({
            "case": label,
            "candidates": len(pool),
            "topN": top_n,
            "samples": len(_BASE_WEIGHTS),
            "minOverlap": min(overlaps),
            "identicalTopN": sum(1 for value in overlaps if value == top_n),
            "sameFirst": sum(1 for order in sampled if order[0] == averaged[0]),
        })
    return report


def _eligible(row: dict, profile: dict) -> bool:
    """민감도 비교에 쓸 최소 필터. 서비스 필터(filter_candidates)의 부분집합이다."""
    if profile.get("data_unlimited") and not row["effective_unlimited"]:
        return False
    if profile.get("min_data_gb") is not None:
        if not row["data_unlimited"] and (row["data_gb"] or 0) < profile["min_data_gb"]:
            return False
    if profile.get("budget_max_won") is not None and row["discounted_fee"] > profile["budget_max_won"]:
        return False
    return True


@lru_cache(maxsize=1)
def analysis_snapshot() -> dict:
    rows = all_plans()
    total = len(rows)
    payload = json.loads(_WEIGHT_DATA_PATH.read_text(encoding="utf-8"))
    vectors = payload["vectors"]
    dates = sorted({row["crawled_at"][:10] for row in rows if row["crawled_at"]})
    counts = Counter(row["carrier_type"] for row in rows)
    tiers = Counter(row["data_tier"] for row in rows)
    qos = Counter(row["qos_mbps"] for row in rows if not row["data_unlimited"])
    missing = [
        ("tethering", "테더링 제공량", sum(row["tethering_gb"] is None for row in rows)),
        ("qos", "국내 QoS 속도", sum(row["qos_mbps"] is None for row in rows)),
        ("benefits", "금액이 확인된 혜택 없음", sum(not row["benefit_value_won"] for row in rows)),
        ("network", "LTE / 5G 구분", sum(not row["network_gen"] for row in rows)),
    ]
    # '무제한' 정의의 두 문턱을 함께 흔든다. 속도만 흔들면 실제 정의와 다른 표가 된다 -
    # 서비스는 제공량과 소진 후 속도를 함께 본다. minGb=0 줄이 속도만 보던 옛 정의다.
    #
    # 이 표는 **문턱을 왜 그 값으로 정했는지**를 보이는 자리라 고정 분석본으로 계산한다.
    # 최신 수집본으로 계산하면 수집할 때마다 근거 숫자가 흔들려 재현이 안 된다.
    # 위쪽 현황 통계(총계·결측·등급)는 지금 서비스하는 데이터라 최신본 그대로다.
    baseline = baseline_plans()
    def _opt(value):
        return None if value != value else float(value)   # NaN 판정

    baseline_rows = [
        {"plan_id": str(r.plan_id),
         "data_unlimited": bool(r.data_unlimited),
         "effective_unlimited": bool(r.effective_unlimited),
         "data_tier": str(r.data_tier),
         "qos_mbps": _opt(r.qos_mbps),
         "data_gb": _opt(r.data_gb),
         "tethering_gb": _opt(r.tethering_gb),
         "voice_unlimited": bool(r.voice_unlimited),
         "voice_minutes": _opt(r.voice_minutes),
         "discounted_fee": int(r.discounted_fee),
         "monthly_fee": int(r.monthly_fee),
         "discount_period_months": _opt(r.discount_period_months)}
        for r in baseline.itertuples()
    ]
    sensitivity = []
    for min_gb in [0.0, 70.0, UNLIMITED_MIN_GB, 150.0]:
        for speed in [1.0, 3.0, UNLIMITED_QOS_MBPS]:
            selected = [r for r in baseline_rows if r["data_unlimited"]
                        or ((r["qos_mbps"] or 0) >= speed and (r["data_gb"] or 0) >= min_gb)]
            sensitivity.append({
                "minGb": min_gb, "threshold": speed, "count": len(selected),
                "medianDataGb": median([r["data_gb"] for r in selected
                                        if not r["data_unlimited"] and r["data_gb"] is not None]
                                       or [0]),
                "under30k": sum(r["discounted_fee"] <= 30000 for r in selected),
                "current": min_gb == UNLIMITED_MIN_GB and speed == UNLIMITED_QOS_MBPS,
            })
    return {
        "total": total,
        "dataFingerprint": sha256(PLANS_CSV.read_bytes() + BENEFITS_CSV.read_bytes()).hexdigest()[:16],
        "weightFingerprint": sha256(_WEIGHT_DATA_PATH.read_bytes()).hexdigest()[:16],
        "collectedFrom": dates[0] if dates else None,
        "collectedTo": dates[-1] if dates else None,
        "carriers": [{"name": key, "count": count,
                       "medianFee": median(r["discounted_fee"] for r in rows if r["carrier_type"] == key)}
                      for key, count in sorted(counts.items())],
        "quality": [{"key": key, "label": label, "missing": count,
                     "percent": round(count / total * 100, 1)} for key, label, count in missing],
        "suspectQosCount": sum(r["qos_source_suspect"] for r in rows),
        "unknownPromoCount": sum(r["discounted_fee"] < r["monthly_fee"] and
                                 r["discount_period_months"] is None for r in rows),
        "duplicateNameRows": total - len({r["plan_name"] for r in rows}),
        "tiers": [{"name": DATA_TIERS[key], "count": tiers[key]} for key in DATA_TIERS],
        "qosDistribution": [{"name": "미확인" if speed is None else f"{speed:g}Mbps", "count": count}
                            for speed, count in sorted(qos.items(), key=lambda x: -1 if x[0] is None else x[0])],
        "unlimitedPolicy": {"threshold": UNLIMITED_QOS_MBPS, "minGb": UNLIMITED_MIN_GB,
                            # 민감도 표의 기준 데이터. 위 현황 통계와 기준일이 다르다.
                            "baselineDate": BASELINE_DATE,
                            "baselineTotal": len(baseline_rows),
                            "sensitivity": sensitivity,
                            "definition": (
                                f"기본량 무제한, 또는 제공량 {UNLIMITED_MIN_GB:g}GB 이상이면서 소진 후 "
                                f"{UNLIMITED_QOS_MBPS:g}Mbps 이상. 두 조건을 함께 본다. "
                                "'완전 무제한' 요청은 기본량 무제한만 선택."
                            )},
        "weights": [{"key": key, "label": LABELS[key],
                     "mean": round(mean(v[i] for v in vectors), 5),
                     "min": round(min(v[i] for v in vectors), 5),
                     "max": round(max(v[i] for v in vectors), 5)} for i, key in enumerate(CRITERIA)],
        # 가중치를 300세트 흔든 순위와 평균 가중치 하나의 순위를 견준 결과.
        # "SMAA-2 덕에 추천이 좋아졌다"가 아니라 "가중치를 바꿔도 결론이 같다"를 보이는 값이다.
        "weightRobustness": _weight_robustness(baseline_rows, TOP_N),
        "weightSource": payload.get("source", "출처 미기재"),
        "weightSamples": len(vectors),
        "rankingMonths": _PRICE_HORIZON_MONTHS,
        "method": [
            {"title": "수집·정규화", "body": "통신사/알뜰폰 데이터를 공통 스키마로 통합하고 plan_id로 혜택을 결합합니다."},
            {"title": "EDA·변수 설계", "body": "가격·기본 데이터·QoS·혜택·통화·테더링의 분포와 결측을 확인합니다. 미확인을 미제공으로 단정하지 않습니다."},
            {"title": "AI 요구조건 추출", "body": "자연어를 구조화하고 사용시간은 규칙으로 GB를 추정합니다. 필수조건과 목표량을 구분합니다."},
            {"title": "SMAA-2 추천", "body": "기존 부트스트랩 가중치 표본마다 효용을 합산하고 기대순위로 정렬합니다. 우선순위는 기존 보정 규칙을 적용합니다."},
            {"title": "설명·검증", "body": "AI가 원본 후보로 설명을 작성하고 코드 검증 및 AI 사실 대조를 수행합니다. 최종 검증 미통과는 화면에 알립니다."},
        ],
        "limitations": [
            "가중치 300세트를 흔들어도 상위 후보가 그대로입니다(weightRobustness). 즉 이 추천은 "
            "가중치 선택에 민감하지 않으며, 평균 가중치 하나로 계산해도 결과가 같습니다. "
            "SMAA-2 는 추천을 더 좋게 만드는 장치가 아니라, 임의로 고른 가중치 때문에 나온 "
            "순위가 아님을 보이는 검사입니다.",
            "가입자 데이터 기반의 시장 사전분포입니다. 개인 만족도나 추천 정확도 검증을 대신하지 않습니다.",
            "학습 출처는 MVNO 2,158건으로 기록되어 있습니다. 통신 3사로의 적용에는 분포 차이가 있습니다.",
            f"기준 유도는 {BASELINE_DATE} 고정 분석본으로 하고, 서비스 추천은 최신 수집본에 "
            "그 기준을 적용합니다. 위 현황 통계와 무제한 민감도 표는 기준일이 다릅니다.",
            f"'무제한' 기준(기본량 무제한 또는 제공량 {UNLIMITED_MIN_GB:g}GB 이상＋소진 후 "
            f"{UNLIMITED_QOS_MBPS:g}Mbps 이상)은 서비스 정책입니다. 수집 데이터에서 상품 군집이 "
            "갈리는 경계를 따랐을 뿐, 규제나 표준이 정한 값도 최적 기준의 증명도 아닙니다. "
            "문턱별 후보 수 비교는 민감도 분석입니다.",
            "1위 수용도는 고정된 표본 가중치에서 1위가 된 비율입니다. 동일 효용이면 ID 정렬 영향을 받을 수 있습니다.",
            "추천과 총비용은 12개월 기준입니다. 할인 기간 미상은 현재가 유지 가정이며, 페이백 반영 표시가만 있는 상품은 추천에서 제외합니다.",
            "혜택 환산액은 조건부 참고 가치입니다. 모두 현금 할인으로 받을 수 있다는 뜻이 아닙니다.",
        ],
    }
