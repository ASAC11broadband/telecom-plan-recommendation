"""원본 합성 가입 이력으로 소프트 세그먼트 인기안과 개인화 이웃안을 비교한다.

세그먼트 인기안은 사용자의 세그먼트별 소속 확률과 세그먼트별 요금제 인기를 가중 합산한다.
개인화 이웃안은 프로필이 가까운 가입자들의 요금제 선택을 거리 가중 집계한다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from sklearn.cluster import KMeans
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parent.parent
# 합성 가입 이력은 make_synthetic_customers.py 가 현행 카탈로그로 만든다. 요금제는 그
# 카탈로그를 그대로 보므로, 세 방법(세그먼트·코사인·MCDA)이 같은 후보군 위에서 비교된다.
CUSTOMERS_CSV = ROOT / "data" / "synthetic_original" / "customers_mvno.csv"
PLANS_CSV = ROOT / "data" / "통신요금제_통합데이터_최종.csv"

# 성별은 추천 근거로 쓰지 않는다. 사용량·예산·필요 조건만 쓴다.
FEATURE_COLUMNS = (
    "age",
    "data_gb_month",
    "budget_krw",
    "voice_minutes_need",
    "sms_count_need",
    "data_unlimited_need",
    "voice_unlimited_need",
    "sms_unlimited_need",
    "ott_required",
    "ott_want_present",
)


def _flag(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"}).astype(int)


def _plan_key(series: pd.Series) -> pd.Series:
    """CSV를 읽으며 숫자로 추론된 요금제 ID를 안정적인 문자열 키로 바꾼다."""
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.astype("Int64").astype(str).where(numeric.notna(), series.astype(str).str.strip())


def load_interactions(
    customers_path: Path = CUSTOMERS_CSV,
    plans_path: Path = PLANS_CSV,
) -> pd.DataFrame:
    """가입 이력에 당시 요금제 스냅샷의 이름을 붙여, 평가 가능한 행만 반환한다."""
    customers = pd.read_csv(customers_path)
    plans = pd.read_csv(plans_path, dtype={"plan_id": str})

    customers = customers.copy()
    customers["source_plan_id"] = _plan_key(customers["source_plan_id"])
    plans = plans.dropna(subset=["plan_id", "plan_name"]).copy()
    plans["plan_id"] = plans["plan_id"].astype(str).str.strip()
    plan_labels = plans[["plan_id", "plan_name"]].drop_duplicates("plan_id")

    joined = customers.merge(
        plan_labels,
        how="inner",
        left_on="source_plan_id",
        right_on="plan_id",
        validate="many_to_one",
    ).drop(columns="plan_id")
    if joined.empty:
        raise ValueError("합성 가입 이력과 요금제 스냅샷을 연결하지 못했습니다.")
    result = joined.reset_index(drop=True)
    result.attrs["raw_customer_rows"] = len(customers)
    return result


def feature_frame(rows: pd.DataFrame) -> pd.DataFrame:
    """원본 합성데이터의 사용자 입력 컬럼을 군집·이웃 검색용 수치 벡터로 만든다."""
    features = pd.DataFrame(index=rows.index)
    for name in ("age", "data_gb_month", "budget_krw", "voice_minutes_need", "sms_count_need"):
        features[name] = pd.to_numeric(rows[name], errors="coerce").fillna(0.0)
    for name in ("data_unlimited_need", "voice_unlimited_need", "sms_unlimited_need", "ott_required"):
        features[name] = _flag(rows[name])
    features["ott_want_present"] = rows["ott_want"].fillna("").astype(str).str.strip().ne("").astype(int)
    return features.loc[:, FEATURE_COLUMNS].astype(float)


def holdout_100(interactions: pd.DataFrame, seed: int = 42, size: int = 100) -> tuple[pd.DataFrame, pd.DataFrame]:
    """학습 데이터에 정답 요금제가 남도록 100명의 신규 사용자를 고정 분리한다."""
    if size <= 0:
        raise ValueError("holdout size는 1 이상이어야 합니다.")
    remaining = Counter(interactions["source_plan_id"])
    picked: list[int] = []
    for index in np.random.default_rng(seed).permutation(interactions.index):
        plan_id = interactions.at[index, "source_plan_id"]
        if remaining[plan_id] <= 1:
            continue
        picked.append(int(index))
        remaining[plan_id] -= 1
        if len(picked) == size:
            break
    if len(picked) != size:
        raise ValueError(f"정답 요금제가 학습셋에 남는 {size}건을 만들 수 없습니다.")
    test = interactions.loc[picked].copy().reset_index(drop=True)
    train = interactions.drop(index=picked).copy().reset_index(drop=True)
    return train, test


@dataclass
class RecommendationModel:
    scaler: StandardScaler
    kmeans: KMeans
    membership_temperature: float
    neighbors: NearestNeighbors
    train: pd.DataFrame
    train_membership: np.ndarray
    plan_ids: list[str]
    plan_popularity: np.ndarray

    def plan_name(self, plan_id: str) -> str:
        return str(self.train.loc[self.train["source_plan_id"] == plan_id, "plan_name"].iloc[0])


def _squared_distances(matrix: np.ndarray, centers: np.ndarray) -> np.ndarray:
    return ((matrix[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)


def _soft_membership(distances: np.ndarray, temperature: float) -> np.ndarray:
    """세그먼트 중심점과의 거리를 합계가 1인 소속 확률로 바꾼다."""
    logits = -distances / temperature
    logits -= logits.max(axis=1, keepdims=True)
    weights = np.exp(logits)
    return weights / weights.sum(axis=1, keepdims=True)


def fit_model(training: pd.DataFrame, clusters: int = 6, neighbors: int = 100) -> RecommendationModel:
    """확률 세그먼트와 프로필 최근접 이웃 인덱스를 학습한다."""
    if clusters < 2 or len(training) < clusters:
        raise ValueError("군집 수보다 충분한 학습 행이 필요합니다.")
    train = training.reset_index(drop=True)
    matrix = feature_frame(train).to_numpy()
    scaler = StandardScaler().fit(matrix)
    scaled = scaler.transform(matrix)
    kmeans = KMeans(n_clusters=clusters, n_init=20, random_state=42).fit(scaled)
    distances = _squared_distances(scaled, kmeans.cluster_centers_)
    ordered = np.sort(distances, axis=1)
    # 학습 사용자의 가장 가까운 두 세그먼트 간 거리를 기준으로 소속도 온도를 정한다.
    # 전형적 경계 사용자는 가까운 두 세그먼트에 대략 80:20 비율로 나뉜다.
    temperature = max(float(np.median(ordered[:, 1] - ordered[:, 0]) / np.log(4)), 1e-6)
    membership = _soft_membership(distances, temperature)
    plan_ids = sorted(train["source_plan_id"].astype(str).unique())
    plan_index = {plan_id: index for index, plan_id in enumerate(plan_ids)}
    popularity = np.full((len(plan_ids), clusters), 0.1)  # 작은 세그먼트·희소 요금제 평활화
    np.add.at(
        popularity,
        [plan_index[plan_id] for plan_id in train["source_plan_id"].astype(str)],
        membership,
    )
    popularity /= popularity.sum(axis=0, keepdims=True)
    nearest = NearestNeighbors(n_neighbors=min(neighbors, len(train)), metric="euclidean").fit(scaled)
    return RecommendationModel(scaler, kmeans, temperature, nearest, train, membership, plan_ids, popularity)


def _ranked_plans(scores: dict[str, float], model: RecommendationModel, limit: int) -> list[dict[str, Any]]:
    return [
        {"plan_id": plan_id, "plan_name": model.plan_name(plan_id), "score": round(float(score), 6)}
        for plan_id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def segment_popularity_recommend(model: RecommendationModel, profile: pd.DataFrame, limit: int = 5) -> dict[str, Any]:
    """초기안: 세그먼트 소속 확률 × 세그먼트별 요금제 인기 점수로 추천한다."""
    vector = model.scaler.transform(feature_frame(profile).to_numpy())
    membership = _soft_membership(
        _squared_distances(vector, model.kmeans.cluster_centers_), model.membership_temperature,
    )[0]
    scores = dict(zip(model.plan_ids, model.plan_popularity @ membership))
    return {
        "segment": int(membership.argmax()),
        "segment_probabilities": [round(float(value), 6) for value in membership],
        "ranking_method": "segment membership probability × smoothed plan popularity",
        "ranked": _ranked_plans(scores, model, limit),
    }


def neighbor_recommend(model: RecommendationModel, profile: pd.DataFrame, limit: int = 5) -> dict[str, Any]:
    """전환안: 가까운 사용자 100명의 가입 요금제를 거리 가중으로 집계한다.

    가입 이력이 사용자당 하나뿐이므로 순수 협업필터링이 아니라, 프로필 유사도를
    함께 쓰는 KNN 개인화 추천이다.
    """
    vector = model.scaler.transform(feature_frame(profile).to_numpy())
    distances, indices = model.neighbors.kneighbors(vector)
    scores: defaultdict[str, float] = defaultdict(float)
    for distance, index in zip(distances[0], indices[0]):
        plan_id = str(model.train.iloc[int(index)]["source_plan_id"])
        scores[plan_id] += 1.0 / (float(distance) + 0.05)
    membership = _soft_membership(
        _squared_distances(vector, model.kmeans.cluster_centers_), model.membership_temperature,
    )[0]
    return {
        "segment": int(membership.argmax()),
        "segment_probabilities": [round(float(value), 6) for value in membership],
        "ranked": _ranked_plans(dict(scores), model, limit),
    }


def evaluate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """정답 가입 요금제 재현성과 Top-5 반복도를 함께 계산한다."""
    ranks: list[int | None] = []
    signatures: list[tuple[str, ...]] = []
    for result in results:
        top5 = [item["plan_id"] for item in result["ranked"]]
        signatures.append(tuple(top5))
        try:
            ranks.append(top5.index(result["actual_plan_id"]) + 1)
        except ValueError:
            ranks.append(None)
    frequency = Counter(signatures)
    repeated = sum(count for signature, count in frequency.items() if signature and count > 1)
    return {
        "hit_at_5": round(sum(rank is not None for rank in ranks) / len(results), 4) if results else 0.0,
        "mrr_at_5": round(sum(1 / rank for rank in ranks if rank) / len(results), 4) if results else 0.0,
        "unique_top5_count": len(frequency),
        "repeated_top5_count": repeated,
        "repeated_top5_rate": round(repeated / len(results), 4) if results else 0.0,
    }


if __name__ == "__main__":
    interactions = load_interactions()
    train, test = holdout_100(interactions)
    model = fit_model(train)
    results = []
    for _, row in test.iterrows():
        recommendation = segment_popularity_recommend(model, row.to_frame().T)
        results.append({**recommendation, "actual_plan_id": row["source_plan_id"]})
    assert abs(sum(results[0]["segment_probabilities"]) - 1.0) < 1e-5
    print({"interaction_rows": len(interactions), "holdout_users": len(test), **evaluate(results)})
