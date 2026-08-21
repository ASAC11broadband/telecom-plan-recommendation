"""1단계 — User Profiling Agent.

사용자 발화(+재시도 피드백)를 읽어 UserProfile 로 구조화한다.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from ..schemas import UserProfile
from ..state import PipelineState


def profiling_node(state: PipelineState, config: RunnableConfig) -> dict:
    feedback = state.get("feedback", [])  # 재시도면 여기에 평가자 피드백이 들어있다

    # TODO: 구현
    # prompt = PROFILING_PROMPT + ("\n[피드백]\n" + "\n".join(feedback) if feedback else "")
    # llm = get_llm(config).with_structured_output(UserProfile)
    # profile = llm.invoke([SystemMessage(content=prompt), *state["messages"]])
    profile = UserProfile()

    return {
        "profile": profile,
        "messages": [AIMessage(content="[profiling] TODO", name="profiling")],
    }
