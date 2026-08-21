"""4단계 — Evaluation / Feedback Agent.

리포트를 채점하고, 미달이면 어느 단계로 되돌릴지(retry_target) 정한다.
피드백은 state["feedback"] 에 누적되어 재실행되는 단계 프롬프트에 주입된다.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from ..schemas import Evaluation
from ..state import PipelineState

MAX_REVISIONS = 2  # 재시도 최대 횟수 (graph.route_after_evaluation 과 짝)


def evaluation_node(state: PipelineState, config: RunnableConfig) -> dict:
    attempt = state.get("attempt", 0) + 1  # 이번 평가까지 포함한 시도 횟수

    # TODO: 구현
    # llm = get_llm(config).with_structured_output(Evaluation)
    # ev = llm.invoke([SystemMessage(content=EVALUATION_PROMPT.format(...))])
    ev = Evaluation(passed=True)

    update: dict = {
        "evaluation": ev,
        "attempt": attempt,
        "messages": [AIMessage(content="[evaluation] TODO", name="evaluation")],
    }

    # 재시도가 실제로 일어날 때만 피드백을 남긴다 (라우터와 같은 조건)
    if not ev.passed and attempt <= MAX_REVISIONS:
        update["feedback"] = [f"[{attempt}차] {ev.feedback}"]

    return update
