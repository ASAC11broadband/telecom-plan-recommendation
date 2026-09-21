# -*- coding: utf-8 -*-
"""세그먼트·KNN 학습용 합성 가입 이력을 현행 요금제 카탈로그로 만든다.

1차 합성은 2026-08-12 스냅샷에서 만들어져 정답 요금제의 20%가 현행 카탈로그에 없었다.
그 상태로는 세그먼트·KNN 이 지금 팔지도 않는 요금제를 추천해서, 코사인·MCDA 와 같은
후보군에 올려 비교할 수가 없다. 그래서 카탈로그를 바꿔 다시 만든다.

**생성 규칙은 1차와 같다.** 규칙은 1차 파일을 역산해 복원한 것이다:

    요금제 추출   subscriber_count 비례
    무제한 요구   요금제의 무제한 여부를 그대로 (요구 True -> 공급 True 100%)
    필요량        제공량 x U(0.8, 1.0). 무제한 축은 빈 값
    예산          = 현재 납부액 = 요금제의 discounted_fee
    fee_group     요금 3분위
    ott_want      요금제에 OTT 가 있으면 70% 확률로 그 서비스명
    ott_required  ott_want 가 있는 사람 중 29%
    age/gender    요금제와 무관 -> 아래 AGE_COUNTS/GENDER_FEMALE_RATE 경험분포

생성식을 손대지 않은 것은 의도한 것이다. 이 규칙은 **요금제에서 사용자를 역산**하므로
정답이 입력에 거의 그대로 들어있다 - 규칙을 뒤집으면 Hit@5 0.905 가 나오고, 이웃 KNN 의
0.83 은 그 상한의 92% 다. 즉 이 데이터의 Hit@5 는 추천 품질이 아니라 생성 규칙 복원도를
잰다. 누수를 줄이려 상수를 바꿔봐야 대체할 값에 근거가 없고, 애초에 이 데이터를 절대
성능 근거가 아니라 세그먼트 인기 vs 이웃 KNN 의 **상대 비교**에만 쓴다.

    python -m experiments.make_synthetic_customers
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PLANS_CSV = ROOT / "data" / "통신요금제_통합데이터_최종.csv"
OUTPUT = ROOT / "data" / "synthetic_original" / "customers_mvno.csv"

# 나이·성별은 요금제와 무관해 1차 합성의 경험분포를 그대로 옮겼다. 1차 파일 자체는
# 지웠으므로(카탈로그가 어긋나 쓸 수 없었다) 분포만 여기 남긴다. 20세부터 1세 단위.
AGE_MIN = 20
AGE_COUNTS = (202, 240, 202, 199, 209, 227, 225, 234, 209, 230,
              873, 865, 843, 897, 886, 814, 850, 848, 867, 823,
              1013, 1033, 1029, 1046, 1032, 1059, 1040, 1029, 1020, 985,
              1099, 1143, 1136, 1123, 1081, 1165, 1143, 1169, 1092, 1114,
              793, 781, 740, 755, 791, 749, 773, 766, 785, 773)
GENDER_FEMALE_RATE = 0.599

COLUMNS = ["customer_id", "age", "gender", "data_gb_month", "data_unlimited_need",
           "voice_unlimited_need", "sms_unlimited_need", "voice_minutes_need",
           "sms_count_need", "carrier_type", "current_carrier", "mvno_ok",
           "current_fee_krw", "budget_krw", "ott_want", "ott_required",
           "source_plan_id", "fee_group"]

NEED_RATIO = (0.8, 1.0)   # 필요량 = 제공량 x U(lo, hi). 1차 파일 분위에서 복원한 값
OTT_WANT_RATE = 0.701     # 요금제에 OTT 가 있을 때 ott_want 를 채우는 비율
OTT_REQUIRED_RATE = 0.293  # ott_want 가 있는 사람 중 ott_required=True 비율


def _flag(series: pd.Series) -> np.ndarray:
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"}).to_numpy()


def eligible_plans(plans_path: Path = PLANS_CSV) -> pd.DataFrame:
    """가입자 수가 있는 알뜰폰 요금제만. 가입자 0 인 상품은 1차에서도 아무도 안 골랐다."""
    plans = pd.read_csv(plans_path, dtype={"plan_id": str})
    plans = plans[plans["carrier_type"].eq("MVNO")].dropna(subset=["plan_id", "plan_name"])
    plans = plans.drop_duplicates("plan_id").copy()
    plans["subs"] = pd.to_numeric(plans["subscriber_count"], errors="coerce").fillna(0)
    plans = plans[plans["subs"] > 0].reset_index(drop=True)

    plans["fee"] = pd.to_numeric(plans["discounted_fee"], errors="coerce")
    plans["fee"] = plans["fee"].fillna(pd.to_numeric(plans["monthly_fee"], errors="coerce")).fillna(0)
    daily = pd.to_numeric(plans.get("daily_data_gb"), errors="coerce").fillna(0) * 30
    plans["data_gb_supply"] = pd.to_numeric(plans["data_gb"], errors="coerce").fillna(daily)
    plans["voice_supply"] = pd.to_numeric(plans["voice_minutes"], errors="coerce").fillna(0)
    plans["sms_supply"] = pd.to_numeric(plans["sms_count"], errors="coerce").fillna(0)
    plans["data_unl"] = _flag(plans["data_unlimited"])
    plans["voice_unl"] = _flag(plans["voice_unlimited"])
    plans["sms_unl"] = _flag(plans["sms_unlimited"])
    plans["ott"] = plans["ott_options"].fillna("").astype(str).str.strip()
    return plans


def generate(n: int = 40_000, seed: int = 42, plans_path: Path = PLANS_CSV) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    plans = eligible_plans(plans_path)

    weights = plans["subs"].to_numpy(dtype=float)
    picked = plans.iloc[rng.choice(len(plans), n, p=weights / weights.sum())].reset_index(drop=True)

    def need(supply: np.ndarray, unlimited: np.ndarray, decimals: int) -> np.ndarray:
        """무제한 축은 빈 값으로 둔다. 1차 파일에서 무제한 요구자의 필요량은 전부 결측이다."""
        drawn = np.round(supply * rng.uniform(*NEED_RATIO, size=len(supply)), decimals)
        return np.where(unlimited, np.nan, drawn)

    ages = np.arange(AGE_MIN, AGE_MIN + len(AGE_COUNTS))
    age = rng.choice(ages, n, p=np.array(AGE_COUNTS, dtype=float) / sum(AGE_COUNTS))
    gender = np.where(rng.random(n) < GENDER_FEMALE_RATE, "여", "남")

    has_ott = picked["ott"].ne("").to_numpy()
    want = has_ott & (rng.random(n) < OTT_WANT_RATE)
    fee = picked["fee"].to_numpy()
    low, high = np.quantile(fee, [1 / 3, 2 / 3])

    return pd.DataFrame({
        "customer_id": [f"M{i:06d}" for i in range(n)],
        "age": age,
        "gender": gender,
        "data_gb_month": need(picked["data_gb_supply"].to_numpy(), picked["data_unl"].to_numpy(), 1),
        "data_unlimited_need": picked["data_unl"].to_numpy(),
        "voice_unlimited_need": picked["voice_unl"].to_numpy(),
        "sms_unlimited_need": picked["sms_unl"].to_numpy(),
        "voice_minutes_need": need(picked["voice_supply"].to_numpy(), picked["voice_unl"].to_numpy(), 0),
        "sms_count_need": need(picked["sms_supply"].to_numpy(), picked["sms_unl"].to_numpy(), 0),
        "carrier_type": "MVNO",
        "current_carrier": "알뜰폰",
        "mvno_ok": True,
        "current_fee_krw": fee.astype(int),
        "budget_krw": fee.astype(int),
        "ott_want": np.where(want, picked["ott"].to_numpy(), ""),
        "ott_required": want & (rng.random(n) < OTT_REQUIRED_RATE),
        "source_plan_id": picked["plan_id"].to_numpy(),
        "fee_group": np.where(fee <= low, "저", np.where(fee <= high, "중", "고")),
    })[COLUMNS]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=40_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=OUTPUT)
    args = ap.parse_args()

    frame = generate(args.rows, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out, index=False, encoding="utf-8-sig")

    plans = eligible_plans()
    print(f"{len(frame):,}명 · 요금제 {frame.source_plan_id.nunique():,}종 / 후보 {len(plans):,}종")
    print(f"예산 중위 {frame.budget_krw.median():,.0f}원 · 데이터 필요 중위 {frame.data_gb_month.median():.1f}GB")
    print(f"무제한 요구  데이터 {frame.data_unlimited_need.mean():.3f}"
          f" · 통화 {frame.voice_unlimited_need.mean():.3f}"
          f" · SMS {frame.sms_unlimited_need.mean():.3f}")
    print(f"저장: {args.out}")


def _self_check() -> None:
    """생성 규칙이 1차 파일과 같은 관계를 재현하는지. 값이 아니라 규칙을 본다."""
    frame = generate(3_000, seed=0)
    plans = eligible_plans().set_index("plan_id")
    picked = plans.loc[frame.source_plan_id]

    assert (frame.data_unlimited_need.to_numpy() == picked["data_unl"].to_numpy()).all(), "무제한 요구는 공급을 따라간다"
    assert frame.data_gb_month[frame.data_unlimited_need].isna().all(), "무제한 축의 필요량은 빈 값"
    assert (frame.budget_krw.to_numpy() == picked["fee"].to_numpy().astype(int)).all(), "예산 = 요금"
    assert (frame.current_fee_krw == frame.budget_krw).all(), "현재 납부액 = 예산"

    # 소수점 반올림 탓에 제공량이 아주 작은 요금제(0.5GB 등)는 비율이 경계를 살짝 넘는다.
    # 1차 파일도 같은 이유로 최소 0.615 였다. 그래서 꼬리 1% 는 빼고 본다.
    finite = ~frame.data_unlimited_need.to_numpy() & (picked["data_gb_supply"].to_numpy() > 0)
    ratio = frame.data_gb_month.to_numpy()[finite] / picked["data_gb_supply"].to_numpy()[finite]
    lo, hi = np.percentile(ratio, [1, 99])
    assert NEED_RATIO[0] - 0.01 <= lo and hi <= NEED_RATIO[1] + 0.01, "필요량 비율 범위"
    assert abs(np.median(ratio) - sum(NEED_RATIO) / 2) < 0.02, "필요량 비율 중앙값"

    assert frame.ott_want[frame.ott_want.ne("")].notna().all(), "ott_want 는 요금제의 OTT 명"
    assert not frame.ott_required[frame.ott_want.eq("")].any(), "OTT 를 원하지 않으면 필수일 수 없다"
    assert frame.source_plan_id.isin(plans.index).all(), "정답 요금제는 전부 현행 카탈로그 안"
    print("self-check ok: 합성 생성 규칙 4종 + 범위 + OTT 정합")


if __name__ == "__main__":
    _self_check()
    main()
