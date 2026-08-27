# -*- coding: utf-8 -*-
"""파이프라인 전역 상태 + LLM 팩토리.

- PipelineState: 각 단계가 무엇을 읽고 쓰는지의 계약. 새로 합류하면 여기부터 읽는다.
- get_llm / get_eval_llm: 모델을 바꾸려면 이 파일만 고치면 된다.
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

    profile: Optional[UserProfile]      # 1단계가 write
    candidates: list[dict]              # 2단계(a) 하드 필터가 통과시킨 후보 원본
    clarification_question: Optional[str]  # 진행 전 사용자에게 확인할 질문
    ranked: list[ScoredPlan]            # 2단계(b) LLM 랭킹 결과
    report: str                         # 3단계가 write
    evaluation: Optional[Evaluation]    # 4단계가 write

    feedback: Annotated[list[str], operator.add]  # 재시도 피드백 누적
    attempt: int                                  # 리포트를 만든 횟수


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

MODEL = os.getenv("MODEL", "gpt-4o-mini")
EVAL_MODEL = os.getenv("EVAL_MODEL", "gpt-4o")
TEMPERATURE = 0.0
# max_retries: 배치 실행 시 TPM 초과(429)가 잦아 SDK 지수 백오프에 맡긴다
MAX_RETRIES = 8


def get_llm(config: RunnableConfig | None = None) -> BaseChatModel:
    """OPENAI_API_KEY 환경변수 필요."""
    return ChatOpenAI(model=MODEL, temperature=TEMPERATURE, max_retries=MAX_RETRIES)


def get_eval_llm(config: RunnableConfig | None = None) -> BaseChatModel:
    """평가 전용. 규칙 준수가 중요해서 상위 모델 (mini 는 판정 지시를 무시하는 경향)."""
    return ChatOpenAI(model=EVAL_MODEL, temperature=TEMPERATURE, max_retries=MAX_RETRIES)
