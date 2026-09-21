# -*- coding: utf-8 -*-
"""가중치를 학습한 눈금과 서비스가 곱하는 눈금이 같은지 잰다.

    python -m experiments.weight_scale_check

가중치(agent/weight_bootstrap.json)는 notebooks/linear_spec_diagnostic.ipynb 가 만들었다:
변환(sqrt 요금·log1p 데이터) -> **z-score** -> Ridge, 표준화 계수 절댓값의 비율. 즉 "1 표준편차당
영향력"이다. 서비스(agent/mcda.py)는 그 가중치를 **0~1 효용**에 곱한다. 0~1 의 전체 폭이 표준편차의
몇 배인지는 축마다 다르므로, 같은 가중치라도 서비스에서 실제로 작동하는 영향력 비율은 학습 때와 다르다.

여기서는 (1) 노트북의 회귀를 고정 분석본에서 그대로 재현해 가중치가 맞는지 확인하고,
(2) 축별 "전체 폭 / 표준편차"를 재서 (3) 0~1 고정 범위 효용에 곱했을 때 학습된 영향력 비율을
보존하는 가중치가 무엇인지 계산한다.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from agent.data import BASELINE_PLANS_CSV
from agent.segmentation import ROOT

AXES = ("cost_month", "data_total_gb", "qos", "voice_min", "ott_value", "tether_gb")  # sms 는 회귀 전에 뺀다
SERVICE_NAME = {"cost_month": "price", "data_total_gb": "data", "qos": "qos", "voice_min": "voice",
                "ott_value": "benefit", "tether_gb": "tethering"}
TRANSFORM = {"cost_month": np.sqrt, "data_total_gb": np.log1p}


def _qos_mbps(value) -> float:
    match = re.match(r"([\d.]+)\s*(kbps|mbps)", str(value).strip().lower().replace(" ", ""))
    if pd.isna(value) or not match:
        return 0.0
    return float(match.group(1)) / (1000 if match.group(2) == "kbps" else 1)


def training_frame() -> tuple[pd.DataFrame, np.ndarray]:
    """노트북 build() 와 같은 피처. 고정 분석본(2026-08-21)의 알뜰폰 2,158건."""
    plans = pd.read_csv(BASELINE_PLANS_CSV, dtype={"plan_id": str}, low_memory=False)
    benefits = pd.read_csv(BASELINE_PLANS_CSV.with_name("통신요금제_혜택상세_최종.csv"), dtype={"plan_id": str})
    prices = pd.read_csv(ROOT / "data" / "ott_prices.csv")
    price_map = dict(zip(prices.service, prices.monthly_won.astype(int)))

    d = plans[plans.carrier_type.eq("MVNO") & ~plans.mvno_brand.isin(["SKT", "KT", "LG U+", "LGU+"])].copy()
    extra = benefits[benefits.benefit_category == "추가데이터"].copy()
    extra["gb"] = extra.benefit_name.fillna("").str.extract(r"([\d.]+)\s*GB", flags=re.I)[0].astype(float)
    d["extra_gb"] = d.plan_id.map(extra.groupby("plan_id").gb.sum()).fillna(0)
    d["cost_month"] = d.discounted_fee
    d["data_total_gb"] = np.where(d.data_unlimited, 300,
                                  (d.data_gb.fillna(d.daily_data_gb.fillna(0) * 30) + d.extra_gb).clip(0, 300))
    d["qos"] = d.data_throttle_speed.map(_qos_mbps)
    d["tether_gb"] = d.tethering_gb.fillna(0)
    d["voice_min"] = np.where(d.voice_unlimited, 1000, d.voice_minutes.fillna(0)).clip(0, 1000)
    ott = benefits[benefits.benefit_service.isin(price_map)]
    services = d.plan_id.map(ott.groupby("plan_id")["benefit_service"].apply(lambda v: sorted(set(v.dropna()))))
    d["ott_value"] = services.apply(lambda s: sum(price_map.get(x, 0) for x in s) if isinstance(s, list) else 0)

    y = np.log((d.subscriber_count / d.subscriber_count.sum()).clip(lower=1e-12).values)
    brand = pd.get_dummies(d.mvno_brand, prefix="brand", dummy_na=True).astype(float)
    X = pd.concat([d[list(AXES)].reset_index(drop=True), brand.reset_index(drop=True)], axis=1)
    for axis, fn in TRANSFORM.items():
        X[axis] = fn(X[axis].values)
    return X, y


def main() -> None:
    X, y = training_frame()
    model = make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(-3, 3, 25))).fit(X, y)
    coef = pd.Series(model[-1].coef_, index=X.columns)[list(AXES)]
    learned = coef.abs() / coef.abs().sum()

    shipped = json.loads((ROOT / "agent" / "weight_bootstrap.json").read_text(encoding="utf-8"))
    shipped_mean = pd.Series(np.mean(shipped["vectors"], axis=0), index=shipped["criteria"])

    axes = X[list(AXES)]
    spread = (axes.max() - axes.min()) / axes.std(ddof=0)      # 0~1 전체 폭이 표준편차의 몇 배인가
    preserving = learned * spread
    preserving /= preserving.sum()

    table = pd.DataFrame({
        "서비스 축": pd.Series(SERVICE_NAME),
        "재현한 가중치": learned.round(3),
        "서비스에 실린 가중치": [round(float(shipped_mean[SERVICE_NAME[a]]), 3) for a in AXES],
        "폭/표준편차": spread.round(2),
        "0~1 효용에서 같은 영향력을 내는 가중치": preserving.round(3),
    })
    print(f"학습 표본 {len(X):,}건 (고정 분석본, 알뜰폰·3사 직판 제외)")
    print(table.to_string())

    # 자기 점검: 재현한 가중치가 서비스에 실린 값과 같아야 뒤의 계산이 의미가 있다.
    for axis in AXES:
        assert abs(learned[axis] - shipped_mean[SERVICE_NAME[axis]]) < 0.01, axis
    print("\n재현 확인: 가중치가 서비스에 실린 값과 0.01 이내로 일치")

    # 서비스의 고정 눈금이 카탈로그를 덮는지. 새 상품이 범위를 넘으면 조용히 잘리지 않고 여기서 걸린다.
    from agent import mcda
    from agent.data import all_plans
    catalog = pd.DataFrame(all_plans())
    for column, top in (("qos_mbps", mcda._QOS_RANGE_MBPS), ("tethering_gb", mcda._TETHERING_RANGE_GB),
                        ("data_gb", mcda._DATA_RANGE_GB), ("discounted_fee", mcda._FEE_RANGE_WON)):
        highest = pd.to_numeric(catalog[column], errors="coerce").max()
        assert highest <= top, f"{column} 최댓값 {highest} 가 고정 범위 {top} 를 넘는다"
    print("고정 범위 확인: 속도·테더링·데이터·요금의 카탈로그 최댓값이 모두 범위 안")


if __name__ == "__main__":
    main()
