"""EDA — 상관이 높은 쌍을 하나씩 표로 확인한다 (히트맵 다음 장).

변수 종류에 따라 보는 법이 다르다.
  이진 × 이진        통화 무제한 × 문자 무제한  → 2×2 교차표. 일치율이 곧 "같은 상품이냐".
  연속 × 값 몇 개    데이터 GB × 소진 후 속도    → 데이터 구간 × 속도 값 교차표. 블록 대각선이면 등급 상품 구조.
  연속 × 연속        월 요금 × 데이터 GB         → 요금 구간별 데이터 중위값·100GB 이상 비율. 상관은 있지만 구간 안 분산이 크다.

데이터: 가중치를 뽑은 고정 분석본(data/baseline/2026-08-21), 알뜰폰.
실행: python -m experiments.plot_pair_tables [--pieces DIR]
  → outputs/분석노트/EDA_쌍별_교차표.png (+ --pieces 면 DIR 에 표 세 장을 투명 PNG 로 따로 저장)
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

from experiments.plot_linear_spec import BASELINE, BLUE, OUT as _OUT, VERM

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import rcParams  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

OUT = _OUT.parent / "EDA_쌍별_교차표.png"
CMAP = LinearSegmentedColormap.from_list("bw", ["#FFFFFF", "#C9DCEC", BLUE])
DATA_BINS = ([-1, 0, 10, 40, 70, 99, 149, 299, 1e9], ["0", "~10", "~40", "~70", "~99", "100~149", "150~299", "무제한"])
FEE_BINS = ([-1, 10_000, 20_000, 30_000, 40_000, 1e9], ["~1만", "1~2만", "2~3만", "3~4만", "4만~"])


def _qos(value) -> float:
    if pd.isna(value):
        return 0.0
    m = re.match(r"([\d.]+)\s*(kbps|mbps)", str(value).strip().lower().replace(" ", ""))
    if not m:
        return 0.0
    return float(m.group(1)) / 1000 if m.group(2) == "kbps" else float(m.group(1))


def load() -> pd.DataFrame:
    p = pd.read_csv(BASELINE / "통신요금제_통합데이터_최종.csv", dtype={"plan_id": str})
    m = p[p.carrier_type.eq("MVNO") & ~p.mvno_brand.isin(["SKT", "KT", "LG U+", "LGU+"])].copy()
    m["voice_unl"] = m.voice_unlimited.fillna(False).astype(bool)
    m["sms_unl"] = m.sms_unlimited.fillna(False).astype(bool)
    m["qos"] = m.data_throttle_speed.map(_qos)
    m["data"] = np.where(m.data_unlimited.fillna(False), 300, m.data_gb.fillna(m.daily_data_gb.fillna(0) * 30))
    m["data_bin"] = pd.cut(m.data, bins=DATA_BINS[0], labels=DATA_BINS[1])
    m["fee_bin"] = pd.cut(m.discounted_fee, bins=FEE_BINS[0], labels=FEE_BINS[1])
    return m


def _count_heatmap(ax, table: pd.DataFrame, xlabel: str, ylabel: str, highlight=None) -> None:
    vals = table.values.astype(float)
    ax.imshow(vals / vals.max(), cmap=CMAP, vmin=0, vmax=1)
    for i in range(vals.shape[0]):
        for j in range(vals.shape[1]):
            v = int(vals[i, j])
            ax.text(j, i, f"{v:,}" if v else "·", ha="center", va="center", fontsize=10,
                    color="white" if vals[i, j] / vals.max() > 0.55 else "#333",
                    fontweight="bold" if v else "normal")
    ax.set_xticks(range(vals.shape[1]), [str(c) for c in table.columns], fontsize=10)
    ax.set_yticks(range(vals.shape[0]), [str(r) for r in table.index], fontsize=10)
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel(ylabel, fontsize=10)
    for sp in ax.spines.values():
        sp.set_visible(False)
    if highlight:
        for (i, j) in highlight:
            ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False, ec=VERM, lw=2.5))


def panel_voice_sms(ax, m: pd.DataFrame) -> str:
    t = pd.crosstab(m.voice_unl, m.sms_unl).reindex(index=[False, True], columns=[False, True]).fillna(0)
    t.index = ["통화 무제한 아니오", "통화 무제한 예"]
    t.columns = ["문자 아니오", "문자 예"]
    _count_heatmap(ax, t, "", "", highlight=[(0, 0), (1, 1)])
    agree = (t.values[0, 0] + t.values[1, 1]) / t.values.sum()
    return f"일치율 {agree:.1%} → 사실상 한 축"


def panel_data_qos(ax, m: pd.DataFrame) -> str:
    t = pd.crosstab(m.data_bin, m.qos.round(1))
    t = t.loc[[r for r in t.index if t.loc[r].sum() > 0]]
    t.columns = [f"{c:g}" if c else "0" for c in t.columns]
    _count_heatmap(ax, t, "소진 후 속도 (Mbps)", "기본 데이터 (GB)")
    return "구간마다 속도가 정해져 있음 → 등급 상품 구조"


def panel_fee_data(ax, m: pd.DataFrame) -> str:
    g = m.groupby("fee_bin", observed=True).data.agg(median="median", big=lambda s: (s >= 100).mean(), n="count")
    x = np.arange(len(g))
    ax.bar(x, g["median"], color=BLUE, width=0.55)
    for i, (med, big) in enumerate(zip(g["median"], g["big"])):
        ax.text(i, med + 3, f"{med:.0f}GB", ha="center", va="bottom", fontsize=10.5, fontweight="bold")
        ax.text(i, med * 0.5 if med > 25 else med + 14, f"{big:.0%}", ha="center", va="center",
                fontsize=8.5, color="white" if med > 25 else "#555")
    ax.set_xticks(x, [f"{b}\nn={n:,}" for b, n in zip(g.index, g["n"])], fontsize=9)
    ax.set_ylim(0, g["median"].max() * 1.3)
    ax.set_ylabel("데이터 중위값 (GB)", fontsize=10)
    ax.set_xlabel("월 요금 구간   (막대 안 % = 100GB 이상 비율)", fontsize=9.5)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    return "비쌀수록 많지만 같은 구간 안 편차가 큼 → 가성비 틈이 있음"


PANELS = [
    ("통화 무제한 × 문자 무제한  (이진 × 이진 → 2×2 교차표)", panel_voice_sms, (4.2, 3.4)),
    ("데이터 GB × 소진 후 속도  (연속 × 값 몇 개 → 구간 교차표)", panel_data_qos, (5.2, 3.6)),
    ("월 요금 × 데이터 GB  (연속 × 연속 → 구간별 요약)", panel_fee_data, (4.2, 3.6)),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pieces", type=Path)
    args = parser.parse_args()
    rcParams["font.family"] = "Malgun Gothic"
    rcParams["axes.unicode_minus"] = False
    m = load()

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.9), gridspec_kw={"width_ratios": [1, 1.25, 1], "wspace": 0.55})
    for ax, (title, fn, _) in zip(axes, PANELS):
        verdict = fn(ax, m)
        ax.set_title(title, loc="left", fontsize=10.5, fontweight="bold")
        ax.text(0, -0.32, verdict, transform=ax.transAxes, fontsize=10.5, color=VERM, fontweight="bold", va="top")
    fig.text(0.01, -0.06, f"고정 분석본 2026-08-21 · 알뜰폰 {len(m):,}건 · 요금제 스펙(가입이력과 무관)",
             fontsize=8.5, color="#666")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=125, bbox_inches="tight")
    print(OUT)

    if args.pieces:
        args.pieces.mkdir(parents=True, exist_ok=True)
        for k, (_, fn, size) in enumerate(PANELS, 1):
            f, a = plt.subplots(figsize=size)
            fn(a, m)
            f.tight_layout()
            f.savefig(args.pieces / f"pair_{k}.png", dpi=200, transparent=True)
            plt.close(f)
        print("pieces ->", args.pieces)


if __name__ == "__main__":
    main()
