"""사용자 요구와 요금제 속성을 같은 축으로 인코딩해 코사인 유사도로 추천한다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity

from .segmentation import PLANS_CSV


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


def prepare_plan_catalog(path: Path = PLANS_CSV) -> pd.DataFrame:
    """알뜰폰 요금제의 공급 속성·가격 효율을 코사인 비교용 벡터로 준비한다."""
    plans = pd.read_csv(path, dtype={"plan_id": str})
    plans = plans.loc[plans["carrier_type"].eq("MVNO")].dropna(subset=["plan_id", "plan_name"]).copy()
    plans["plan_id"] = plans["plan_id"].astype(str).str.strip()
    plans = plans.drop_duplicates("plan_id")

    data_unlimited = plans["data_unlimited"].map(_flag)
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


if __name__ == "__main__":
    catalog = prepare_plan_catalog()
    example = {
        "data_gb_month": 20, "voice_minutes_need": 300, "sms_count_need": 50,
        "data_unlimited_need": False, "voice_unlimited_need": True, "sms_unlimited_need": True,
        "budget_krw": 20_000, "ott_required": False, "ott_want": "",
    }
    result = recommend_by_cosine(catalog, example)
    assert len(result["ranked"]) == 5
    print({"catalog_plans": len(catalog), "candidate_count": result["candidate_count"]})
