"""EDA — 회귀에 넣기 전, 요금제 스펙 축끼리 얼마나 겹치나.

왼쪽: 원자료 7축 상관 히트맵(전처리 전). 오른쪽: 상관이 높은 쌍을 산점도로 확인.
결정 두 개가 여기서 나온다.
  - 통화·문자 0.94 → 같은 묶음 상품. 문자는 회귀에서 뺀다(같이 넣으면 계수가 상쇄돼 부호가 뒤집힌다).
  - 데이터·소진 후 속도 0.80 → 등급 상품 구조. 회귀에는 둘 다 두되, 추천 우선순위에서 속도는 필터로 돌린다.

데이터: 가중치를 뽑은 고정 분석본(data/baseline/2026-08-21), 알뜰폰. 합성 가입이력과 무관한 카탈로그 성질.
실행: python -m experiments.plot_axis_correlation [--pieces DIR]
  → outputs/분석노트/EDA_축_상관.png (+ --pieces 면 DIR 에 히트맵·산점도를 투명 PNG 로 따로 저장)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

from experiments.plot_linear_spec import AXES, BLUE, GRAY, OUT as _OUT, VERM, build_xy

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import gridspec, rcParams  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

OUT = _OUT.parent / "EDA_축_상관.png"
LABEL = {
    "cost_month": "월 요금", "data_total_gb": "데이터 GB", "qos": "소진 후 속도",
    "tether_gb": "테더링", "voice_min": "통화 분", "sms_cnt": "문자 건", "ott_value": "OTT 혜택",
}
PAIRS = [("voice_min", "sms_cnt"), ("data_total_gb", "qos"), ("cost_month", "data_total_gb")]
CMAP = LinearSegmentedColormap.from_list("bw", ["#FFFFFF", "#C9DCEC", BLUE])


def heatmap(ax, corr: pd.DataFrame) -> None:
    n = len(corr)
    ax.imshow(np.abs(corr.values), cmap=CMAP, vmin=0, vmax=1)
    for i in range(n):
        for j in range(n):
            val = corr.values[i, j]
            ax.text(j, i, f"{val:.2f}", ha="center", va="center", fontsize=10.5,
                    color="white" if abs(val) > 0.6 else "#333",
                    fontweight="bold" if (i != j and abs(val) >= 0.7) else "normal")
    for a, b in PAIRS[:2]:
        i, j = AXES.index(a), AXES.index(b)
        for (r, c) in [(i, j), (j, i)]:
            ax.add_patch(plt.Rectangle((c - 0.5, r - 0.5), 1, 1, fill=False, ec=VERM, lw=2.5))
    ax.set_xticks(range(n), [LABEL[a] for a in AXES], rotation=30, ha="right", fontsize=10)
    ax.set_yticks(range(n), [LABEL[a] for a in AXES], fontsize=10)
    for sp in ax.spines.values():
        sp.set_visible(False)


def scatter(ax, A: pd.DataFrame, x: str, y: str, r: float) -> None:
    rng = np.random.default_rng(0)
    jx = A[x].values * (1 + rng.normal(0, 0.02, len(A)))
    jy = A[y].values * (1 + rng.normal(0, 0.02, len(A)))
    ax.scatter(jx, jy, s=9, color=BLUE, alpha=0.25, linewidths=0)
    ax.set_xlabel(LABEL[x], fontsize=10)
    ax.set_ylabel(LABEL[y], fontsize=10)
    ax.text(0.03, 0.93, f"r = {r:.2f}", transform=ax.transAxes, fontsize=12, fontweight="bold",
            color=VERM if r >= 0.7 else "#333")
    ax.grid(alpha=0.2)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pieces", type=Path, help="히트맵·산점도를 투명 PNG 로 따로 저장할 폴더")
    args = parser.parse_args()
    rcParams["font.family"] = "Malgun Gothic"
    rcParams["axes.unicode_minus"] = False
    X, _ = build_xy()
    A = X[AXES]
    corr = A.corr()

    fig = plt.figure(figsize=(15, 5.8))
    gs = gridspec.GridSpec(1, 4, width_ratios=[1.9, 1, 1, 1], wspace=0.45)
    ax = fig.add_subplot(gs[0, 0])
    heatmap(ax, corr)
    ax.set_title("요금제 스펙 축끼리 상관 (전처리 전, Pearson)", loc="left", fontsize=12, fontweight="bold")
    notes = [
        "통화·문자: 같은 묶음 상품\n→ 문자는 회귀에서 뺀다",
        "데이터·속도: 등급 상품 구조\n→ 회귀엔 두고, 추천에선 속도를 필터로",
        "가격·데이터: 비싸면 많다\n→ 그 틈의 가성비 상품이 추천 대상",
    ]
    for k, ((x, y), note) in enumerate(zip(PAIRS, notes)):
        sx = fig.add_subplot(gs[0, k + 1])
        scatter(sx, A, x, y, corr.loc[x, y])
        sx.set_title(f"{LABEL[x]} × {LABEL[y]}", loc="left", fontsize=10.5)
        sx.text(0, -0.2, note, transform=sx.transAxes, fontsize=10.5, va="top", color="#333", linespacing=1.5)
    fig.text(0.01, -0.12, f"고정 분석본 2026-08-21 · 알뜰폰 {len(A):,}건 · 요금제 스펙 상관(가입이력과 무관)",
             fontsize=8.5, color="#666")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=125, bbox_inches="tight")
    print(corr.round(2).to_string())
    print(OUT)

    if args.pieces:
        args.pieces.mkdir(parents=True, exist_ok=True)
        f, a = plt.subplots(figsize=(6.2, 5.6))
        heatmap(a, corr)
        f.tight_layout()
        f.savefig(args.pieces / "eda_heatmap.png", dpi=200, transparent=True)
        plt.close(f)
        for x, y in PAIRS:
            f, a = plt.subplots(figsize=(3.4, 3.2))
            scatter(a, A, x, y, corr.loc[x, y])
            f.tight_layout()
            f.savefig(args.pieces / f"eda_scatter_{x}_{y}.png", dpi=200, transparent=True)
            plt.close(f)
        print("pieces ->", args.pieces)


if __name__ == "__main__":
    main()
