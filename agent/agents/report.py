"""3단계 — Explanation & Report Agent.

랭킹 결과를 사용자가 읽을 리포트로 바꾼다.
구조화 출력이 아니라 자연어 산출물이 목적인 유일한 단계.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from ..state import PipelineState


def report_node(state: PipelineState, config: RunnableConfig) -> dict:
    profile = state.get("profile")
    ranked = state.get("ranked", [])
    feedback = state.get("feedback", [])

    # TODO: 구현
    # report = get_llm(config).invoke(
    #     [SystemMessage(content=REPORT_PROMPT.format(...))]
    # ).content
    report = "[report] TODO"

    return {
        "report": report,
        "messages": [AIMessage(content=report, name="report")],
    }
