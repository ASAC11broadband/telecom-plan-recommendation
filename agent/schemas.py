"""단계 사이를 오가는 데이터 계약.

TODO: 필드는 팀에서 채우기. 여기를 바꾸면 prompts.py 도 같이 손봐야 한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    """1단계 산출물 — 사용자 요구사항."""

    # TODO: monthly_budget_krw, data_usage_gb, carrier, network, priorities ...
    notes: str = ""


class ScoredPlan(BaseModel):
    """2단계 산출물 — 점수가 매겨진 요금제 하나."""

    plan_id: str = ""
    score: float = 0.0
    # TODO: plan_name, carrier, monthly_fee_krw, reasons, concerns ...


class RankingResult(BaseModel):
    """2단계 LLM 구조화 출력 컨테이너."""

    plans: list[ScoredPlan] = Field(default_factory=list)


class Evaluation(BaseModel):
    """4단계 산출물 — 채점 결과와 재시도 지시."""

    passed: bool = False
    feedback: str = ""
    retry_target: Literal["profiling", "matching", "none"] = "none"
    # TODO: overall_score, criteria, issues ...
