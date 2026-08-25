# -*- coding: utf-8 -*-
"""단계 사이를 오가는 데이터 계약.

여기를 바꾸면 prompts.py 도 같이 손봐야 한다 (description 이 곧 LLM 지시문이다).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    """1단계 산출물 — 사용자 요구사항."""

    budget_max_won: Optional[int] = Field(None, description="월 예산 상한(원). 언급 없으면 null")
    data_unlimited: Optional[bool] = Field(None, description="데이터 무제한 필수 여부")
    min_data_gb: Optional[float] = Field(None, description="최소 데이터량(GB)")
    voice_unlimited: Optional[bool] = Field(None, description="통화 무제한 필수 여부")
    ott_wanted: Optional[list[str]] = Field(None, description="원하는 OTT/구독 (예: 넷플릭스, 유튜브)")
    carrier_pref: Optional[list[str]] = Field(None, description="선호 통신사망: KT/SKT/LGU+ 만")
    mvno_ok: Optional[bool] = Field(None, description="알뜰폰 허용 여부. 대형 통신사만 원하면 false")
    network_gen: Optional[str] = Field(None, description="5G 또는 LTE 지정 시")
    notes: str = Field("", description="기타 요구사항 요약")


class ScoredPlan(BaseModel):
    """2단계 산출물 — 점수가 매겨진 요금제 하나."""

    plan_name: str = Field("", description="후보 데이터의 plan_name 을 글자 그대로 복사")
    score: int = Field(0, description="0-100 적합도")
    reason: str = Field("", description="선정 이유 1-2문장, 데이터 근거 인용")


class RankingResult(BaseModel):
    """2단계 LLM 구조화 출력 컨테이너."""

    plans: list[ScoredPlan] = Field(default_factory=list, description="상위 5개, 적합도 순")


class Evaluation(BaseModel):
    """4단계 산출물 — 채점 결과와 재시도 지시."""

    passed: bool = Field(False, description="리포트가 후보 데이터와 모순되는 주장이 없으면 true")
    feedback: str = Field("", description="모순 내용과 개선 지시. 합격이면 빈 문자열")
    retry_target: Literal["profiling", "recommend", "none"] = Field(
        "none",
        description=(
            "재시도할 단계. 조건 추출 자체가 틀렸으면 profiling, "
            "후보·랭킹이 문제면 recommend, 합격이면 none"
        ),
    )
