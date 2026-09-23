"""콘텐츠 기반 추천 두 판.

`recommend_by_cosine` 은 교과서대로다 - 사용자 요구와 요금제 속성을 같은 축에 올리고
코사인 유사도로 정렬한다. `recommend_by_coverage` 는 그 결함을 고친 판이다.

코사인이 이 데이터에서 무너지는 지점은 셋이다:

1. **가격을 벡터 안에 넣었다.** 양쪽 다 `1 - 요금/상한` 이라 "예산과 비슷할수록" 높다.
   예산을 5만원이라 말하면 같은 100GB 인데 14,300원 대신 41,030원을 1순위로 올린다.
2. **질의에 없는 축이 벌점이 된다.** 실제 질의 100건에서 8축 중 채워지는 건 평균 1.29개다.
   질의가 0인 축은 분자에 0을 기여하고 분모(요금제 노름)만 키운다 - 통화 1,000분을
   더 주는 6,900원 요금제가 그 때문에 코사인 0.598, 아무것도 안 주는 14,300원이 0.991.
3. **과잉 제공에 벌점.** `min(1, 요구/제공)` 이라 50GB 필요한데 100GB 주면 0.5 다.
   더 주는 쪽이 깎이니 지배관계가 깨진다.

고친 판은 가격을 벡터 밖 분모로 빼고(= 천원당 적합도), 부족분만 벌점을 주는 비대칭
충족도를 쓴다. 대칭 유사도(코사인·유클리드·맨해튼·자카드) 넷은 서로 거의 차이가 없었다 -
전부 "닮음"을 재기 때문에 같은 실패를 한다. 비대칭만 다르다.

그래서 고친 판은 엄밀히 말하면 유사도가 아니다. `sim(a,b) != sim(b,a)` 라 대칭성이
깨지고, 수학적으로는 충족도 함수다. 그리고 그건 `agent.mcda._minimum_fit` 과 같은
모양이다 - 유사도를 고치다 보니 유사도가 아니게 됐고, 그게 효용 함수였다.

두 판을 다 남겨둔 것은 이 과정을 숫자로 보여주기 위해서다.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity

from .data import all_plans


# 데이터, 통화, SMS, 각 무제한 필요 여부, 가격 민감도, OTT 필요 여부.
# 요금제의 공급 속성과 사용자의 요구를 같은 0~1 축에서 비교한다.
VECTOR_COLUMNS = (
    "vector_data", "vector_voice", "vector_sms", "vector_data_unlimited",
    "vector_voice_unlimited", "vector_sms_unlimited", "vector_price_sensitivity", "vector_ott",
)
WEIGHTS = np.array((1.5, 1.0, 0.5, 1.0, 0.7, 0.4, 0.8, 0.6))
DATA_CAP_GB = 200.0
VOICE_CAP_MINUTES = 1_000.0
SMS_CAP_COUNT = 500.0
FEE_CAP_WON = 60_000.0


def _flag(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _number(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _unit(value: Any, cap: float) -> float:
    return min(_number(value) / cap, 1.0)


def prepare_plan_catalog(rows: list[dict] | None = None) -> pd.DataFrame:
    """알뜰폰 요금제의 공급 속성·가격 효율을 비교용 벡터로 준비한다.

    `agent.data.all_plans()` 를 거쳐 서비스와 같은 판을 쓴다. CSV 를 직접 읽던 것을
    바꾼 이유가 둘이다:

    - **'무제한'의 정의가 한 곳에서 나온다.** 원본 `data_unlimited` 플래그를 그대로
      200GB 로 환산하면 *7GB 쓰고 1Mbps* 짜리가 200GB 로 둔갑한다. `effective_unlimited`
      는 제공량 100GB 와 소진 후 10Mbps 를 **둘 다** 넘어야 무제한으로 친다.
    - **청구액이 확인되지 않은 페이백 상품을 뺀다.** 표시가가 실제 납부액이 아니라
      가격 축이 통째로 어긋난다. MCDA 쪽 `filter_candidates` 는 이미 빼고 있었다.
    """
    plans = pd.DataFrame(all_plans() if rows is None else rows)
    plans = plans.loc[plans["carrier_type"].eq("MVNO")].dropna(subset=["plan_id", "plan_name"]).copy()
    plans["plan_id"] = plans["plan_id"].astype(str).str.strip()
    plans = plans.drop_duplicates("plan_id")
    plans = plans[plans["billing_price_known"].map(_flag)]

    data_unlimited = plans["effective_unlimited"].map(_flag)
    voice_unlimited = plans["voice_unlimited"].map(_flag)
    sms_unlimited = plans["sms_unlimited"].map(_flag)
    plans["data_capacity"] = np.where(data_unlimited, DATA_CAP_GB, plans["data_gb"].map(_number))
    plans["voice_capacity"] = np.where(voice_unlimited, VOICE_CAP_MINUTES, plans["voice_minutes"].map(_number))
    plans["sms_capacity"] = np.where(sms_unlimited, SMS_CAP_COUNT, plans["sms_count"].map(_number))
    discounted = pd.to_numeric(plans["discounted_fee"], errors="coerce")
    monthly = pd.to_numeric(plans["monthly_fee"], errors="coerce")
    # 0원 할인가는 결측이 아니라 실제 프로모션 가격이므로 그대로 보존한다.
    plans["effective_fee"] = discounted.where(discounted.notna(), monthly).fillna(0.0)
    plans["has_ott"] = plans["ott_options"].fillna("").astype(str).str.strip().ne("")
    plans["data_unlimited_flag"] = data_unlimited
    plans["voice_unlimited_flag"] = voice_unlimited
    plans["sms_unlimited_flag"] = sms_unlimited
    plans.loc[:, VECTOR_COLUMNS] = np.column_stack(
        (
            plans["data_capacity"].map(lambda value: _unit(value, DATA_CAP_GB)),
            plans["voice_capacity"].map(lambda value: _unit(value, VOICE_CAP_MINUTES)),
            plans["sms_capacity"].map(lambda value: _unit(value, SMS_CAP_COUNT)),
            data_unlimited.astype(float),
            voice_unlimited.astype(float),
            sms_unlimited.astype(float),
            1.0 - plans["effective_fee"].map(lambda value: _unit(value, FEE_CAP_WON)),
            plans["has_ott"].astype(float),
        )
    )
    return plans.reset_index(drop=True)


def _profile_values(profile: pd.Series | dict[str, Any]) -> dict[str, Any]:
    get = profile.get if isinstance(profile, dict) else profile.get
    raw_ott_want = get("ott_want", "")
    ott_want = "" if pd.isna(raw_ott_want) else str(raw_ott_want).strip()
    return {
        "data": _number(get("data_gb_month")),
        "voice": _number(get("voice_minutes_need")),
        "sms": _number(get("sms_count_need")),
        "data_unlimited": _flag(get("data_unlimited_need")),
        "voice_unlimited": _flag(get("voice_unlimited_need")),
        "sms_unlimited": _flag(get("sms_unlimited_need")),
        "budget": _number(get("budget_krw")),
        "ott": _flag(get("ott_required")) or bool(ott_want),
    }


def _candidate_mask(catalog: pd.DataFrame, values: dict[str, Any]) -> pd.Series:
    """유사도 계산 전에 명시된 최소 사용량·무제한 요구를 만족하지 않는 요금제를 제외한다."""
    data_ok = catalog["data_unlimited_flag"] | catalog["data_capacity"].ge(values["data"])
    voice_ok = catalog["voice_unlimited_flag"] | catalog["voice_capacity"].ge(values["voice"])
    sms_ok = catalog["sms_unlimited_flag"] | catalog["sms_capacity"].ge(values["sms"])
    return (
        data_ok
        & voice_ok
        & sms_ok
        & ((not values["data_unlimited"]) | catalog["data_unlimited_flag"])
        & ((not values["voice_unlimited"]) | catalog["voice_unlimited_flag"])
        & ((not values["sms_unlimited"]) | catalog["sms_unlimited_flag"])
    )


def _query_vector(values: dict[str, Any]) -> np.ndarray:
    return np.array(
        (
            _unit(values["data"], DATA_CAP_GB),
            _unit(values["voice"], VOICE_CAP_MINUTES),
            _unit(values["sms"], SMS_CAP_COUNT),
            float(values["data_unlimited"]),
            float(values["voice_unlimited"]),
            float(values["sms_unlimited"]),
            1.0 - _unit(values["budget"], FEE_CAP_WON),
            float(values["ott"]),
        )
    ) * WEIGHTS


def _resource_fit(candidates: pd.DataFrame, values: dict[str, Any]) -> np.ndarray:
    """최소 요구량을 만족한 후보 중 과도한 제공량을 줄이는 보정 점수다."""
    fits = []
    for column, need, unlimited in (
        ("data_capacity", values["data"], values["data_unlimited"]),
        ("voice_capacity", values["voice"], values["voice_unlimited"]),
        ("sms_capacity", values["sms"], values["sms_unlimited"]),
    ):
        if unlimited or need <= 0:
            fits.append(np.ones(len(candidates)))
        else:
            fits.append(np.minimum(1.0, need / candidates[column].clip(lower=1).to_numpy()))
    return np.mean(np.column_stack(fits), axis=1)


def recommend_by_cosine(catalog: pd.DataFrame, profile: pd.Series | dict[str, Any], limit: int = 5) -> dict[str, Any]:
    """하드 조건 필터 후 코사인 유사도를 주점수로 개인별 요금제를 정렬한다."""
    values = _profile_values(profile)
    candidates = catalog.loc[_candidate_mask(catalog, values)].copy()
    if candidates.empty:
        raise ValueError("입력 조건을 동시에 만족하는 요금제가 없습니다.")

    query = _query_vector(values).reshape(1, -1)
    plan_vectors = candidates.loc[:, VECTOR_COLUMNS].to_numpy(dtype=float) * WEIGHTS
    candidates["cosine_similarity"] = cosine_similarity(query, plan_vectors)[0]
    candidates["resource_fit"] = _resource_fit(candidates, values)
    candidates["affordability"] = np.where(
        candidates["effective_fee"].eq(0),
        1.0,
        np.minimum(1.0, values["budget"] / candidates["effective_fee"].clip(lower=1)),
    )
    candidates["ott_match"] = np.where(values["ott"], candidates["has_ott"].astype(float), 1.0)
    # 코사인이 주점수(70%)이며, 과잉 제공 방지·예산·OTT만 제한적으로 보정한다.
    candidates["final_score"] = (
        0.70 * candidates["cosine_similarity"]
        + 0.20 * candidates["resource_fit"]
        + 0.05 * candidates["affordability"]
        + 0.05 * candidates["ott_match"]
    )
    ranked = candidates.sort_values(["final_score", "plan_id"], ascending=[False, True]).head(limit)
    return {
        "candidate_count": len(candidates),
        "ranking_method": "hard requirement filter -> weighted cosine similarity -> resource/budget/OTT correction",
        "ranked": [
            {
                "plan_id": str(row.plan_id),
                "plan_name": str(row.plan_name),
                "cosine_similarity": round(float(row.cosine_similarity), 6),
                "final_score": round(float(row.final_score), 6),
            }
            for row in ranked.itertuples()
        ],
    }


# 부족분을 잴 때 축마다 얼마나 무겁게 볼지. WEIGHTS 의 용량·OTT 축만 쓴다
# (무제한 플래그는 하드 조건으로 빠지고, 가격은 벡터 밖 분모로 나간다).
_COVERAGE_AXES = (("data", "data_capacity", DATA_CAP_GB, 1.5),
                  ("voice", "voice_capacity", VOICE_CAP_MINUTES, 1.0),
                  ("sms", "sms_capacity", SMS_CAP_COUNT, 0.5))


def _coverage(candidates: pd.DataFrame, values: dict[str, Any]) -> np.ndarray:
    """요구 대비 부족분만 벌점. 과잉은 잘라내 0 으로 본다.

    `1 - Σ w·max(0, 요구-제공)/Σ w·요구`. 요구가 0 인 축은 분자·분모에 아무것도 더하지
    않아 **자동으로 빠진다** - 코사인처럼 분모만 키워 벌점이 되지 않는다. 실제 질의의
    8축 중 평균 1.29개만 채워지므로 이 성질이 결정적이다.
    """
    short = np.zeros(len(candidates))
    total = 0.0
    for key, column, cap, weight in _COVERAGE_AXES:
        need = float(values[key])
        if values[f"{key}_unlimited"] or need <= 0:
            continue
        scaled = weight / cap
        short += scaled * np.maximum(0.0, need - candidates[column].to_numpy())
        total += scaled * need
    if values["ott"]:
        short += 0.6 * (1.0 - candidates["has_ott"].astype(float).to_numpy())
        total += 0.6
    if total <= 0:  # 요구가 하나도 없는 질의 - 전 후보가 동점이고 가격만 남는다
        return np.ones(len(candidates))
    return 1.0 - short / total


def recommend_by_coverage(catalog: pd.DataFrame, profile: pd.Series | dict[str, Any],
                          limit: int = 5) -> dict[str, Any]:
    """비대칭 충족도 ÷ 월 요금. 코사인의 세 결함(모듈 설명 참고)을 고친 판이다.

    코사인 판과 하드 필터가 다르다. 무제한 요구와 **예산 상한**만 하드로 걸고, 용량은
    `_coverage` 가 부드럽게 본다 - 요구보다 5% 모자란 요금제를 후보에서 통째로 빼는
    것보다 점수를 깎는 게 맞다. 대신 예산은 코사인 판에 아예 없던 하드 조건이다.
    """
    values = _profile_values(profile)
    budget = values["budget"]
    mask = (
        ((not values["data_unlimited"]) | catalog["data_unlimited_flag"])
        & ((not values["voice_unlimited"]) | catalog["voice_unlimited_flag"])
        & ((not values["sms_unlimited"]) | catalog["sms_unlimited_flag"])
    )
    if budget > 0:
        mask &= catalog["effective_fee"].le(budget)
    candidates = catalog.loc[mask].copy()
    if candidates.empty:
        raise ValueError("입력 조건을 동시에 만족하는 요금제가 없습니다.")

    candidates["coverage"] = _coverage(candidates, values)
    # 천원당 적합도. 0원 프로모션은 나눗셈이 터지므로 100원을 바닥으로 둔다.
    candidates["final_score"] = (
        candidates["coverage"] / candidates["effective_fee"].clip(lower=100).to_numpy() * 10_000
    )
    ranked = candidates.sort_values(["final_score", "plan_id"], ascending=[False, True]).head(limit)
    return {
        "candidate_count": len(candidates),
        "ranking_method": "unlimited/budget hard filter -> asymmetric coverage / monthly fee",
        "ranked": [
            {
                "plan_id": str(row.plan_id),
                "plan_name": str(row.plan_name),
                "coverage": round(float(row.coverage), 6),
                "final_score": round(float(row.final_score), 6),
            }
            for row in ranked.itertuples()
        ],
    }


if __name__ == "__main__":
    catalog = prepare_plan_catalog()
    example = {
        "data_gb_month": 20, "voice_minutes_need": 300, "sms_count_need": 50,
        "data_unlimited_need": False, "voice_unlimited_need": True, "sms_unlimited_need": True,
        "budget_krw": 20_000, "ott_required": False, "ott_want": "",
    }
    assert len(recommend_by_cosine(catalog, example)["ranked"]) == 5
    assert len(recommend_by_coverage(catalog, example)["ranked"]) == 5

    # '무제한'은 agent.data 의 정의를 따른다 - 소량+저속 QoS형이 섞이면 안 된다.
    from .data import UNLIMITED_MIN_GB
    unlimited = catalog[catalog["data_unlimited_flag"]]
    qos_only = unlimited[~unlimited["data_unlimited"].map(_flag)]
    assert (qos_only["data_gb"].map(_number) >= UNLIMITED_MIN_GB).all(), \
        "기본량 무제한이 아닌데 무제한으로 잡힌 요금제는 제공량 문턱을 넘어야 한다"

    # 예산 단조성: 필요 조건을 고정하고 예산만 올렸을 때 추천 요금이 오르면 안 된다.
    # 코사인 판은 이걸 어긴다(가격 축이 "예산과 비슷할수록" 높아서). 고친 판은 지킨다.
    def _top_fee(fn, budget: int) -> float:
        picked = fn(catalog, {**example, "budget_krw": budget})["ranked"][0]["plan_id"]
        return float(catalog.loc[catalog["plan_id"].eq(picked), "effective_fee"].iloc[0])

    fees = [_top_fee(recommend_by_coverage, b) for b in (15_000, 25_000, 40_000, 60_000)]
    assert fees == sorted(fees) and fees[0] == fees[-1], f"예산을 올려도 추천 요금은 그대로여야 한다: {fees}"

    print("self-check ok: 무제한 정의 · 예산 단조성 · 두 판 각 5건")
    print({"catalog_plans": len(catalog),
           "cosine_candidates": recommend_by_cosine(catalog, example)["candidate_count"],
           "coverage_candidates": recommend_by_coverage(catalog, example)["candidate_count"]})
