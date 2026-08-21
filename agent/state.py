"""파이프라인 전역 상태 — 각 단계가 무엇을 읽고 쓰는지의 계약."""

from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from .schemas import Evaluation, ScoredPlan, UserProfile


class PipelineState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]

    profile: Optional[UserProfile]      # 1단계가 write
    ranked: list[ScoredPlan]            # 2단계가 write
    report: str                         # 3단계가 write
    evaluation: Optional[Evaluation]    # 4단계가 write

    feedback: Annotated[list[str], operator.add]  # 재시도 피드백 누적
    attempt: int                                  # 리포트를 만든 횟수
