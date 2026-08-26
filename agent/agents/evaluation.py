# -*- coding: utf-8 -*-
"""4단계 — Evaluation Agent.

두 겹으로 나뉜다:
  (a) 코드 검증 — 예산 초과·환각(후보에 없는 요금제)처럼 결정적으로 판정 가능한 것.
                  LLM 판정은 여기서 오탐이 잦아 코드로 못 박는다.
  (b) LLM 검증  — 리포트 서술이 후보 데이터와 모순되는지(없는 혜택, 틀린 숫자)만.
                  (a)가 이미 잡은 예산·조건 충족 여부는 판단하지 못하게 막는다.

미달이면 retry_target 을 정하고, 피드백은 state["feedback"] 에 누적되어
재실행되는 단계의 프롬프트에 주입된다.

[읽기] profile, candidates, ranked, report, attempt   [쓰기] evaluation, attempt, feedback
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from ..data import find_candidate, slim
from ..schemas import Evaluation
from ..state import PipelineState, get_eval_llm

MAX_REVISIONS = 2  # 재시도 최대 횟수 (graph.route_after_evaluation 과 짝)

# 주의: 템플릿에 중괄호를 직접 쓰지 말 것 (.format 이 깨진다).
PROMPT = """추천 리포트가 후보 데이터와 모순되는 주장(없는 혜택, 틀린 요금·데이터량)을 하는지만 검증해라.
예산·조건 충족 여부는 이미 별도 코드로 검증됐으니 절대 판단하지 마라.
용어 정의: '무제한'은 소진 후 속도제한형을 포함한다(모순 아님).
예산 판단 기준 요금은 discounted_fee(할인 후)다.
숫자가 후보 데이터와 일치하면 합격이다.
추천 노트가 있으면 리포트가 조건 완화를 정직하게 설명했는지도 확인해라.
불합격이면 retry_target 을 정해라 — 조건 추출 자체가 틀렸으면 profiling, 후보·랭킹·서술이 문제면 recommend.
{recommend_note}후보 데이터: {candidates}
리포트: {report}"""


def _code_checks(state: PipelineState) -> list[str]:
    """(a) 결정적 검증 — 위반 사유 목록. 비어 있으면 통과."""
    budget = state["profile"].budget_max_won if state.get("profile") else None
    candidates = state.get("candidates", [])
    errors: list[str] = []

    for r in state.get("ranked", []):
        c = find_candidate(candidates, r.plan_name)
        if c is None:
            errors.append(f"'{r.plan_name}'은 후보 목록에 없음(환각)")
        elif budget and c["discounted_fee"] > budget:
            errors.append(
                f"'{r.plan_name}' 할인 후 요금 {c['discounted_fee']}원이 예산 {budget}원 초과"
            )
    return errors


def evaluation_node(state: PipelineState, config: RunnableConfig) -> dict:
    attempt = state.get("attempt", 0) + 1  # 이번 평가까지 포함한 시도 횟수

    errors = _code_checks(state)
    if errors:
        ev = Evaluation(passed=False, feedback="; ".join(errors), retry_target="recommend")
    else:
        note = state.get("recommend_note", "")
        prompt = PROMPT.format(
            recommend_note=f"추천 노트: {note}\n" if note else "",
            candidates=json.dumps(slim(state.get("candidates", [])), ensure_ascii=False),
            report=state.get("report", ""),
        )
        llm = get_eval_llm(config).with_structured_output(Evaluation)
        ev = llm.invoke(prompt)
        if not ev.passed and ev.retry_target == "none":
            ev.retry_target = "recommend"  # 불합격인데 대상을 안 고르면 기본값

    update: dict = {
        "evaluation": ev,
        "attempt": attempt,
        "messages": [
            AIMessage(
                content=f"[evaluation] passed={ev.passed} target={ev.retry_target} {ev.feedback}",
                name="evaluation",
            )
        ],
    }

    # 재시도가 실제로 일어날 때만 피드백을 남긴다 (라우터와 같은 조건)
    if not ev.passed and attempt <= MAX_REVISIONS:
        update["feedback"] = [f"[{attempt}차] {ev.feedback}"]

    return update
