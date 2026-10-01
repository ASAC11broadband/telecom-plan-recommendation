"""√가격·log 데이터를 고른 근거를 구간별로 자세히 본다 (회귀 전처리 장의 뒷받침).

부분잔차(다른 축 효과를 뺀 뒤 그 축만의 기여)를 요금·데이터 구간별로 평균 내고, 구간 사이 기울기를 적는다.
  가격:  0~5천원(프로모션 구간)에서 1만원당 -3.7, 5천원 넘으면 1만원당 -0.6 ~ -1.5 로 일정.
         → 싼 쪽이 튀고 그 뒤는 완만. √ 가 싼 구간을 늘리고 비싼 구간을 줄여 편다.
  데이터: 20GB 까지 10GB당 +0.9 ~ +1.0, 그 뒤 10GB당 +0.05 ~ +0.13.
         → 두 배 늘 때마다 비슷하게 오른다 = log 모양. "150GB 넘으면 평평"보다 "20GB 넘으면 완만"이 정확하다.

데이터: 가중치를 뽑은 고정 분석본(data/baseline/2026-08-21), 6축(문자 제외), 브랜드 통제.
실행: python -m experiments.plot_transform_detail [--pieces DIR]  → outputs/분석노트/회귀_변환_근거_상세.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

from experiments.plot_linear_spec import BLUE, GRAY, OUT as _OUT, VERM, build_xy, ridge
from experiments.plot_regression_steps import steps

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import rcParams  # noqa: E402

OUT = _OUT.parent / "회귀_변환_근거_상세.png"
PRICE_BINS = ([-1, 2500, 5000, 10000, 15000, 20000, 30000, 40000, 60000],
              ["~2.5천", "~5천", "~1만", "~1.5만", "~2만", "~3만", "~4만", "4만~"])
DATA_BINS = ([-1, 5, 10, 20, 40, 70, 100, 150, 200, 300],
             ["~5", "~10", "~20", "~40", "~70", "~100", "~150", "~200", "200~"])


def band_table(model, X: pd.DataFrame, y: np.ndarray, col: str, bins, labels, unit: float) -> pd.DataFrame:
    """구간별 부분잔차 평균과 이웃 구간 사이 기울기(unit 당)."""
    scaler, reg = model[0], model[-1]
    j = list(X.columns).index(col)
    beta = reg.coef_[j] / scaler.scale_[j]
    x = X[col].values
    comp = beta * (x - x.mean()) + (y - model.predict(X))
    d = pd.DataFrame({"x": x, "comp": comp, "b": pd.cut(x, bins, labels=labels)})
    g = d.groupby("b", observed=True).agg(n=("x", "size"), x_mid=("x", "median"), comp=("comp", "mean"))
    slope = [np.nan] + [
        (g.comp.iloc[i] - g.comp.iloc[i - 1]) / (g.x_mid.iloc[i] - g.x_mid.iloc[i - 1]) * unit
        for i in range(1, len(g))
    ]
    g["slope"] = slope
    return g


def panel(ax, g: pd.DataFrame, fitted_x, fitted_y, xlabel: str, unit_label: str, split: float, note_left: str, note_right: str):
    ax.plot(fitted_x, fitted_y, color=BLUE, lw=2, label="변환 후 직선(원 눈금으로 되돌린 곡선)")
    ax.scatter(g.x_mid, g.comp, s=60, color=GRAY, zorder=3, label="구간 평균 부분잔차")
    for i in range(1, len(g)):
        xm = (g.x_mid.iloc[i] + g.x_mid.iloc[i - 1]) / 2
        ym = (g.comp.iloc[i] + g.comp.iloc[i - 1]) / 2
        steep = abs(g.slope.iloc[i]) >= 2 * np.nanmedian(np.abs(g.slope.values[1:]))
        ax.annotate(f"{g.slope.iloc[i]:+.2f}", (xm, ym), textcoords="offset points",
                    xytext=(12, 10) if i % 2 else (-12, -14),
                    ha="center", fontsize=8.5, color=VERM if steep else "#666", fontweight="bold" if steep else "normal")
    ax.axvline(split, color=VERM, ls=":", lw=1.2)
    ax.text(0.02, 0.06, note_left, transform=ax.transAxes, fontsize=10, color=VERM, fontweight="bold")
    ax.text(0.98, 0.06, note_right, transform=ax.transAxes, fontsize=10, color="#555", ha="right")
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel("그 축만의 기여 (부분잔차, log 점유율)", fontsize=9.5)
    ax.text(0.98, 0.95, f"숫자 = 이웃 구간 사이 기울기 ({unit_label})", transform=ax.transAxes, ha="right", va="top", fontsize=9, color="#666")
    ax.grid(alpha=0.2)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pieces", type=Path)
    args = parser.parse_args()
    rcParams["font.family"] = "Malgun Gothic"
    rcParams["axes.unicode_minus"] = False
    X, y = build_xy()
    st = steps(X)
    raw_X, t_X = st[0][2], st[2][2]
    m_raw, m_t = ridge().fit(raw_X, y), ridge().fit(t_X, y)

    gp = band_table(m_raw, raw_X, y, "cost_month", *PRICE_BINS, unit=10_000)
    gd = band_table(m_raw, raw_X, y, "data_total_gb", *DATA_BINS, unit=10)

    # 변환 후 직선을 원 눈금으로 되돌린 곡선 (부분잔차 평균과 같은 중심으로 맞춘다)
    def fitted(model, Xs, col, transform, grid):
        scaler, reg = model[0], model[-1]
        j = list(Xs.columns).index(col)
        beta = reg.coef_[j] / scaler.scale_[j]
        tx = transform(grid)
        return beta * (tx - Xs[col].values.mean())

    px = np.linspace(100, 57_000, 200)
    py = fitted(m_t, t_X, "cost_month", np.sqrt, px)
    py += gp.comp.mean() - np.interp(gp.x_mid, px, py).mean()
    dx = np.linspace(0.5, 250, 200)
    dy = fitted(m_t, t_X, "data_total_gb", np.log1p, dx)
    dy += gd.comp.mean() - np.interp(gd.x_mid, dx, dy).mean()

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 5.2), gridspec_kw={"wspace": 0.3})
    panel(a1, gp, px, py, "월 요금 (원)", "1만원당", 5000,
          "5천원 아래: 1만원당 -3.7", "5천원 위: 1만원당 -0.6 ~ -1.5 로 일정")
    a1.set_title("가격 — 싼 구간(프로모션)이 튀고 그 뒤는 완만 → √", loc="left", fontsize=11.5, fontweight="bold")
    panel(a2, gd, dx, dy, "기본 데이터 (GB)", "10GB당", 20,
          "20GB 아래: 10GB당 +0.9 ~ +1.0", "20GB 위: 10GB당 +0.05 ~ +0.13")
    a2.set_title("데이터 — 20GB 까지 급하고 그 뒤 완만, 두 배마다 비슷하게 ↑ → log", loc="left", fontsize=11.5, fontweight="bold")
    a1.legend(fontsize=9, frameon=False, loc="upper right", bbox_to_anchor=(1, 0.9))
    fig.text(0.01, -0.02, f"고정 분석본 2026-08-21 · 알뜰폰 {len(y):,}건 · 6축(문자 제외)+브랜드 통제 · 회색 점 = 원자료 Ridge 의 부분잔차 구간 평균",
             fontsize=8.5, color="#666")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=125, bbox_inches="tight")
    print(gp.round(3).to_string())
    print(gd.round(3).to_string())
    print(OUT)

    if args.pieces:
        args.pieces.mkdir(parents=True, exist_ok=True)
        for name, g, fx, fy, xl, ul, sp, nl, nr in [
            ("detail_price", gp, px, py, "월 요금 (원)", "1만원당", 5000, "5천원 아래: 1만원당 -3.7", "5천원 위: -0.6 ~ -1.5 로 일정"),
            ("detail_data", gd, dx, dy, "기본 데이터 (GB)", "10GB당", 20, "20GB 아래: 10GB당 +0.9 ~ +1.0", "20GB 위: +0.05 ~ +0.13"),
        ]:
            f, a = plt.subplots(figsize=(6.4, 4.4))
            panel(a, g, fx, fy, xl, ul, sp, nl, nr)
            f.tight_layout()
            f.savefig(args.pieces / f"{name}.png", dpi=200, transparent=True)
            plt.close(f)
        print("pieces ->", args.pieces)


if __name__ == "__main__":
    main()
