"""2단계 — Plan Matching & Ranking Agent.

권장 구조는 두 겹:
  (a) 코드로 하드 필터  — 예산/통신사 같은 절대 조건 (LLM에 맡기지 않는다)
  (b) LLM 으로 랭킹     — 남은 후보를 프로필에 맞춰 점수화·정렬
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from ..state import PipelineState


def matching_node(state: PipelineState, config: RunnableConfig) -> dict:
    profile = state.get("profile")
    feedback = state.get("feedback", [])

    # TODO: 구현
    # candidates = hard_filter(profile)                       # (a) 데이터 소스 조회 + 규칙 필터
    # llm = get_llm(config).with_structured_output(RankingResult)
    # result = llm.invoke([SystemMessage(content=MATCHING_PROMPT.format(...))])
    # ranked = [p for p in result.plans if p.plan_id in {c["plan_id"] for c in candidates}]
    ranked = []

    return {
        "ranked": ranked,
        "messages": [AIMessage(content="[matching] TODO", name="matching")],
    }
