# -*- coding: utf-8 -*-
"""단계 사이를 오가는 데이터 계약.

여기를 바꾸면 Profiling/Recommend 프롬프트도 같이 손봐야 한다.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class AppUsage(BaseModel):
    """앱별 하루 이용 시간과 사용자가 명시한 화질·모드."""

    service: Literal[
        "youtube", "netflix", "disney_plus", "tiktok", "instagram",
        "instagram_reels", "spotify", "google_maps", "zoom", "whatsapp",
        "mobile_game", "casual_game", "pokemongo_game", "moba_game",
        "battle_royale_game", "league_of_legend_game", "battleground_game",
        "clashroyale_game", "online_rpg_game", "fortnite_game",
        "callofduty_game", "brawlstars_game", "stardewvalley_game",
        "generic_video", "generic_shortform",
    ] = Field(..., description="usage.py에서 사용하는 정규화된 앱 식별자")
    daily_hours: float = Field(..., gt=0, description="해당 앱의 하루 평균 이용 시간")
    mode: Optional[str] = Field(None, description="사용자가 명시한 화질·이용 모드")


class UserProfile(BaseModel):
    """1단계 산출물 — 사용자 요구사항."""

    budget_min_won: Optional[int] = Field(None, description="월 예산 하한(원). 언급 없으면 null")
    budget_max_won: Optional[int] = Field(None, description="월 예산 상한(원). 언급 없으면 null")
    min_data_gb: Optional[float] = Field(None, description="최소 데이터량(GB)")
    target_data_gb: Optional[float] = Field(
        None,
        description="목표 데이터량(GB). '100GB 정도'처럼 근접 적합도를 계산할 때 사용",
    )
    max_data_gb: Optional[float] = Field(
        None, description="최대 데이터량(GB). 무제한은 상한 조건에서 제외"
    )
    data_unlimited: Optional[bool] = Field(None, description="데이터 무제한 필수 여부. 언급 없으면 null")
    require_full_unlimited: Optional[bool] = Field(
        None,
        description=(
            "'기본 제공량 자체가 무제한'인 상품만 원하면 true. "
            "'완전 무제한', '속도 제한 없는 무제한'처럼 QoS형을 명시적으로 배제할 때만 사용한다"
        ),
    )
    min_qos_mbps: Optional[float] = Field(None, description="데이터 소진 후 최소 요구 속도(Mbps). 언급 없으면 null")
    requires_qos: Optional[bool] = Field(
        None,
        description="속도 수치와 관계없이 데이터 소진 후 QoS 제공이 필수이면 true. 언급 없으면 null",
    )
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
    network_preference: Optional[Literal["LTE", "5G"]] = Field(
        None,
        description="LTE 또는 5G를 순위에서 우선한다. 다른 세대 후보는 제외하지 않는다.",
    )
    age_condition: Optional[str] = Field(
        None,
        description="가입 대상 조건의 DB 표준값(예: 만 34세 이하, 만 65세 이상, 현역병사). 언급 없으면 null",
    )
    user_age: Optional[int] = Field(
        None,
        description=(
            "사용자가 밝힌 본인 나이(만 나이). 연령 전용 요금제의 가입 자격 판정에만 쓴다. "
            "'20대'처럼 범위만 말하면 만 나이를 확정하지 말고 null로 둔다. 언급 없으면 null"
        ),
    )
    wanted_benefits: Optional[list[str]] = Field(
        None,
        description="원하는 개별 서비스·혜택명 목록(예: 넷플릭스, 지니뮤직). 언급 없으면 null",
    )
    wanted_benefit_categories: Optional[
        list[
            Literal[
                "영상/OTT",
                "음악/오디오",
                "도서/콘텐츠",
                "제휴서비스",
                "복합/선택혜택",
                "교육/AI서비스",
                "멤버십",
                "스마트기기",
                "추가데이터",
                "사은품/페이백",
                "기타",
            ]
        ]
    ] = Field(
        None,
        description="원하는 혜택 유형 목록. 개별 서비스명이 아닌 포괄적 유형 요청에 사용",
    )
    benefit_match_mode: Literal["all", "any"] = Field(
        "all",
        description="여러 혜택 조건을 모두 만족해야 하면 all, 하나 이상이면 any",
    )
    min_discount_period_months: Optional[int] = Field(
        None,
        description="최소 할인·프로모션 유지 기간(개월). 언급 없으면 null",
    )
    daily_video_hours: Optional[float] = Field(None, description="하루 일반 영상 시청 시간")
    daily_shortform_hours: Optional[float] = Field(None, description="하루 숏폼 시청 시간")
    daily_game_hours: Optional[float] = Field(None, description="하루 모바일 게임 시간")
    app_usages: list[AppUsage] = Field(
        default_factory=list,
        description="사용자가 말한 앱별 하루 이용 시간과 선택적 화질·모드",
    )
    smartchoice_usage_pattern: Optional[
        Literal["wifi_primary", "web_music_primary", "video_1h", "video_2h", "video_3h_plus"]
    ] = Field(None, description="앱·화질이 특정되지 않은 스마트초이스 생활패턴")
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
    priorities: Optional[
        list[Literal["price", "data", "qos", "benefit", "voice", "tethering"]]
    ] = Field(
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
    matched_benefits: list[str] = Field(
        default_factory=list,
        description="사용자가 요청한 혜택 조건과 직접 일치하는 실제 혜택명",
    )
    criteria_fit: dict[str, float] = Field(
        default_factory=dict,
        description="축별 충족도(0~1). 총점 하나로는 후보 2천 건에서 상위권이 전부 100 으로 포화한다",
    )
    expected_rank: Optional[float] = None
    first_rank_acceptability: Optional[float] = None
    recommendation_fit: Optional[float] = Field(
        None, description="현재 조건에 대한 다기준 적합도(0~100). 만족 확률이 아니다"
    )
    top3_acceptability: Optional[float] = Field(
        None, description="가중치 300세트 중 상위 3위 안에 든 비율(0~1)"
    )


class RankingResult(BaseModel):
    """2단계 LLM 구조화 출력 컨테이너."""

    plans: list[ScoredPlan] = Field(default_factory=list, description="상위 3개, 적합도 순")


class Evaluation(BaseModel):
    """4단계 산출물 — 채점 결과와 재시도 지시."""

    passed: bool = Field(False, description="리포트가 후보 데이터와 모순되는 주장이 없으면 true")
    feedback: str = Field("", description="모순 내용과 개선 지시. 합격이면 빈 문자열")
    retry_target: Literal["profiling", "recommend", "report", "none"] = Field(
        "none",
        description=(
            "재시도할 단계. 조건 추출 자체가 틀렸으면 profiling, "
            "후보·랭킹이 문제면 recommend, 추천은 맞는데 리포트 서술만 문제면 report, "
            "합격이거나 재시도로 고칠 수 없으면 none"
        ),
    )
