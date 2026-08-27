# -*- coding: utf-8 -*-
"""요금제 추천 파이프라인 — 진입점.

    START ──> profiling ──> recommend ──> report ──> evaluation ──> END
                    │            │
                    └─ 재질문 ───┴──────────────────────────────> END
                 ▲             ▲                              │
                 └─────────────┴──── 재시도 (retry_target) ────┘

평가가 미달이면 evaluation 이 retry_target 을 정하고,
피드백이 누적된 채로 그 단계부터 다시 흐른다. (최대 MAX_REVISIONS 회)
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from .agents import evaluation_node, report_node, recommend_node, profiling_node
from .agents.evaluation import MAX_REVISIONS
from .state import PipelineState

__all__ = [
    "build_graph",
    "graph",
    "route_after_profiling",
    "route_after_recommend",
    "route_after_evaluation",
]


def route_after_profiling(state: PipelineState, config: RunnableConfig) -> str:
    profile = state.get("profile")
    return END if profile and profile.needs_user_input else "recommend"


def route_after_recommend(state: PipelineState, config: RunnableConfig) -> str:
    return END if state.get("clarification_question") else "report"


def route_after_evaluation(state: PipelineState, config: RunnableConfig) -> str:
    """종료할지, 어느 단계로 되돌릴지 정한다."""
    ev = state.get("evaluation")

    if ev is None or ev.passed:
        return END
    if state.get("attempt", 0) > MAX_REVISIONS:
        return END  # 재시도 예산 소진 — 미달이어도 지금 결과로 종료
    if ev.retry_target in ("profiling", "recommend", "report"):
        return ev.retry_target
    return END


def build_graph(checkpointer=None):
    builder = StateGraph(PipelineState)

    builder.add_node("profiling", profiling_node)
    builder.add_node("recommend", recommend_node)
    builder.add_node("report", report_node)
    builder.add_node("evaluation", evaluation_node)

    builder.add_edge(START, "profiling")
    builder.add_conditional_edges(
        "profiling",
        route_after_profiling,
        {"recommend": "recommend", END: END},
    )
    builder.add_conditional_edges(
        "recommend",
        route_after_recommend,
        {"report": "report", END: END},
    )
    builder.add_edge("report", "evaluation")
    builder.add_conditional_edges(
        "evaluation",
        route_after_evaluation,
        {"profiling": "profiling", "recommend": "recommend", "report": "report", END: END},
    )

    return builder.compile(name="plan-recommendation", checkpointer=checkpointer)


# agent.run 과 외부에서 가져다 쓰는 컴파일된 그래프
graph = build_graph()
