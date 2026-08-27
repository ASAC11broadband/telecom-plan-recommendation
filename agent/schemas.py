# -*- coding: utf-8 -*-
"""단계 사이를 오가는 데이터 계약.

여기를 바꾸면 Profiling/Recommend 프롬프트도 같이 손봐야 한다.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    """1단계 산출물 — 사용자 요구사항."""

    budget_min_won: Optional[int] = Field(None, description="월 예산 하한(원). 언급 없으면 null")
    budget_max_won: Optional[int] = Field(None, description="월 예산 상한(원). 언급 없으면 null")
    min_data_gb: Optional[float] = Field(None, description="최소 데이터량(GB)")
    data_unlimited: Optional[bool] = Field(None, description="데이터 무제한 필수 여부. 언급 없으면 null")
    min_qos_mbps: Optional[float] = Field(None, description="데이터 소진 후 최소 요구 속도(Mbps). 언급 없으면 null")
    min_tethering_gb: Optional[float] = Field(None, description="최소 테더링 제공량(GB). 언급 없으면 null")
    min_voice_minutes: Optional[int] = Field(None, description="최소 통화 시간(분). 언급 없으면 null")
    voice_unlimited: Optional[bool] = Field(None, description="통화 무제한 필수 여부")
    sms_unlimited: Optional[bool] = Field(None, description="문자 무제한 필수 여부. 언급 없으면 null")
    carrier_type: Optional[Literal["MNO", "MVNO"]] = Field(
        None,
        description="통신 3사만 원하면 MNO, 알뜰폰만 원하면 MVNO. 유형을 지정하지 않으면 null",
    )
    host_mno: Optional[Literal["SKT", "KT", "LGU+"]] = Field(
        None,
        description="선호 통신사 또는 사용 망: SKT/KT/LGU+. 언급 없으면 null",
    )
    mvno_brand: Optional[str] = Field(
        None,
        description="특정 알뜰폰 브랜드명(예: KT엠모바일). 언급 없으면 null",
    )
    network_gen: Optional[Literal["LTE", "5G"]] = Field(None, description="LTE 또는 5G. 언급 없으면 null")
    age_condition: Optional[str] = Field(
        None,
        description="가입 대상 조건의 DB 표준값(예: 만 34세 이하, 만 65세 이상, 현역병사). 언급 없으면 null",
    )
    wanted_benefits: Optional[list[str]] = Field(
        None,
        description="원하는 OTT/구독/부가혜택 목록(예: 넷플릭스, 유튜브 프리미엄). 언급 없으면 null",
    )
    min_discount_period_months: Optional[int] = Field(
        None,
        description="최소 할인·프로모션 유지 기간(개월). 언급 없으면 null",
    )
    daily_video_hours: Optional[float] = Field(None, description="하루 일반 영상 시청 시간")
    daily_shortform_hours: Optional[float] = Field(None, description="하루 숏폼 시청 시간")
    daily_game_hours: Optional[float] = Field(None, description="하루 모바일 게임 시간")
    estimated_monthly_data_gb: Optional[float] = Field(
        None,
        description="이용 시간으로 코드가 계산한 월 예상 데이터량(GB)",
    )
    usage_estimate_notes: list[str] = Field(
        default_factory=list,
        description="월 데이터 사용량 추정 근거",
    )
    reference_plan_name: Optional[str] = Field(
        None,
        description="비교 기준으로 사용자가 언급한 현재·기존 요금제명",
    )
    reference_fee_won: Optional[int] = Field(None, description="사용자가 말한 현재 요금(원)")
    reference_data_gb: Optional[float] = Field(None, description="현재 기본 데이터량(GB)")
    reference_data_unlimited: Optional[bool] = Field(None, description="현재 데이터 무제한 여부")
    reference_voice_minutes: Optional[int] = Field(None, description="현재 통화 제공량(분)")
    reference_voice_unlimited: Optional[bool] = Field(None, description="현재 통화 무제한 여부")
    reference_qos_mbps: Optional[float] = Field(None, description="현재 소진 후 속도(Mbps)")
    comparison_goals: Optional[
        list[Literal["cheaper", "more_data", "faster_qos", "similar", "better"]]
    ] = Field(None, description="기준 요금제 대비 사용자가 원하는 개선·비교 방향")
    priorities: Optional[list[Literal["price", "data", "qos", "benefit", "voice", "sms", "tethering", "carrier"]]] = Field(
        None,
        description="사용자가 직접 말한 추천 우선순위를 중요도 순으로 저장. 언급 없으면 null",
    )
    hard_constraints: list[str] = Field(
        default_factory=list,
        description="추천 과정에서 절대로 완화하면 안 되는 UserProfile 필드명",
    )
    missing: list[str] = Field(
        default_factory=list,
        description="추천에 중요하지만 사용자 발화에서 확인하지 못한 필드명",
    )
    ambiguous: list[str] = Field(default_factory=list, description="두 가지 이상으로 해석될 수 있는 조건")
    assumptions: list[str] = Field(
        default_factory=list,
        description="사용자 발화만으로 확정할 수 없어 별도로 적용한 가정",
    )
    needs_user_input: bool = Field(False, description="추천 전에 사용자에게 반드시 추가 질문해야 하면 true")
    followup_question: Optional[str] = Field(
        None,
        description="needs_user_input이 true일 때 사용자에게 보여줄 후속 질문",
    )
    notes: Optional[str] = Field(None, description="기타 요구사항 요약. 언급 없으면 null")


class ScoredPlan(BaseModel):
    """2단계 산출물 — 점수가 매겨진 요금제 하나."""

    plan_id: str = Field(..., description="후보 데이터의 고유 plan_id를 글자 그대로 복사")
    plan_name: str = Field(..., description="후보 데이터의 plan_name을 글자 그대로 복사")
    score: int = Field(..., ge=0, le=100, description="후보군 내 사용자 요청 상대 적합도")
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
