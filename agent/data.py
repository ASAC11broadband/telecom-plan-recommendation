# -*- coding: utf-8 -*-
"""데이터 소스 레이어 — CSV 로드 + pandas 필터. LLM 없음.

에이전트가 아니라 2단계(recommend)가 쓰는 도구다.
예산·통신사 같은 절대 조건은 여기서 결정적으로 거른다 (LLM 에 맡기지 않는다).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"  # CSV 는 프로젝트 루트의 data/ 에 둔다

PLANS_CSV = DATA_DIR / "통신요금제_통합데이터_최종.csv"
BENEFITS_CSV = DATA_DIR / "통신요금제_혜택상세_최종.csv"
DAILY_CSV = DATA_DIR / "통신요금제_가입자일별_최종.csv"

_plans = _benefits = _trend = None


def load() -> None:
    global _plans, _benefits, _trend
    if _plans is not None:
        return
    _plans = pd.read_csv(PLANS_CSV)
    _plans["effective_fee"] = _plans["discounted_fee"].where(
        _plans["discounted_fee"] > 0, _plans["monthly_fee"]
    )
    _benefits = pd.read_csv(BENEFITS_CSV)
    # plan_id: 일별 CSV 는 전부 숫자라 int 로 추론된다. 통합 CSV 쪽은 "1692_..." 형태가
    # 섞여 str 이라, 명시하지 않으면 dtype 불일치로 조인이 통째로 0건이 된다.
    daily = pd.read_csv(DAILY_CSV, dtype={"plan_id": str}).sort_values("date")
    # 최근 30일 가입자 증감 (plan_id 기준 — plan_name 은 중복이 있어 키로 못 쓴다)
    g = daily.groupby("plan_id")["subscriber_count"]
    _trend = (g.last() - g.first()).rename("subscriber_change_30d")


def filter_candidates(profile: dict, limit: int = 20) -> tuple[list[dict], str]:
    """profile 조건으로 후보 추출, 가입자수 순 상위 limit개. (후보, 완화노트) 반환."""
    load()
    df = _plans
    note = ""

    def apply(df, p):
        if p.get("budget_max_won"):
            df = df[df["effective_fee"] <= p["budget_max_won"]]
        if p.get("data_unlimited"):
            # 완전 무제한 + 소진 후 속도제한형 모두 "무제한"으로 취급
            df = df[df["data_unlimited"] | df["data_throttle_speed"].notna()]
        elif p.get("min_data_gb"):
            df = df[df["data_unlimited"] | (df["data_gb"] >= p["min_data_gb"])]
        if p.get("voice_unlimited"):
            df = df[df["voice_unlimited"]]
        if p.get("carrier_pref"):
            df = df[df["host_mno"].isin(p["carrier_pref"])]
        if p.get("mvno_ok") is False:
            df = df[df["carrier_type"] == "MNO"]
        if p.get("network_gen"):
            df = df[df["network_gen"].astype(str).str.contains(p["network_gen"], na=False)]
        if p.get("ott_wanted"):
            pat = "|".join(p["ott_wanted"])
            df = df[df["ott_options"].astype(str).str.contains(pat, case=False, na=False)]
        return df

    out = apply(df, profile)
    # 후보 부족 시 OTT 조건만 완화 (랭킹 LLM 이 혜택으로 재평가)
    if len(out) < 5 and profile.get("ott_wanted"):
        relaxed = {**profile, "ott_wanted": None}
        out = pd.concat([out, apply(df, relaxed)]).drop_duplicates("plan_id")
        note = (
            f"조건을 모두 만족하는 요금제가 {len(apply(df, profile))}개뿐이라 "
            f"OTT 조건({', '.join(profile['ott_wanted'])})을 완화해 후보를 채웠음. "
            "리포트에 이 사실과 트레이드오프를 반드시 명시할 것."
        )
    out = out.sort_values("subscriber_count", ascending=False).head(limit)
    return [_row_summary(r) for _, r in out.iterrows()], note


def find_candidate(candidates: list[dict], name: str) -> dict | None:
    """랭킹 LLM 이 접미사 등을 붙이는 경우 대비 부분일치 허용."""
    for c in candidates:
        if c["plan_name"] == name or c["plan_name"] in name or name in c["plan_name"]:
            return c
    return None


def _row_summary(r) -> dict:
    load()
    bens = _benefits[_benefits["plan_id"] == r["plan_id"]]["benefit_name"].dropna().unique()[:8]
    if r["data_unlimited"]:
        data = "완전 무제한"
    elif pd.notna(r.get("data_throttle_speed")):
        data = f"{r['data_gb']}GB + 무제한(소진 후 {r['data_throttle_speed']})"
    else:
        data = f"{r['data_gb']}GB"
    return {
        "plan_name": r["plan_name"],
        "carrier": r["mvno_brand"] if r["carrier_type"] == "MVNO" and pd.notna(r["mvno_brand"]) else r["host_mno"],
        "carrier_type": r["carrier_type"],
        "network": r["network_gen"] if pd.notna(r["network_gen"]) else "",
        "data": data,
        "voice": "무제한" if r["voice_unlimited"] else f"{r['voice_minutes']}분",
        "monthly_fee": int(r["monthly_fee"]),
        "discounted_fee": int(r["discounted_fee"]),
        "discount_type": r["discount_type"] if pd.notna(r["discount_type"]) else "",
        "ott_options": r["ott_options"] if pd.notna(r["ott_options"]) else "",
        "benefits": " | ".join(bens),
        "subscribers": int(r["subscriber_count"]),
        "subscriber_change_30d": int(_trend.get(r["plan_id"], 0)),
    }


if __name__ == "__main__":
    c, note = filter_candidates(
        {"budget_max_won": 50000, "data_unlimited": True, "ott_wanted": ["넷플릭스"]}
    )
    assert len(c) > 0
    assert all(x["discounted_fee"] <= 50000 or x["monthly_fee"] <= 50000 for x in c)
    assert note  # 넷플릭스+5만원 이하는 0개라 완화 노트가 있어야 함
    print(f"self-check ok: {len(c)} candidates, note={note[:40]}...")
