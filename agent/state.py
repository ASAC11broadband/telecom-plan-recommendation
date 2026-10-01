# -*- coding: utf-8 -*-
"""파이프라인 전역 상태 + LLM 팩토리.

- PipelineState: 각 단계가 무엇을 읽고 쓰는지의 계약. 새로 합류하면 여기부터 읽는다.
- get_profile_llm / get_report_llm / get_eval_llm: 역할별 모델 설정.
"""

from __future__ import annotations

import operator
import os
from typing import Annotated, Optional, TypedDict

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.graph.message import add_messages

from .schemas import Evaluation, ScoredPlan, UserProfile

# ─────────────────────────── 상태 ───────────────────────────


class PipelineState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    # 0건 화면에서 사용자가 직접 누른 "조건 풀기" 필드. 자연어 재해석에 맡기지 않고
    # profiling 결과에서 정확히 제거한다.
    relaxed_fields: list[str]

    profile: Optional[UserProfile]      # 1단계가 write
    candidates: list[dict]              # 2단계(a) 하드 필터가 통과시킨 후보 원본
    reference: Optional[dict]           # 2단계가 DB 에서 확정한 비교 기준 요금제
    reference_verdict: Optional[dict]   # 현재 요금제 유지/전환/판단불가 (코드 판정)
    blockers: list[dict]                # 후보 0건일 때 어느 조건이 막았는지
    recommendation_trace: dict         # 실제 후보 수·평가 기준. 발표 및 결과 설명용
    clarification_question: Optional[str]  # 진행 전 사용자에게 확인할 질문
    ranked: list[ScoredPlan]            # 2단계(b) SMAA-2 랭킹 결과
    report: str                         # 3단계가 write
    evaluation: Optional[Evaluation]    # 4단계가 write

    feedback: Annotated[list[str], operator.add]  # 재시도 피드백 누적
    attempt: int                                  # 리포트를 만든 횟수
    # 검증 에이전트가 무엇을 잡고 어떻게 했는지. 화면에는 내보내지 않고 로그·분석용으로만 쓴다.
    eval_log: Annotated[list[dict], operator.add]


def user_query(state: PipelineState) -> str:
    """현재까지의 사용자 발화를 시간순으로 합쳐 반환한다."""
    return "\n".join(
        str(message.content)
        for message in state.get("messages", [])
        if isinstance(message, HumanMessage)
    )


def feedback_block(state: PipelineState) -> str:
    """누적된 평가 피드백을 프롬프트에 끼워 넣을 형태로. 없으면 빈 문자열."""
    fb = state.get("feedback", [])
    return "[이전 평가 피드백 — 반드시 반영]\n" + "\n".join(fb) + "\n" if fb else ""


# ─────────────────────────── LLM ───────────────────────────

load_dotenv()

# 과거 MODEL 환경변수는 호환성을 위해 역할별 모델의 공통 override 로 남긴다.
# 새 설정에서는 PROFILE_MODEL / REPORT_MODEL 을 각각 지정하는 것을 권장한다.
LEGACY_MODEL = os.getenv("MODEL")
PROFILE_MODEL = os.getenv("PROFILE_MODEL", LEGACY_MODEL or "gpt-4o-mini")
REPORT_MODEL = os.getenv("REPORT_MODEL", LEGACY_MODEL or "gpt-4o-mini")
EVAL_MODEL = os.getenv("EVAL_MODEL", "gpt-4o")
TEMPERATURE = 0.0
# max_retries: 배치 실행 시 TPM 초과(429)가 잦아 SDK 지수 백오프에 맡긴다
MAX_RETRIES = 2


def get_profile_llm(config: RunnableConfig | None = None) -> BaseChatModel:
    """사용자 요구사항 추출 전용. OPENAI_API_KEY 환경변수가 필요하다."""
    return ChatOpenAI(
        model=PROFILE_MODEL,
        temperature=TEMPERATURE,
        max_retries=MAX_RETRIES,
        timeout=40,
    )


def get_report_llm(config: RunnableConfig | None = None) -> BaseChatModel:
    """추천 이유·최종 리포트·현재 요금제 비교 설명 전용."""
    return ChatOpenAI(
        model=REPORT_MODEL,
        temperature=TEMPERATURE,
        max_retries=MAX_RETRIES,
        timeout=40,
    )


def get_llm(config: RunnableConfig | None = None) -> BaseChatModel:
    """기존 단발 설명 API용 호환 별칭. 새 코드에서는 역할별 팩토리를 사용한다."""
    return get_report_llm(config)


def get_eval_llm(config: RunnableConfig | None = None) -> BaseChatModel:
    """조건 검증 전용. 규칙 준수가 중요해서 상위 모델 (mini 는 판정 지시를 무시하는 경향).

    검증은 결과물이 아니라서 오래 기다릴 가치가 없다. 실패하면 검증을 건너뛰고
    추천은 그대로 나가므로(fail-open) 재시도를 줄이고 시간 제한을 짧게 둔다.
    """
    return ChatOpenAI(model=EVAL_MODEL, temperature=TEMPERATURE, max_retries=1, timeout=20)
