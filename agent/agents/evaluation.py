# -*- coding: utf-8 -*-
"""4단계 — Evaluation Agent.

두 겹으로 나뉜다:
  (a) 코드 검증 — 환각·Hard Constraint 위반·중복 추천·순위 역전·리포트 누락처럼
                  결정적으로 판정 가능한 것. LLM 판정은 여기서 오탐이 잦아 코드로 못 박는다.
  (b) LLM 검증  — 리포트 서술이 후보 데이터와 모순되는지(없는 혜택, 틀린 숫자)만.
                  (a)가 이미 잡은 조건 충족 여부는 판단하지 못하게 막는다.

미달이면 retry_target 을 정하고, 피드백은 state["feedback"] 에 누적되어
재실행되는 단계의 프롬프트에 주입된다.

[읽기] profile, candidates, ranked, report, attempt   [쓰기] evaluation, attempt, feedback
"""

from __future__ import annotations

import json
import math

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from ..data import find_candidate, has_benefit, slim
from ..schemas import Evaluation, UserProfile
from ..state import PipelineState, get_eval_llm

MAX_REVISIONS = 2  # 재시도 최대 횟수 (graph.route_after_evaluation 과 짝)

# 주의: 템플릿에 중괄호를 직접 쓰지 말 것 (.format 이 깨진다).
PROMPT = """추천 리포트가 후보 데이터와 모순되는 주장(없는 혜택, 틀린 요금·데이터량)을 하는지만 검증해라.
조건 충족 여부, 중복 추천, 순위 정합성, 할인가 표기는 이미 별도 코드로 검증됐으니 절대 판단하지 마라.
용어 정의: '무제한'은 소진 후 속도제한형을 포함한다(모순 아님).
정상가는 monthly_fee, 할인 후 요금은 discounted_fee 다.
둘 중 어느 것도 아닌 금액을 쓰거나 할인이 영구적이라고 단언할 때만 불합격이다.
할인 기간이 비어 있다는 이유만으로는 불합격시키지 마라(리포트가 확인을 안내하면 충분하다).
숫자가 후보 데이터와 일치하면 합격이다.
불합격이면 retry_target 을 정해라 — 조건 추출 자체가 틀렸으면 profiling,
고른 요금제가 잘못됐으면 recommend, 요금제는 맞는데 리포트 서술·표기만 문제면 report.
추천된 요금제 데이터: {candidates}
리포트: {report}"""


def _benefit_text(plan: dict) -> str:
    return f"{plan.get('ott_options') or ''} | " + " | ".join(plan.get("included_benefits") or [])


# UserProfile 의 hard_constraints 필드명 → 후보 dict 검증식.
# data.py 의 filter_candidates 와 짝이지만 판정은 한 단계 느슨하게 둔다(오탐이 재시도를 태우므로).
CONSTRAINT_CHECKS = {
    "budget_min_won": lambda plan, v: plan["discounted_fee"] >= v,
    "budget_max_won": lambda plan, v: plan["discounted_fee"] <= v,
    # 무제한은 data_gb 가 null 이라 수치 비교가 성립하지 않는다 → 충족으로 본다.
    "min_data_gb": lambda plan, v: plan["data_unlimited"] or (plan.get("data_gb") or 0) >= v,
    "data_unlimited": lambda plan, v: plan["data_unlimited"] or not v,  # v=False 는 "필수 아님"
    "min_qos_mbps": lambda plan, v: (plan.get("qos_mbps") or 0) >= v,
    "min_tethering_gb": lambda plan, v: (plan.get("tethering_gb") or 0) >= v,
    "min_voice_minutes": lambda plan, v: plan["voice_unlimited"] or (plan.get("voice_minutes") or 0) >= v,
    "voice_unlimited": lambda plan, v: plan["voice_unlimited"] or not v,  # v=False 는 "필수 아님"
    "sms_unlimited": lambda plan, v: plan["sms_unlimited"] or not v,  # v=False 는 "필수 아님"
    "carrier_type": lambda plan, v: plan.get("carrier_type") == v,
    "host_mno": lambda plan, v: plan.get("host_mno") == v,
    "network_gen": lambda plan, v: plan.get("network_gen") == v,
    # 빈 값은 "가입 조건 없음"(누구나 가입) 이라 조건 위반이 아니다. data.filter_candidates 와 같은 규칙.
    "age_condition": lambda plan, v: plan.get("age_condition") in ("", None, v),
    "mvno_brand": lambda plan, v: str(plan.get("mvno_brand") or "").strip().casefold()
    == str(v).strip().casefold(),
    "wanted_benefits": lambda plan, v: all(
        has_benefit(_benefit_text(plan), benefit) for benefit in v
    ),
    "min_discount_period_months": lambda plan, v: (plan.get("discount_period_months") or 0) >= v,
}


