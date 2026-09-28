# -*- coding: utf-8 -*-
"""요금제 추천 파이프라인 — 진입점.

    START ──> profiling ──> profile_check ──> recommend ──> report ──> evaluation ──> END
                  │               │                │           ▲            │
         재질문 ──┴───────────────┴────────────────┴──> END    └─ 1회 재작성 ┘

profiling 이 추가 질문을 남겨도 대개 멈추지 않는다. 후보를 먼저 보여주고
질문은 profile.followup_question 으로 함께 내보낸다.
데이터·요금 신호가 모두 없거나 '혜택이 좋은'의 주관적 기준이 없는 경우에는
recommend 로 가지 않고 필요한 조건을 먼저 질문한다.

Evaluation Agent 는 두 곳에서 검증한다(agent/agents/evaluation.py).
profile_check 는 추출 조건을 발화와 대조해 그 자리에서 고치고, evaluation 은 카드 문장을
검사해 report 를 한 번 다시 쓰게 하거나 문장을 뺀다. recommend 로는 되돌리지 않는다.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from .agents import evaluation_node, profile_check_node, report_node, recommend_node, profiling_node
from .agents.evaluation import MAX_REVISIONS
from .agents.profiling import benefit_preference_missing, core_signal_missing
from .state import PipelineState

__all__ = [
    "build_graph",
    "graph",
    "route_after_profiling",
    "route_after_recommend",
    "route_after_evaluation",
]


def route_after_profiling(state: PipelineState, config: RunnableConfig) -> str:
    """핵심 추천 조건이나 주관적인 혜택 기준이 없으면 먼저 되묻는다."""
    profile = state.get("profile")
    return END if core_signal_missing(profile) or benefit_preference_missing(profile) else "recommend"


def route_after_recommend(state: PipelineState, config: RunnableConfig) -> str:
    return END if state.get("clarification_question") else "report"


def route_after_evaluation(state: PipelineState, config: RunnableConfig) -> str:
    """설명 검증에 걸렸으면 report 를 한 번 다시 쓴다. 그 밖에는 종료."""
    ev = state.get("evaluation")

    if ev is None or ev.passed:
        return END
    if state.get("attempt", 0) > MAX_REVISIONS:
        return END  # 재시도 예산 소진 — evaluation 이 다음 시도에서 문장을 빼고 통과시킨다
    return "report" if ev.retry_target == "report" else END


def build_graph(checkpointer=None):
    builder = StateGraph(PipelineState)

    builder.add_node("profiling", profiling_node)
    builder.add_node("profile_check", profile_check_node)
    builder.add_node("recommend", recommend_node)
    builder.add_node("report", report_node)
    builder.add_node("evaluation", evaluation_node)

    builder.add_edge(START, "profiling")
    builder.add_conditional_edges(
        "profiling",
        route_after_profiling,
        {"recommend": "profile_check", END: END},
    )
    # 조건 검증이 조건을 지워 신호가 사라졌을 수 있어 같은 기준으로 한 번 더 본다.
    builder.add_conditional_edges(
        "profile_check",
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
        {"report": "report", END: END},
    )

    return builder.compile(name="plan-recommendation", checkpointer=checkpointer)


# agent.run 과 외부에서 가져다 쓰는 컴파일된 그래프
graph = build_graph()
