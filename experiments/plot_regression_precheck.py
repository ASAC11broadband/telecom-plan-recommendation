"""회귀 돌리기 전 기본 점검 — 한 장 그림 (EDA 맨 앞).

  ① y 분포: 점유율 원자료는 한쪽으로 극단히 쏠림(왜도 9.6) → log 를 씌우면 종 모양(0.65). 그래서 y = log(점유율).
  ② x 분포: 6축 히스토그램. 왜도가 큰 가격·데이터는 뒤에서 √·log 변환, 0 이 대부분인 테더링·OTT 는 "있냐 없냐"에 가까움.
  ③ 결측·0 비율·왜도 표: 원본 결측은 0 으로 채웠다(속도 결측 = 소진 후 차단, 통화·문자 결측 = 미제공).

데이터: 가중치를 뽑은 고정 분석본(data/baseline/2026-08-21), 알뜰폰.
실행: python -m experiments.plot_regression_precheck [--pieces DIR]
  → outputs/분석노트/EDA_회귀전_점검.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

from experiments.plot_linear_spec import AXES, BASELINE, BLUE, GRAY, OUT as _OUT, VERM, build_xy

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import gridspec, rcParams  # noqa: E402

OUT = _OUT.parent / "EDA_회귀전_점검.png"
LABEL = {
    "cost_month": "월 요금 (원)", "data_total_gb": "데이터 (GB)", "qos": "소진 후 속도 (Mbps)",
    "tether_gb": "테더링 (GB)", "voice_min": "통화 (분, 무제한=1000)", "ott_value": "OTT 혜택 (원)",
}
RAW_COL = {  # 원본 CSV 컬럼 → 결측 비율을 보여주기 위해
    "cost_month": "discounted_fee", "data_total_gb": "data_gb", "qos": "data_throttle_speed",
    "tether_gb": "tethering_gb", "voice_min": "voice_minutes", "ott_value": None,
}
TREAT = {
    "cost_month": "√ 변환", "data_total_gb": "log 변환", "qos": "그대로 (0 = 소진 후 차단)",
    "tether_gb": "그대로 (0 = 미제공)", "voice_min": "그대로 (무제한 = 1000)", "ott_value": "그대로 (0 = 없음)",
}
SIX = [a for a in AXES if a != "sms_cnt"]


def load_raw_missing() -> pd.Series:
    p = pd.read_csv(BASELINE / "통신요금제_통합데이터_최종.csv", dtype={"plan_id": str})
    m = p[p.carrier_type.eq("MVNO") & ~p.mvno_brand.isin(["SKT", "KT", "LG U+", "LGU+"])]
    return pd.Series({a: (m[c].isna().mean() if c else 0.0) for a, c in RAW_COL.items()})


def hist(ax, values: np.ndarray, title: str, color=BLUE, bins=40, note: str | None = None, note_color=VERM):
    ax.hist(values, bins=bins, color=color, alpha=0.9)
    ax.set_title(title, loc="left", fontsize=10.5)
    ax.set_yticks([])
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    if note:
        ax.text(0.98, 0.9, note, transform=ax.transAxes, ha="right", va="top", fontsize=10,
                color=note_color, fontweight="bold")


def y_panels(ax_raw, ax_log, y: np.ndarray) -> None:
    share = np.exp(y)
    hist(ax_raw, share * 100, f"점유율 원자료 (%)   왜도 {pd.Series(share).skew():.1f}", color=GRAY,
         note="한쪽으로 쏠림")
    hist(ax_log, y, f"log(점유율)   왜도 {pd.Series(y).skew():.2f}", note="종 모양 → y 는 log 로", note_color=BLUE)


def x_panels(axes, A: pd.DataFrame) -> None:
    for ax, col in zip(axes, SIX):
        sk = A[col].skew()
        zero = (A[col] == 0).mean()
        tag = {"cost_month": "→ √", "data_total_gb": "→ log"}.get(col)
        short = LABEL[col].split(" (")[0]
        title = f"{short}\n왜도 {sk:.1f}" + (f" · 0 비율 {zero:.0%}" if zero > 0.2 else "")
        hist(ax, A[col].values, title, note=tag, note_color=VERM)


def table_panel(ax, A: pd.DataFrame, raw_missing: pd.Series) -> None:
    ax.axis("off")
    cols = ["축", "원본 결측", "0 비율", "왜도", "처리"]
    xs = [0.0, 0.28, 0.42, 0.54, 0.66]
    for x, c in zip(xs, cols):
        ax.text(x, 1.0, c, fontsize=10, color="#666", va="top", transform=ax.transAxes)
    for i, col in enumerate(SIX):
        row = [LABEL[col].split(" (")[0], f"{raw_missing[col]:.0%}", f"{(A[col] == 0).mean():.0%}",
               f"{A[col].skew():.1f}", TREAT[col]]
        strong = col in ("cost_month", "data_total_gb")
        for x, v in zip(xs, row):
            ax.text(x, 0.86 - i * 0.145, v, fontsize=10, va="top", transform=ax.transAxes,
                    color=BLUE if strong else "#333", fontweight="bold" if strong else "normal")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pieces", type=Path)
    args = parser.parse_args()
    rcParams["font.family"] = "Malgun Gothic"
    rcParams["axes.unicode_minus"] = False
    X, y = build_xy()
    A = X[SIX]
    raw_missing = load_raw_missing()

    fig = plt.figure(figsize=(15, 8.2))
    gs = gridspec.GridSpec(3, 6, height_ratios=[1, 1, 1.1], hspace=0.7, wspace=0.35)
    fig.text(0.125, 0.955, f"① 종속변수 y — 점유율은 그대로 쓰면 안 된다   (알뜰폰 {len(A):,}건, 결측 0, 0건 없음)",
             fontsize=12, fontweight="bold")
    y_panels(fig.add_subplot(gs[0, 0:2]), fig.add_subplot(gs[0, 2:4]), y)
    tx = fig.add_subplot(gs[0, 4:6])
    tx.axis("off")
    tx.text(0, 0.95, "점유율은 소수 요금제가 대부분을 가져가는 분포다.\n"
                     "그대로 회귀하면 그 몇 개가 직선을 끌고 간다.\n"
                     "log 를 씌우면 종 모양이 되고, 로짓 모형에서\n"
                     "log(점유율) = x'β 라 β 를 효용 가중치로 읽을 수 있다.",
            fontsize=10, va="top", color="#333", linespacing=1.6)
    fig.text(0.125, 0.66, "② 독립변수 x — 6축 분포 (문자는 앞 EDA 에서 제외)", fontsize=12, fontweight="bold")
    x_panels([fig.add_subplot(gs[1, k]) for k in range(6)], A)
    fig.text(0.125, 0.315, "③ 결측 · 0 비율 · 왜도 → 처리", fontsize=12, fontweight="bold")
    table_panel(fig.add_subplot(gs[2, 0:4]), A, raw_missing)
    nx = fig.add_subplot(gs[2, 4:6])
    nx.axis("off")
    nx.text(0, 0.95, "원본 결측은 뜻이 있는 결측이라 0 으로 채웠다.\n"
                     "· 속도 결측 = 소진 후 차단 (속도 0)\n"
                     "· 통화·문자 결측 = 미제공 (0분)\n"
                     "· 테더링 결측 = 미제공\n\n"
                     "왜도가 큰 가격·데이터만 뒤에서 변환한다.\n"
                     "테더링·OTT 는 0 이 대부분이라 '있냐 없냐'에 가깝다.",
            fontsize=10, va="top", color="#333", linespacing=1.6)
    fig.text(0.01, 0.005, "고정 분석본 2026-08-21 · 알뜰폰 · 점유율 = 합성 가입이력의 가입자 수 / 전체", fontsize=8.5, color="#666")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=125, bbox_inches="tight")
    print(OUT)

    if args.pieces:
        args.pieces.mkdir(parents=True, exist_ok=True)
        f, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 2.6))
        y_panels(a1, a2, y)
        f.tight_layout()
        f.savefig(args.pieces / "pre_y_dist.png", dpi=200, transparent=True)
        plt.close(f)
        f, axes = plt.subplots(1, 6, figsize=(14, 2.4))
        x_panels(axes, A)
        f.tight_layout()
        f.savefig(args.pieces / "pre_x_dist.png", dpi=200, transparent=True)
        plt.close(f)
        print("pieces ->", args.pieces)


if __name__ == "__main__":
    main()
