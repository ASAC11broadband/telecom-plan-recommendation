"""회귀 — 전처리(변환)마다 R² 가 어떻게 오르나, 한 장 그림 (발표용, 트리는 참고선만).

  위:   원자료 → √가격 → log 데이터, 단계마다 Ridge 교차검증 R².
        맨 오른쪽 점선은 같은 변수로 튜닝한 비선형 모델(GBM)의 R². "이 변수로 갈 수 있는 위쪽".
  아래: 가격·데이터의 부분잔차 그림. 회색 점 = 실제 데이터의 구간 평균, 파랑 = Ridge 직선.
        점이 직선을 따라가면 그 축은 직선으로 봐도 된다.

축은 가중치에 들어가는 6개(가격·데이터·속도·테더링·통화·OTT). 문자는 EDA 에서 뺐다(plot_axis_correlation).
브랜드 더미는 노트북과 같이 통제변수로 들어 있지만 가중치 축이 아니라 그림에는 단계로 안 보인다.
데이터는 가중치를 뽑은 고정 분석본(data/baseline/2026-08-21).
실행: python -m experiments.plot_regression_steps [--pieces DIR]
  → outputs/분석노트/회귀_전처리_단계.png (+ --pieces 면 DIR 에 막대·잔차 그림을 투명 PNG 로 따로 저장)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from sklearn.metrics import r2_score

from experiments.plot_linear_spec import (
    BLUE, CV, GRAY, GREEN, VERM, OUT as _OUT, apply_spec, build_xy, ridge,
)

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import gridspec, rcParams  # noqa: E402

OUT = _OUT.parent / "회귀_전처리_단계.png"
# 같은 변수·같은 고정본에서 깊이 무제한 GBM 을 랜덤서치 30회로 튜닝한 값(5-fold). 참고선.
TUNED_TREE_R2 = 0.616
SQRT_TICKS = (np.array([0, 2_500, 10_000, 22_500, 40_000]), ["0", "2.5천", "1만", "2.25만", "4만"])
LOG_TICKS = (np.array([0, 10, 50, 100, 300]), ["0", "10", "50", "100", "300"])


def cv_r2(X: pd.DataFrame, y: np.ndarray) -> float:
    return float(np.mean([
        r2_score(y[te], ridge().fit(X.iloc[tr], y[tr]).predict(X.iloc[te])) for tr, te in CV.split(X)
    ]))


def steps(X: pd.DataFrame) -> list[tuple[str, str, pd.DataFrame]]:
    """(단계 이름, 무엇을 했나, 그 단계의 X). 문자 열은 뺀다."""
    base = X.drop(columns=["sms_cnt"])
    sqrt_cost = apply_spec(base, {"cost_month": "sqrt"})
    log_data = apply_spec(base, {"cost_month": "sqrt", "data_total_gb": "log1p"})
    return [
        ("① 원자료", "요금제 스펙 그대로", base),
        ("② √가격", "싼 구간(프로모션)이 튀고 그 뒤 완만\n→ √로 편다", sqrt_cost),
        ("③ log 데이터", "20GB 까지 급하고 그 뒤 완만\n(두 배마다 비슷하게 ↑) → log 로 편다", log_data),
    ]


def partial_residual(model, X: pd.DataFrame, col: str, y: np.ndarray, q: int = 15):
    """부분잔차 = β·x + 잔차. 구간 평균(회색 점)과 직선(파랑)을 돌려준다."""
    scaler, reg = model[0], model[-1]
    j = list(X.columns).index(col)
    beta = reg.coef_[j] / scaler.scale_[j]  # 원 눈금 기울기
    x = X[col].values
    comp = beta * (x - x.mean()) + (y - model.predict(X))
    b = pd.qcut(pd.Series(x).rank(method="first"), q=q, duplicates="drop")
    g = pd.DataFrame({"x": x, "c": comp, "b": b}).groupby("b", observed=True).mean()
    xs = np.linspace(x.min(), x.max(), 100)
    return g.x.values, g.c.values, xs, beta * (xs - x.mean())


def bars(ax, stage, r2: list[float], with_desc: bool) -> None:
    xs = np.arange(len(stage))
    ax.bar(xs, r2, color=[GRAY, BLUE, BLUE], width=0.5, alpha=0.9)
    for x, v, prev in zip(xs, r2, [None] + r2[:-1]):
        ax.text(x, v + 0.006, f"{v:.3f}", ha="center", fontsize=12, fontweight="bold")
        if prev is not None:
            ax.text(x, v + 0.03, f"{v - prev:+.3f}", ha="center", fontsize=10.5, color=GREEN)
    ax.set_xticks(xs, [s[0] for s in stage], fontsize=11)
    ax.set_ylim(0.3, 0.68)
    ax.set_ylabel("교차검증 R² (5×5 fold)")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.axhline(TUNED_TREE_R2, color=VERM, ls="--", lw=1.4)
    ax.text(len(stage) - 0.6, TUNED_TREE_R2 + 0.01,
            f"참고: 같은 변수로 튜닝한 비선형 모델(GBM) {TUNED_TREE_R2:.2f}\n"
            "직선으로 갈 수 있는 위쪽 경계. 나머지는 문턱·조합 효과라 필터가 담당",
            ha="right", va="bottom", fontsize=9.5, color=VERM)
    if with_desc:
        for x, s in zip(xs, stage):
            ax.text(x, -0.14, s[1], ha="center", va="top", fontsize=9, color="#555",
                    transform=ax.get_xaxis_transform())


def residual_panel(a, model, X: pd.DataFrame, col: str, y: np.ndarray, ticks, xlabel: str) -> None:
    bx, bc, lx, ly = partial_residual(model, X, col, y)
    a.plot(lx, ly, color=BLUE, lw=2.2)
    a.scatter(bx, bc, color=GRAY, s=42, zorder=3)
    if ticks is not None:
        vals, labels = ticks
        f = np.sqrt if "√" in xlabel else np.log1p
        a.set_xticks(f(vals), labels)
    a.set_xlabel(xlabel, fontsize=9)
    a.set_yticks([])
    a.grid(alpha=0.2)
    for sp in ("top", "right", "left"):
        a.spines[sp].set_visible(False)


def panels(stage):
    raw_X, t_X = stage[0][2], stage[2][2]
    return [
        ("pr_price_raw", raw_X, "cost_month", None, "원", "① 가격 · 원자료", "휘어 있음", VERM),
        ("pr_price_sqrt", t_X, "cost_month", SQRT_TICKS, "원 (√눈금)", "② 가격 · √ 변환", "직선을 따라감", BLUE),
        ("pr_data_raw", raw_X, "data_total_gb", None, "GB", "③ 데이터 · 원자료", "휘어 있음", VERM),
        ("pr_data_log", t_X, "data_total_gb", LOG_TICKS, "GB (log눈금)", "④ 데이터 · log 변환", "직선을 따라감", BLUE),
    ]


def draw(stage, r2: list[float], y: np.ndarray, pieces: Path | None) -> None:
    rcParams["font.family"] = "Malgun Gothic"
    rcParams["axes.unicode_minus"] = False
    models = {id(s[2]): ridge().fit(s[2], y) for s in stage}

    fig = plt.figure(figsize=(15, 8.6))
    gs = gridspec.GridSpec(2, 4, height_ratios=[1.05, 1], hspace=0.6, wspace=0.35)
    ax = fig.add_subplot(gs[0, :])
    bars(ax, stage, r2, with_desc=True)
    ax.set_title("회귀 전처리 단계별 R² — 무엇을 했더니 얼마나 올랐나", loc="left", fontsize=12, fontweight="bold")
    for i, (_, X, col, ticks, xl, title, note, color) in enumerate(panels(stage)):
        a = fig.add_subplot(gs[1, i])
        residual_panel(a, models[id(X)], X, col, y, ticks, xl)
        a.set_title(title, fontsize=10.5, loc="left")
        a.text(0.02, 0.04, note, transform=a.transAxes, fontsize=10, color=color, fontweight="bold")
    fig.text(0.5, 0.435, "회색 점 = 실제 데이터의 구간 평균(부분잔차, 15분위)   파랑 = Ridge 직선   점이 직선을 따라가야 '기울기 = 가중치'가 성립",
             ha="center", fontsize=10, color="#333")
    fig.text(0.01, 0.005, f"합성 가입이력 · 고정 분석본 2026-08-21 · 알뜰폰 {len(y):,}건 · y = log(점유율) · 6축(문자 제외)",
             fontsize=8.5, color="#666")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=125, bbox_inches="tight")

    if pieces:
        pieces.mkdir(parents=True, exist_ok=True)
        f, a = plt.subplots(figsize=(8, 4.2))
        bars(a, stage, r2, with_desc=False)
        f.tight_layout()
        f.savefig(pieces / "steps_bars.png", dpi=200, transparent=True)
        plt.close(f)
        for name, X, col, ticks, xl, *_ in panels(stage):
            f, a = plt.subplots(figsize=(3.6, 3.6))
            residual_panel(a, models[id(X)], X, col, y, ticks, xl)
            f.tight_layout()
            f.savefig(pieces / f"{name}.png", dpi=200, transparent=True)
            plt.close(f)
        print("pieces ->", pieces)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pieces", type=Path, help="막대·잔차 그림을 투명 PNG 로 따로 저장할 폴더")
    args = parser.parse_args()
    X, y = build_xy()
    stage = steps(X)
    r2 = [cv_r2(s[2], y) for s in stage]
    for s, v in zip(stage, r2):
        print(f"{s[0]:<10} R² {v:.4f}")
    draw(stage, r2, y, args.pieces)
    print(OUT)


if __name__ == "__main__":
    main()