def _constraint_errors(profile: UserProfile | None, plan: dict) -> list[str]:
    """profiling 이 확정한 Hard Constraint 를 추천 결과에 재적용한다."""
    if profile is None:
        return []

    errors: list[str] = []
    for field in profile.hard_constraints:
        check = CONSTRAINT_CHECKS.get(field)
        value = getattr(profile, field, None)
        if check is None or value is None:
            continue
        if not check(plan, value):
            errors.append(f"'{plan['plan_name']}'이 필수 조건 {field}={value} 위반")
    return errors


# 우선순위 축별 정렬값. 클수록 좋은 값으로 통일한다.
PRIORITY_VALUES = {
    "price": lambda plan: -plan["discounted_fee"],
    "data": lambda plan: math.inf if plan["data_unlimited"] else float(plan.get("data_gb") or 0),
    "qos": lambda plan: float(plan.get("qos_mbps") or 0),
    "voice": lambda plan: math.inf if plan["voice_unlimited"] else float(plan.get("voice_minutes") or 0),
    "benefit": lambda plan: float(len(plan.get("included_benefits") or [])),
}


def _ranking_errors(profile: UserProfile | None, ranked: list, rows: list[dict]) -> list[str]:
    """점수 역전, 중복 추천, 명백한 우선순위 위반을 잡는다."""
    errors: list[str] = []

    scores = [plan.score for plan in ranked]
    if any(earlier < later for earlier, later in zip(scores, scores[1:])):
        errors.append(f"순위와 score 가 어긋남: {scores}")

    names = [plan.plan_name for plan in ranked]
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        errors.append(f"같은 요금제가 여러 순위를 차지함: {', '.join(duplicated)}")

    # ponytail: 1위가 추천 목록 안에서 해당 축 '최하위'일 때만 위반으로 본다.
    # 축별 가중치까지 판정하면 오탐이 재시도 예산을 태우므로 하지 않는다.
    priorities = (profile.priorities if profile else None) or []
    axis = next((p for p in priorities if p in PRIORITY_VALUES), None)
    if axis and len(rows) > 1:
        values = [PRIORITY_VALUES[axis](row) for row in rows]
        if values[0] < min(values[1:]):
            errors.append(f"우선순위 1순위가 {axis} 인데 1위 요금제가 추천 목록 중 최하위")
    return errors


def _report_errors(report: str, ranked: list, rows: list[dict]) -> list[str]:
    """리포트가 추천 결과를 실제로 담고 있는지만 본다."""
    errors: list[str] = []
    if "|" not in report:
        errors.append("리포트에 비교 표가 없음")
    missing = [plan.plan_name for plan in ranked if plan.plan_name not in report]
    if missing:
        errors.append(f"추천 요금제가 리포트에서 빠짐: {', '.join(missing)}")

    # 예산은 discounted_fee 로 판정된다. 리포트가 monthly_fee 만 쓰면 실납부 2,200원짜리가
    # 25,520원으로 나가 예산 초과처럼 보인다. 할인가가 본문에 있는지만 확인한다.
    no_price = [row["plan_name"] for row in rows if f"{row['discounted_fee']:,}" not in report]
    if no_price:
        errors.append(f"리포트에 할인가(실납부액)가 없음: {', '.join(no_price)}")
    return errors


def _code_checks(state: PipelineState) -> tuple[list[str], list[dict]]:
    """(a) 결정적 검증. (위반 사유 목록, 추천된 후보 원본) 을 돌려준다."""
    profile = state.get("profile")
    candidates = state.get("candidates", [])
    ranked = state.get("ranked", [])

    errors: list[str] = []
    rows: list[dict] = []

    for plan in ranked:
        row = find_candidate(candidates, plan.plan_id)
        if row is None:
            errors.append(f"'{plan.plan_name}'은 후보 목록에 없음(환각)")
            continue
        rows.append(row)
        errors.extend(_constraint_errors(profile, row))

    if len(rows) == len(ranked):  # 환각이 없을 때만 순위·리포트를 볼 의미가 있다
        errors.extend(_ranking_errors(profile, ranked, rows))
        errors.extend(_report_errors(state.get("report", ""), ranked, rows))
    return errors, rows


