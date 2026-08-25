# -*- coding: utf-8 -*-
"""CLI 러너.

    python -m agent.run "데이터 무제한, 유튜브 프리미엄 포함, 월 7만원 이하"
"""

from __future__ import annotations

import sys

from langchain_core.messages import HumanMessage

from .graph import graph
from .state import PipelineState

DEFAULT_QUERY = "데이터 무제한, 유튜브 프리미엄 포함, 월 7만원 이하"


def run(query: str) -> PipelineState:
    print(f"\n{'=' * 60}\n질문: {query}\n{'=' * 60}")
    state = graph.invoke({"messages": [HumanMessage(content=query)]})

    profile = state.get("profile")
    ev = state.get("evaluation")
    print(f"\n[프로파일] {profile.model_dump(exclude_none=True) if profile else None}")
    print(f"[후보 수] {len(state.get('candidates', []))}")
    if state.get("recommend_note"):
        print(f"[추천 노트] {state['recommend_note']}")
    print(
        f"[평가] passed={ev.passed if ev else None} attempt={state.get('attempt')}"
        + (f" feedback={ev.feedback}" if ev and ev.feedback else "")
    )
    print(f"\n{state.get('report', '')}\n")
    return state


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY)
