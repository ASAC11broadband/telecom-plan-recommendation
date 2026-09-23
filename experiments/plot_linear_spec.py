"""왜 √가격·log 데이터로 변환했나 — 한 장 그림.

notebooks/linear_spec_diagnostic.ipynb 의 모집단·피처·CV 를 고정 분석본(data/baseline) 위에서 재현해서
  위:   Ridge(원자료) → Ridge(변환) → GBM depth1 → GBM depth3 의 교차검증 R² 와 갭 분해
  아래: 가격·데이터의 부분의존도(PDP). 회색 = 가법 GBM 이 본 모양, 파랑 = Ridge 직선
을 한 장에 그린다. 결과: outputs/분석노트/왜_변환했나.png

실행: python -m experiments.plot_linear_spec [--quick]
  --quick 은 CV 를 건너뛰고 R² 를 노트북 기록값으로 적는다(그림 배치 확인용).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import lightgbm as lgb
import matplotlib
import numpy as np
import pandas as pd
from sklearn.inspection import partial_dependence
from sklearn.linear_model import RidgeCV
from sklearn.metrics import r2_score
from sklearn.model_selection import RepeatedKFold, train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import gridspec, rcParams  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
# 가중치(weight_bootstrap.json)는 고정 분석본에서 뽑았다. 루트 data/ 는 서비스용 최신본이라
# 여기서 읽으면 노트북 수치가 재현되지 않는다(2026-09-22 기준 R² 0.32/0.42/0.45/0.50).
BASELINE = DATA / "baseline" / "2026-08-21"
OUT = ROOT / "outputs" / "분석노트" / "왜_변환했나.png"

AXES = ["cost_month", "data_total_gb", "qos", "tether_gb", "voice_min", "sms_cnt", "ott_value"]
SPEC = {"cost_month": "sqrt", "data_total_gb": "log1p"}  # 노트북 greedy 탐색 결과
TRANSFORMS = {"raw": lambda v: v, "log1p": np.log1p, "sqrt": np.sqrt}
CV = RepeatedKFold(n_splits=5, n_repeats=5, random_state=0)
ALPHAS = np.logspace(-3, 3, 25)
BLUE, GREEN, VERM, GRAY = "#0072B2", "#009E73", "#D55E00", "#8a8a85"

# 노트북 기록값(2026-09 실행). --quick 일 때만 쓴다.
RECORDED = {
    "ridge_raw": 0.376, "ridge_t": 0.483, "gbm_d1": 0.525, "gbm_d3": 0.585,
    "loss": {"cost_month": 0.065, "data_total_gb": 0.052},
}


# ---------- 노트북과 같은 X, y ----------
def _qos_mbps(value) -> float:
    if pd.isna(value):
        return 0.0
    m = re.match(r"([\d.]+)\s*(kbps|mbps)", str(value).strip().lower().replace(" ", ""))
    if not m:
        return 0.0
    n = float(m.group(1))
    return n / 1000 if m.group(2) == "kbps" else n


def build_xy() -> tuple[pd.DataFrame, np.ndarray]:
    plans = pd.read_csv(BASELINE / "통신요금제_통합데이터_최종.csv", dtype={"plan_id": str})
    benefits = pd.read_csv(BASELINE / "통신요금제_혜택상세_최종.csv", dtype={"plan_id": str})
    prices = pd.read_csv(DATA / "ott_prices.csv")
    price_map = dict(zip(prices.service, prices.monthly_won.astype(int)))

    d = plans[plans.carrier_type == "MVNO"].copy()
    d = d[~d.mvno_brand.isin(["SKT", "KT", "LG U+", "LGU+"])].copy()

    d["cost_month"] = d.discounted_fee
    extra = benefits[benefits.benefit_category == "추가데이터"].copy()
    extra["gb"] = (
        extra.benefit_name.fillna("").str.extract(r"([\d.]+)\s*GB", flags=re.I)[0].astype(float)
    )
    d["extra_gb"] = d.plan_id.map(extra.groupby("plan_id").gb.sum()).fillna(0)
    d["data_total_gb"] = np.where(
        d.data_unlimited, 300,
        (d.data_gb.fillna(d.daily_data_gb.fillna(0) * 30) + d.extra_gb).clip(0, 300),
    )
    d["qos"] = d.data_throttle_speed.map(_qos_mbps)
    d["tether_gb"] = d.tethering_gb.fillna(0)
    d["voice_min"] = np.where(d.voice_unlimited, 1000, d.voice_minutes.fillna(0)).clip(0, 1000)
    d["sms_cnt"] = np.where(d.sms_unlimited, 1000, d.sms_count.fillna(0)).clip(0, 1000)
    ott_b = benefits[benefits.benefit_service.isin(price_map)]
    ott_services = d.plan_id.map(
        ott_b.groupby("plan_id")["benefit_service"].apply(lambda v: sorted(set(v.dropna())))
    )
    d["ott_value"] = ott_services.apply(
        lambda s: sum(price_map.get(x, 0) for x in s) if isinstance(s, list) else 0
    )

    share = d.subscriber_count / d.subscriber_count.sum()
    y = np.log(share.clip(lower=1e-12).values)
    brand = pd.get_dummies(d.mvno_brand, prefix="brand", dummy_na=True).astype(float)
    X = pd.concat([d[AXES].reset_index(drop=True), brand.reset_index(drop=True)], axis=1)
    return X, y


def apply_spec(X: pd.DataFrame, spec: dict) -> pd.DataFrame:
    out = X.copy()
    for ax, t in spec.items():
        out[ax] = TRANSFORMS[t](X[ax].values)
    return out


# ---------- 노트북과 같은 모델·CV ----------
def ridge():
    return make_pipeline(StandardScaler(), RidgeCV(alphas=ALPHAS))


def gbm(depth: int):
    return lgb.LGBMRegressor(
        n_estimators=3000, learning_rate=0.05, max_depth=depth, num_leaves=2 ** depth,
        min_child_samples=20, reg_lambda=1.0, random_state=0, verbosity=-1,
    )


def fit_gbm(depth: int, X: pd.DataFrame, y: np.ndarray):
    itr, iva = train_test_split(np.arange(len(X)), test_size=0.2, random_state=0)
    return gbm(depth).fit(
        X.iloc[itr], y[itr], eval_set=[(X.iloc[iva], y[iva])],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )


def ridge_cv(X: pd.DataFrame, y: np.ndarray) -> np.ndarray:
    return np.array([
        r2_score(y[te], ridge().fit(X.iloc[tr], y[tr]).predict(X.iloc[te]))
        for tr, te in CV.split(X)
    ])


def gbm_cv(depth: int, X: pd.DataFrame, y: np.ndarray) -> np.ndarray:
    return np.array([
        r2_score(y[te], fit_gbm(depth, X.iloc[tr], y[tr]).predict(X.iloc[te]))
        for tr, te in CV.split(X)
    ])


def scores(X: pd.DataFrame, Xt: pd.DataFrame, y: np.ndarray, quick: bool) -> dict:
    if quick:
        return dict(RECORDED)
    ridge_t = ridge_cv(Xt, y)
    loss = {}
    for ax in SPEC:
        back = ridge_cv(apply_spec(X, {k: v for k, v in SPEC.items() if k != ax}), y)
        loss[ax] = float((ridge_t - back).mean())
    return {
        "ridge_raw": float(ridge_cv(X, y).mean()),
        "ridge_t": float(ridge_t.mean()),
        "gbm_d1": float(gbm_cv(1, Xt, y).mean()),
        "gbm_d3": float(gbm_cv(3, Xt, y).mean()),
        "loss": loss,
    }


# ---------- 그림 ----------
def pdp(model, X: pd.DataFrame, col: str):
    p = partial_dependence(model, X, [col], grid_resolution=80, kind="average")
    gx, gy = p["grid_values"][0], p["average"][0]
    return gx, gy - gy.mean()


def draw(s: dict, X: pd.DataFrame, Xt: pd.DataFrame, y: np.ndarray) -> None:
    rcParams["font.family"] = "Malgun Gothic"
    rcParams["axes.unicode_minus"] = False
    models = {
        "raw": (ridge().fit(X, y), fit_gbm(1, X, y)),
        "t": (ridge().fit(Xt, y), fit_gbm(1, Xt, y)),
    }
    curv = s["gbm_d1"] - s["ridge_raw"]
    inter = s["gbm_d3"] - s["gbm_d1"]
    recovered = s["ridge_t"] - s["ridge_raw"]

    fig = plt.figure(figsize=(15, 8.6))
    gs = gridspec.GridSpec(2, 6, height_ratios=[1, 1], hspace=0.62, wspace=0.4)

    ax = fig.add_subplot(gs[0, :])
    bars = [
        ("Ridge\n원자료", s["ridge_raw"], BLUE),
        ("Ridge\n√·log 변환", s["ridge_t"], BLUE),
        ("GBM depth1\n곡선 O · 조합 X", s["gbm_d1"], GREEN),
        ("GBM depth3\n곡선 O · 조합 O", s["gbm_d3"], VERM),
    ]
    xs = np.arange(len(bars))
    ax.bar(xs, [b[1] for b in bars], color=[b[2] for b in bars], width=0.55, alpha=0.9)
    for x, b in zip(xs, bars):
        ax.text(x, b[1] + 0.008, f"{b[1]:.3f}", ha="center", fontsize=11, fontweight="bold")
    ax.set_xticks(xs, [b[0] for b in bars], fontsize=10)
    ax.set_ylim(0.3, 0.68)
    ax.set_ylabel("교차검증 R² (5×5 fold)")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)

    def brace(x0, x1, yv, text, color):
        ax.annotate("", xy=(x1, yv), xytext=(x0, yv),
                    arrowprops=dict(arrowstyle="<->", color=color, lw=1.6))
        ax.text((x0 + x1) / 2, yv + 0.012, text, ha="center", fontsize=10, color=color)

    brace(0, 2, 0.60, f"곡률 갭 +{curv:.3f}  —  '직선이라서' 놓친 몫", GREEN)
    brace(2, 3, 0.645, f"상호작용 갭 +{inter:.3f}  —  '축을 못 섞어서' 놓친 몫", VERM)
    ax.annotate(f"변환이 회수 +{recovered:.3f}", xy=(1, s["ridge_t"]), xytext=(0.4, 0.54),
                fontsize=10, color=BLUE, arrowprops=dict(arrowstyle="->", color=BLUE))
    ax.set_title("왜 변환했나 — 같은 fold 에서 세 모델의 R²", loc="left", fontsize=12, fontweight="bold")
    fig.text(0.125, 0.525,
             "※ 변환 뒤에도 남는 갭은 등급 문턱·조건형이라 곱항으로도 안 메워짐 → 가중치가 아니라 하드 필터가 담당",
             fontsize=9.5, color="#444")

    def panel(pos, key, col, title, xlabel, note, color):
        a = fig.add_subplot(gs[1, pos])
        r, g = models[key]
        Xs = X if key == "raw" else Xt
        for m, c, lw in ((g, GRAY, 3.2), (r, BLUE, 2)):
            a.plot(*pdp(m, Xs, col), color=c, lw=lw)
        a.set_title(title, fontsize=10.5, loc="left")
        a.set_xlabel(xlabel, fontsize=9)
        a.set_yticks([])
        a.grid(alpha=0.2)
        for sp in ("top", "right", "left"):
            a.spines[sp].set_visible(False)
        a.text(0.02, 0.04, note, transform=a.transAxes, fontsize=10, color=color, fontweight="bold")

    loss = s["loss"]
    panel(0, "raw", "cost_month", "① 가격 · 원자료", "원", "휘어 있음", VERM)
    panel(1, "t", "cost_month", "② 가격 · √ 변환", "√원", f"펴짐  R² +{loss['cost_month']:.3f}", BLUE)
    panel(2, "raw", "data_total_gb", "③ 데이터 · 원자료", "GB", "휘어 있음", VERM)
    panel(3, "t", "data_total_gb", "④ 데이터 · log1p 변환", "log(1+GB)",
          f"펴짐  R² +{loss['data_total_gb']:.3f}", BLUE)

    t = fig.add_subplot(gs[1, 4:])
    t.axis("off")
    t.text(0, 1.0, "변환 채택 기준", fontsize=11, fontweight="bold", va="top")
    t.text(0, 0.88, "fold 짝지은 95% 구간이 0을 넘고 평균 개선 ≥ 0.005 일 때만",
           fontsize=9.5, va="top", color="#333")
    colx = [0, 0.42, 0.62, 0.88]
    for cx, h in zip(colx, ["변수", "변환", "R² 변화*", "채택"]):
        t.text(cx, 0.72, h, fontsize=9.5, va="top", color="#666")
    rows = [
        ("cost_month", "sqrt", f"+{loss['cost_month']:.3f}", "O"),
        ("data_total_gb", "log1p", f"+{loss['data_total_gb']:.3f}", "O"),
        ("qos", "–", "< 0.005", "X"),
        ("voice_min", "–", "< 0.005", "X"),
        ("tether_gb", "–", "< 0.005", "X"),
    ]
    for i, row in enumerate(rows):
        ok = row[3] == "O"
        for cx, val in zip(colx, row):
            t.text(cx, 0.62 - i * 0.095, val, fontsize=10, va="top",
                   color=BLUE if ok else "#888", fontweight="bold" if ok else "normal")
    t.text(0, 0.12, "* 그 축만 원자료로 되돌렸을 때의 R² 손실", fontsize=8.5, va="top", color="#666")
    t.text(0, 0.04,
           "회색 = 데이터가 보여주는 모양 (가법 GBM, 한 축씩)\n"
           "파랑 = 우리가 쓰는 직선 (Ridge)\n"
           "직선이 회색을 따라가야 '기울기 = 가중치'가 성립",
           fontsize=9.5, va="top", color="#333")
    fig.text(0.01, 0.005,
             f"합성 가입이력 기준 · y = log(점유율) · 알뜰폰 {len(X):,}건 · 아래 곡선은 부분의존도(PDP)",
             fontsize=8.5, color="#666")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=125, bbox_inches="tight")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="CV 생략, 노트북 기록값 사용")
    args = parser.parse_args()
    X, y = build_xy()
    Xt = apply_spec(X, SPEC)
    s = scores(X, Xt, y, args.quick)
    print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()})
    draw(s, X, Xt, y)
    print(OUT)


if __name__ == "__main__":
    main()