def evaluation_node(state: PipelineState, config: RunnableConfig) -> dict:
    attempt = state.get("attempt", 0) + 1  # 이번 평가까지 포함한 시도 횟수

    if not state.get("ranked"):
        # 조건을 만족하는 요금제가 없는 것은 데이터의 사실이라 재시도로 해결되지 않는다.
        # retry_target="none" 이면 graph 가 재시도 없이 종료시킨다.
        ev = Evaluation(
            passed=False,
            feedback="조건을 만족하는 요금제가 없어 추천을 생성하지 못했습니다. 조건 완화가 필요합니다.",
            retry_target="none",
        )
        return {
            "evaluation": ev,
            "attempt": attempt,
            "messages": [
                AIMessage(content=f"[evaluation] passed=False target=none {ev.feedback}", name="evaluation")
            ],
        }

    errors, rows = _code_checks(state)
    if errors:
        ev = Evaluation(passed=False, feedback="; ".join(errors), retry_target="recommend")
    else:
        prompt = PROMPT.format(
            candidates=json.dumps(slim(rows), ensure_ascii=False),
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
    if not ev.passed and ev.retry_target != "none" and attempt <= MAX_REVISIONS:
        update["feedback"] = [f"[{attempt}차] {ev.feedback}"]

    return update


if __name__ == "__main__":
    from ..schemas import ScoredPlan

    def _plan(plan_id, name, fee=30000, **kw):
        row = {
            "plan_id": plan_id,
            "plan_name": name,
            "discounted_fee": fee,
            "monthly_fee": fee,
            "discounted_fee": fee,
            "data_gb": 50.0,
            "data_unlimited": False,
            "qos_mbps": 1.0,
            "tethering_gb": 10.0,
            "voice_minutes": 300,
            "voice_unlimited": False,
            "sms_unlimited": True,
            "carrier_type": "MNO",
            "host_mno": "KT",
            "mvno_brand": "",
            "network_gen": "5G",
            "age_condition": "",
            "ott_options": "",
            "included_benefits": [],
            "discount_period_months": 12,
        }
        row.update(kw)
        return row

    def _state(cands, ranked, report, profile):
        return {"profile": profile, "candidates": cands, "ranked": ranked, "report": report}

    ok_profile = UserProfile(budget_max_won=40000, hard_constraints=["budget_max_won"])
    a, b = _plan("1", "A"), _plan("2", "B", fee=35000)
    ranked = [
        ScoredPlan(plan_id="1", plan_name="A", score=90, reason=""),
        ScoredPlan(plan_id="2", plan_name="B", score=80, reason=""),
    ]
    report = "| 순위 | 요금제 | 월 요금 |\n| 1 | A | 30,000원 |\n| 2 | B | 35,000원 |"

    # 정상: plan_id 로 후보를 찾고 조건도 만족 → 위반 없음
    errors, rows = _code_checks(_state([a, b], ranked, report, ok_profile))
    assert errors == [], errors
    assert len(rows) == 2

    # 환각: 후보에 없는 plan_id
    errors, _ = _code_checks(_state([a], ranked, report, ok_profile))
    assert any("환각" in e for e in errors), errors

    # Hard Constraint 위반: 예산 초과
    errors, _ = _code_checks(_state([a, _plan("2", "B", fee=99000)], ranked, report, ok_profile))
    assert any("budget_max_won" in e for e in errors), errors

    # 중복 추천: plan_id 는 다른데 plan_name 이 같음
    dup = [
        ScoredPlan(plan_id="1", plan_name="A", score=90, reason=""),
        ScoredPlan(plan_id="2", plan_name="A", score=80, reason=""),
    ]
    errors, _ = _code_checks(_state([a, _plan("2", "A")], dup, "| A | 30,000원 |", ok_profile))
    assert any("여러 순위" in e for e in errors), errors

    # score 역전
    reversed_scores = [
        ScoredPlan(plan_id="1", plan_name="A", score=70, reason=""),
        ScoredPlan(plan_id="2", plan_name="B", score=95, reason=""),
    ]
    errors, _ = _code_checks(_state([a, b], reversed_scores, report, ok_profile))
    assert any("score" in e for e in errors), errors

    # 우선순위 위반: price 1순위인데 1위가 더 비쌈
    price_profile = UserProfile(priorities=["price"])
    errors, _ = _code_checks(_state([_plan("1", "A", fee=39000), b], ranked, report, price_profile))
    assert any("최하위" in e for e in errors), errors

    # 리포트 누락
    errors, _ = _code_checks(
        _state([a, b], ranked, "| 순위 | 월 요금 |\n| 1 | A | 30,000원 |", ok_profile)
    )
    assert any("빠짐" in e for e in errors), errors

    # 할인가 누락: 정가만 적힌 리포트 (실납부 2,200원짜리가 25,520원으로 나가던 실제 사고)
    promo = _plan("2", "B", fee=25520, discounted_fee=2200)
    promo_ranked = [
        ScoredPlan(plan_id="1", plan_name="A", score=90, reason=""),
        ScoredPlan(plan_id="2", plan_name="B", score=80, reason=""),
    ]
    regular_only = "| 순위 | 요금제 | 월 요금 |\n| 1 | A | 30,000원 |\n| 2 | B | 25,520원 |"
    errors, _ = _code_checks(_state([a, promo], promo_ranked, regular_only, UserProfile()))
    assert any("할인가" in e for e in errors), errors

    with_discounted = regular_only.replace("25,520원", "2,200원 (정가 25,520원)")
    errors, _ = _code_checks(_state([a, promo], promo_ranked, with_discounted, UserProfile()))
    assert errors == [], errors

    # slim: 필터용 파생 필드는 안 넘어간다
    assert "data_gb" not in slim([a])[0] and slim([a])[0]["plan_name"] == "A"

    print("self-check ok")
