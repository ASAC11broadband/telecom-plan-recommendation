# -*- coding: utf-8 -*-
"""요금제 추천 4단계 파이프라인 (참고 구현).

기존 test/ 의 6개 에이전트를 telecom-plan-recommendation 의 4단계 구조로 재정리한 것.
컴파일된 그래프는 `from agent.graph import graph` 로 가져온다.
(패키지에서 graph 를 재export 하면 서브모듈 agent.graph 를 가리므로 하지 않는다.)
"""

from .graph import build_graph

__all__ = ["build_graph"]
