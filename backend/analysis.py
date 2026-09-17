"""동일 CSV로 다시 계산하는 EDA와 방법론 카드. LLM은 이 수치를 생성하지 않는다."""
from collections import Counter
from functools import lru_cache
from hashlib import sha256
import json
from statistics import mean, median

from agent.data import all_plans, PLANS_CSV, BENEFITS_CSV, DATA_TIERS, UNLIMITED_QOS_MBPS
from agent.mcda import CRITERIA, _WEIGHT_DATA_PATH, _PRICE_HORIZON_MONTHS

LABELS = dict(zip(CRITERIA, ["가격", "데이터", "소진 후 속도", "혜택", "통화", "테더링"]))


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
    sensitivity = []
    for speed in [0.4, 1.0, 3.0, 5.0]:
        selected = [r for r in rows if r["data_unlimited"] or (r["qos_mbps"] or 0) >= speed]
        sensitivity.append({"threshold": speed, "count": len(selected),
                            "under30k": sum(r["discounted_fee"] <= 30000 for r in selected)})
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
        "unlimitedPolicy": {"threshold": UNLIMITED_QOS_MBPS, "sensitivity": sensitivity,
                            "definition": "기본량 무제한 또는 소진 후 1Mbps 이상. 속도 제한 없는 무제한 요청은 기본량 무제한만 선택."},
        "weights": [{"key": key, "label": LABELS[key],
                     "mean": round(mean(v[i] for v in vectors), 5),
                     "min": round(min(v[i] for v in vectors), 5),
                     "max": round(max(v[i] for v in vectors), 5)} for i, key in enumerate(CRITERIA)],
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
            "가입자 데이터 기반의 시장 사전분포입니다. 개인 만족도나 추천 정확도 검증을 대신하지 않습니다.",
            "학습 출처는 MVNO 2,158건으로 기록되어 있습니다. 통신 3사로의 적용에는 분포 차이가 있습니다.",
            "1Mbps 기준은 서비스 정책입니다. 임계값별 후보 수 비교는 민감도 분석이며 최적 기준의 증명이 아닙니다.",
            "1위 수용도는 고정된 표본 가중치에서 1위가 된 비율입니다. 동일 효용이면 ID 정렬 영향을 받을 수 있습니다.",
            "6개월 비용은 단기 비교, 12개월 평균 비용은 기존 순위 계산용입니다. 할인 기간 미상은 현재가 유지 가정입니다.",
            "혜택 환산액은 조건부 참고 가치입니다. 모두 현금 할인으로 받을 수 있다는 뜻이 아닙니다.",
        ],
    }
