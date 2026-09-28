# -*- coding: utf-8 -*-
"""4단계 — Evaluation Agent. 두 지점에서 검증하고, 틀리면 직접 고친다.

    profiling ──> ① profile_check ──> recommend ──> report ──> ② evaluation ──> END
                                                       ▲                │
                                                       └── 1회 재작성 ──┘

① 조건 검증(profile_check_node) — 추천 전에 사용자 발화와 추출 조건을 대조한다.
   LLM 이 문제를 고르고(뺀 조건이 남음·값이 틀림·빠짐·희망이 필수로 바뀜), 코드가 인용이
   실제 발화에 있는지 확인한 것만 profile 에 바로 적용한다. profiling 을 다시 돌리지 않는다.
   오류 대부분이 LLM 뒤의 정규식 보정에서 나와서 다시 돌려도 같은 값이 나온다.
② 설명 검증(evaluation_node) — 화면에 나가는 카드 문장(ranked[].reason)을 코드로 검사한다.
   데이터에 없는 금액, 현재보다 비싼데 싸다는 말, 근거 없는 약속. 걸리면 report 를 1회 다시
   쓰게 하고, 그래도 걸리면 그 문장만 뺀다.

recommend 로는 되돌리지 않는다. 같은 입력이면 같은 결과가 나오는 단계라 재시도가 의미 없다.
plan_id·필수 조건·순위 검사(_code_checks)는 그 불변식이 깨졌는지 로그로만 남긴다.
검증 LLM 이 실패해도 추천은 그대로 나간다(fail-open). 화면은 evaluation.passed 만 읽는다.

[읽기] messages, profile, candidates, ranked, report, reference, attempt
[쓰기] profile, clarification_question, evaluation, attempt, feedback, ranked, report, eval_log
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from ..data import (
    BENEFIT_CATEGORY_ALIASES,
    CONSTRAINT_LABELS,
    age_eligible,
    benefit_requests_match,
    filter_candidates,
    find_candidate,
    has_benefit,
)
from ..mcda import COMPARE_MONTHS, _switching_monthly_fee
from ..schemas import Evaluation, ProfileCheck, ProfileIssue, UserProfile
from ..state import PipelineState, get_eval_llm
from .profiling import (
    _BENEFIT_FIELDS,
    _BENEFIT_MUST_RE,
    _BENEFIT_WISH_RE,
    _CURRENT_FEE_PREFIX_RE,
    _CURRENT_FEE_SUFFIX_RE,
    CONSTRAINT_FIELDS,
    CORE_MISSING_QUESTION,
    _apply_relaxed_fields,
    core_signal_missing,
)
from .report import _card_reason, _ranked_recommendations, split_sentences

MAX_REVISIONS = 1  # 설명 검증 재작성 횟수 (graph.route_after_evaluation 과 짝)

_logger = logging.getLogger(__name__)

# 주의: 템플릿에 중괄호를 직접 쓰지 말 것 (.format 이 깨진다).
PROFILE_CHECK_PROMPT = """너는 요금제 상담의 조건 검증자다. 사용자 발화와, 코드가 발화에서 뽑은 추천 조건을
대조해 틀린 것만 보고하라.

[판정 종류]
- retracted: 사용자가 뒤 발화에서 빼거나 취소한 조건이 아직 남아 있다.
  source_turn/source_quote 에 조건을 처음 건 원문, turn/quote 에 빼 달라고 한 원문을 적는다.
- wrong_value: 조건 값이 사용자가 말한 값과 다르다. value 에 사용자가 말한 값을 숫자로(원·GB·분).
- missed: 사용자가 분명히 말한 조건이 빠졌다. value 에 숫자로.
- soften: 혜택을 '있으면 좋겠다/가능하면'처럼 희망으로 말했는데 필수 조건(hard_constraints)에 들어 있다.
- phantom: 사용자가 말한 적 없는 조건이 들어 있다.

[규칙]
- quote·source_quote 는 사용자 발화에서 글자 그대로 복사한다. 고쳐 쓰거나 합치지 마라.
- turn 은 아래 사용자 발화의 [번호]다.
- 뒤 발화가 앞 발화를 덮는다.
- 지금 내고 있는 요금은 예산이 아니다.
- 코드가 표준값으로 바꾼 것은 틀린 것이 아니다. 'N만원대'는 N0,000~N9,999, 'N만원 미만'은 N만원-1,
  '청년'은 만 34세 이하, 앱·화질 사용량에서 계산한 데이터·속도 값이 그렇다.
- 확신이 없으면 보고하지 마라. 문제가 없으면 빈 목록을 돌려준다.

[사용자 발화]
{turns}

[추출된 조건]
{profile}"""


def _matches_requested_benefits(profile: UserProfile, plan: dict) -> bool:
    """서비스명·카테고리 조건을 프로필의 AND/OR 방식으로 함께 검증한다."""
    return benefit_requests_match(
        plan,
        profile.wanted_benefits or [],
        profile.wanted_benefit_categories or [],
        profile.benefit_match_mode,
    )


# UserProfile 의 hard_constraints 필드명 → 후보 dict 검증식.
# data.py 의 filter_candidates 와 짝이지만 판정은 한 단계 느슨하게 둔다(오탐이 재시도를 태우므로).
CONSTRAINT_CHECKS = {
    "budget_min_won": lambda plan, v: plan["discounted_fee"] >= v,
    "budget_max_won": lambda plan, v: plan["discounted_fee"] <= v,
    # 무제한은 data_gb 가 null 이라 수치 비교가 성립하지 않는다 → 충족으로 본다.
    "min_data_gb": lambda plan, v: plan["data_unlimited"] or (plan.get("data_gb") or 0) >= v,
    "max_data_gb": lambda plan, v: not plan["data_unlimited"] and (
        plan.get("data_gb") if plan.get("data_gb") is not None else math.inf
    ) <= v,
    # '무제한' 요청은 완전 무제한과 쓸 만한 QoS형을 함께 받는다(agent.data 참고).
    # require_full_unlimited 가 붙은 경우만 완전 무제한으로 좁힌다.
    "data_unlimited": lambda plan, v: plan.get("effective_unlimited", plan["data_unlimited"]) or not v,
    "require_full_unlimited": lambda plan, v: plan["data_unlimited"] or not v,
    "min_qos_mbps": lambda plan, v: (plan.get("qos_mbps") or 0) >= v,
    "requires_qos": lambda plan, v: (plan.get("qos_mbps") or 0) > 0 or not v,
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
    "min_discount_period_months": lambda plan, v: (plan.get("discount_period_months") or 0) >= v,
}


def _constraint_errors(profile: UserProfile | None, plan: dict) -> list[str]:
    """profiling 이 확정한 Hard Constraint 를 추천 결과에 재적용한다."""
    if profile is None:
        return []

    errors: list[str] = []
    # 가입 자격은 hard_constraints 에 없어도 항상 본다. 자격 없는 상품을 추천하면
    # 사용자는 가입 단계에서 튕긴다(키즈 요금제가 성인에게 1순위로 나온 적이 있다).
    if not age_eligible(plan.get("age_condition"), profile.user_age):
        errors.append(
            f"'{plan['plan_name']}'은 가입 대상이 '{plan.get('age_condition')}'인데 "
            f"사용자 나이({profile.user_age or '미상'})로 자격을 확인할 수 없음"
        )

    benefit_fields = {"wanted_benefits", "wanted_benefit_categories"}
    if benefit_fields.intersection(profile.hard_constraints):
        if not _matches_requested_benefits(profile, plan):
            errors.append(
                f"'{plan['plan_name']}'이 필수 혜택 조건 "
                f"({profile.benefit_match_mode})을 만족하지 않음"
            )
    for field in profile.hard_constraints:
        if field in benefit_fields:
            continue
        check = CONSTRAINT_CHECKS.get(field)
        value = getattr(profile, field, None)
        if check is None or value is None:
            continue
        if not check(plan, value):
            errors.append(f"'{plan['plan_name']}'이 필수 조건 {field}={value} 위반")
    return errors


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

    # 다기준 순위는 한 축의 최솟값/최댓값과 다를 수 있다. SMAA-2 결과를
    # 단일 가격/데이터 축으로 다시 판정하면 정상 결과도 무한 재추천하게 된다.
    ranks = [plan.expected_rank for plan in ranked]
    if all(value is not None for value in ranks) and any(a > b for a, b in zip(ranks, ranks[1:])):
        errors.append("SMAA-2 기대순위와 출력 순서가 어긋남")
    return errors


def _report_errors(report: str, ranked: list, rows: list[dict]) -> list[str]:
    """리포트가 추천 결과를 실제로 담고 있는지만 본다."""
    errors: list[str] = []
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


def _log(entry: dict) -> dict:
    """검증 기록을 state 와 서버 로그에 남긴다. 서버 로그에는 발화·문장 원문을 싣지 않는다.

    무엇이든 고쳤거나 걸렸으면 WARNING 이다. 앞 단계(추출·설명 작성)가 틀렸다는 뜻이라
    운영 로그에서 바로 보여야 한다. 아무 일도 없으면 INFO.
    """
    public = {
        key: [{k: v for k, v in item.items() if k not in ("quote", "sentence")} for item in value]
        if key in ("issues", "violations") else value
        for key, value in entry.items()
    }
    acted = entry.get("violations") or entry.get("action") == "error" or any(
        not str(item.get("action", "")).startswith(("skip", "logged")) for item in entry.get("issues", [])
    )
    _logger.log(logging.WARNING if acted else logging.INFO, "evaluation %s",
                json.dumps(public, ensure_ascii=False, default=str))
    return {"eval_log": [entry]}


# ─────────────────────── 금액·수치 읽기 ───────────────────────

# 한국어 금액. '4만 6,200원'·'2만 8천 6백원'·'1만 5천원'·'1.5만원'·'13,990원'·'만 원'. bare 는 단위 없는
# 1,000 이상의 수(조건 발화의 '예산 30000 이하'용). 카드 문장 검사는 bare 를 쓰지 않고, '5만 포인트'처럼
# '원'이 없는 만·천 표기도 금액으로 보지 않는다.
_WON_RE = re.compile(
    r"(?P<man>\d+(?:\.\d+)?)\s*만\s*(?:(?P<mc>\d+)\s*천\s*)?(?:(?P<mb>\d+)\s*백\s*)?"
    r"(?:(?P<mr>\d{1,3}(?:,\d{3})|\d{1,4})(?=\s*원))?원?"
    r"|(?P<cheon>\d+)\s*천\s*(?:(?P<cb>\d+)\s*백\s*)?(?:(?P<cr>\d{1,3})(?=\s*원))?원?"
    r"|(?P<won>\d{1,3}(?:,\d{3})+|\d+)\s*원"
    r"|(?P<bare>\d{1,3}(?:,\d{3})+|\d{4,})(?!\d)(?!\s*(?:gb|g\b|기가|mb|분|개월|일|세|살|%|mbps|년))"
    r"|(?<![\d가-힣])(?P<one_man>만)\s*원",
    re.IGNORECASE,
)
_GB_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:gb|기가|g)(?![a-z])", re.IGNORECASE)
_MINUTES_RE = re.compile(r"(\d+)\s*분")


def _won_matches(text: str, bare: bool = True):
    """(시작, 끝, 금액)."""
    for match in _WON_RE.finditer(text or ""):
        group = match.groupdict()
        if (group["man"] or group["cheon"]) and not bare and "원" not in match.group():
            continue
        if group["man"]:
            value = float(group["man"]) * 10_000 + int(group["mc"] or 0) * 1_000 + int(group["mb"] or 0) * 100
            value += int((group["mr"] or "0").replace(",", ""))
        elif group["cheon"]:
            value = int(group["cheon"]) * 1_000 + int(group["cb"] or 0) * 100 + int(group["cr"] or 0)
        elif group["won"]:
            value = int(group["won"].replace(",", ""))
        elif group["one_man"]:
            value = 10_000
        elif bare:
            value = int(group["bare"].replace(",", ""))
        else:
            continue
        yield match.start(), match.end(), round(value)


def _money(text: str) -> list[int]:
    """문장 속 원 단위 금액. 단위 없는 숫자는 금액으로 보지 않는다."""
    return [value for _, _, value in _won_matches(text, bare=False)]


def _money_spans(text: str) -> list[tuple[int, int, int]]:
    return list(_won_matches(text, bare=False))


def _unit_matches(text: str, kind: str):
    if kind == "won":
        yield from _won_matches(text)
        return
    pattern = _GB_RE if kind == "gb" else _MINUTES_RE
    for match in pattern.finditer(text):
        yield match.start(), match.end(), float(match.group(1))


# ─────────────────────── ① 조건 검증 ───────────────────────

# 바로 고칠 수 있는 수치 필드 → (단위, 경계 방향, 뒤 발화에서 다시 말했는지 볼 표현).
_MONEY_MENTION = r"\d\s*(?:만\s*원|천\s*원|원)|만\s*원"
_DATA_MENTION = r"\d\s*(?:gb|기가)"
_NUMERIC_FIELDS = {
    "budget_max_won": ("won", "max", _MONEY_MENTION),
    "budget_min_won": ("won", "min", _MONEY_MENTION),
    "min_data_gb": ("gb", "min", _DATA_MENTION),
    "max_data_gb": ("gb", "max", _DATA_MENTION),
    "min_voice_minutes": ("minutes", "min", r"\d\s*분"),
    "min_tethering_gb": ("gb", "min", r"테더링|핫스팟"),
}
_BOUND_MAX_RE = re.compile(r"\s*(?:이하|까지|이내|안쪽|안으로|미만|아래|내로|넘지)")
_BOUND_MIN_RE = re.compile(r"\s*(?:이상|부터|초과|넘는|넘게|넘어)")
# '3만원 넘는 건 싫어', '이상은 안 돼' - 하한 표현을 부정하면 상한이다.
# '10GB 이상 아니면 싫어', '이상 안 되면 곤란해'는 조건문이라 여전히 하한이다.
_NEGATED_RE = re.compile(r"[^.,]{0,8}?(?:싫|안\s*돼|안\s*되|말고|빼|제외|않)")
_CONDITIONAL_RE = re.compile(r"[^.,]{0,8}?(?:아니|안\s*되|안\s*돼|못\s*하)\s*면")
_PREFIX_MAX_RE = re.compile(r"(?:최대|많아야)\s*$")
_PREFIX_MIN_RE = re.compile(r"(?:최소|최소한|적어도)\s*$")
# 'N만원대', '3만원 정도'는 코드가 범위·근삿값으로 바꾼 값이라 인용 숫자와 달라도 정상이다.
_APPROX_RE = re.compile(r"\d\s*만\s*원?\s*대|정도|쯤|내외|안팎|전후|언저리")
_RETRACT_RE = re.compile(
    r"빼|제외|없어도|상관\s*없|관계\s*없|필요\s*없|안\s*해도|취소|풀어|해제|신경\s*안|제한\s*없|무관|"
    r"얼마(?:든|라도)|아니어도|안\s*따져"
)
# 판정이 가리키는 필드와 인용의 주제가 맞는지. '통화는 상관없어'로 예산을 풀거나
# '데이터 20GB 이상'으로 테더링 조건을 거는 LLM 오판을 막는다. 버튼 문구(CONSTRAINT_LABELS)도 받는다.
_TOPIC_RE = {
    "budget": r"예산|요금|가격|금액|돈|비용|\d\s*원|만\s*원",
    "data": r"데이터|용량|gb|기가|무제한",
    "qos": r"속도|qos|소진|mbps",
    "tethering": r"테더링|핫스팟",
    "voice": r"통화|음성|전화",
    "sms": r"문자|sms",
    "carrier": r"통신사|망|알뜰폰|skt|kt|lg|유플|브랜드|사업자|3사",
    "network": r"5g|lte|4g|세대",
    "age": r"나이|연령|청년|시니어|어르신|키즈|학생|군인|병사|세",
    "discount": r"할인|개월|기간",
}
_FIELD_TOPIC = {
    "budget_min_won": "budget", "budget_max_won": "budget",
    "min_data_gb": "data", "max_data_gb": "data", "min_monthly_base_data_gb": "data", "min_daily_data_gb": "data",
    "data_unlimited": "data", "require_full_unlimited": "data",
    "min_qos_mbps": "qos", "requires_qos": "qos", "min_tethering_gb": "tethering",
    "min_voice_minutes": "voice", "voice_unlimited": "voice", "sms_unlimited": "sms",
    "carrier_type": "carrier", "host_mno": "carrier", "mvno_brand": "carrier",
    "network_gen": "network", "age_condition": "age", "min_discount_period_months": "discount",
}


def _on_topic(field: str, quote: str) -> bool:
    label = CONSTRAINT_LABELS.get(field, "")
    if label and _squash(label) in _squash(quote):
        return True
    topic = _FIELD_TOPIC.get(field)
    if topic is None or not re.search(_TOPIC_RE[topic], quote, re.IGNORECASE):
        return False
    # 데이터와 테더링은 둘 다 GB 라서 주제어로만 가른다.
    return not (topic == "data" and re.search(_TOPIC_RE["tethering"], quote))


# 새 상한(하한)을 넣을 때 같은 필드 쌍의 반대쪽 값이 모순이면 그쪽이 잘못 읽힌 것이다
# ('3만원 넘는 건 싫어'가 하한 30,000 으로 들어간 채 상한 30,000 을 더하면 정확히 3만원짜리만 남는다).
_OPPOSITE = {
    "budget_max_won": ("budget_min_won", "max"), "budget_min_won": ("budget_max_won", "min"),
    "max_data_gb": ("min_data_gb", "max"), "min_data_gb": ("max_data_gb", "min"),
}
_WISH_EXTRA_RE = re.compile(r"면\s*(?:더\s*)?좋|되면\s*좋|있음\s*좋")
_BENEFIT_WORD_RE = re.compile(
    r"혜택|ott|넷플릭스|유튜브|티빙|디즈니|웨이브|쿠팡|왓챠|음악|멜론|지니|스포티파이|플로|밀리|도서|멤버십|쿠폰|페이백",
    re.IGNORECASE,
)
_CLAUSE_RE = re.compile(r"[^.,!?\n;]+")


def _squash(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _quote_span(turns: list[str], turn: int | None, quote: str | None) -> tuple[int, int] | None:
    """인용이 그 번호의 사용자 발화에 글자 그대로 있으면 공백을 뺀 좌표로 (시작, 끝)."""
    squashed = _squash(quote)
    if len(squashed) < 2 or turn is None or not 1 <= turn <= len(turns):
        return None
    start = _squash(turns[turn - 1]).find(squashed)
    return None if start < 0 else (start, start + len(squashed))


def _bounded_values(quote: str, kind: str) -> set[tuple[float, str]]:
    """인용 속 '수치+단위+경계어'를 (값, max|min) 으로. 경계어가 수치 바로 옆에 있어야 한다.

    인용 어디서든 경계어를 찾으면 '3만원 넘지 않게, 데이터 20GB 이상'의 '이상'이 예산 하한의
    근거가 된다.
    """
    found = set()
    for start, end, value in _unit_matches(quote, kind):
        after, before = quote[end:end + 12], quote[max(0, start - 6):start]
        if _BOUND_MAX_RE.match(after):
            found.add((value, "max"))
        elif bound := _BOUND_MIN_RE.match(after):
            rest = after[bound.end():]
            negated = _NEGATED_RE.match(rest) and not _CONDITIONAL_RE.match(rest)
            found.add((value, "max" if negated else "min"))
        elif _PREFIX_MAX_RE.search(before):
            found.add((value, "max"))
        elif _PREFIX_MIN_RE.search(before):
            found.add((value, "min"))
    return found


def _later_text(turns: list[str], turn: int, span: tuple[int, int]) -> list[str]:
    """인용 뒤에 사용자가 한 말. 같은 발화의 뒷부분도 포함한다(공백 제거본)."""
    return [_squash(turns[turn - 1])[span[1]:], *(_squash(text) for text in turns[turn:])]


def _verified_value(profile: UserProfile, issue: ProfileIssue, turns: list[str],
                    span: tuple[int, int]) -> float | int | None:
    """wrong_value/missed 가 고치려는 값이 인용으로 뒷받침될 때만 그 값을 돌려준다."""
    rule = _NUMERIC_FIELDS.get(issue.field)
    value, quote = issue.value, issue.quote
    if rule is None or value is None or not math.isfinite(value) or value <= 0:
        return None
    kind, direction, later_mention = rule
    if _APPROX_RE.search(quote) or (kind == "gb" and re.search(r"\d\s*mb", quote, re.IGNORECASE)):
        return None
    if kind == "gb" and not _on_topic(issue.field, quote):
        return None  # 금액은 단위로 주제가 정해진다. GB 는 데이터인지 테더링인지 주제어로 가른다
    if kind == "won" and (
        _CURRENT_FEE_PREFIX_RE.search(quote) or _CURRENT_FEE_SUFFIX_RE.search(quote)
        or re.search(r"지금|현재|기존|원래|쓰던|쓰는|내고|내는|납부", quote)
    ):
        return None  # 지금 내는 요금은 예산이 아니다(단위 없이 말한 '지금 55000 내는'도 포함)
    if not any(math.isclose(found, value) and bound == direction for found, bound in _bounded_values(quote, kind)):
        return None
    # 뒤에서 다시 말했거나 뺐으면(버튼 문장 '…조건은 빼고' 포함) 그쪽이 최신 요구다.
    if any(re.search(later_mention, text, re.IGNORECASE) or _RETRACT_RE.search(text)
           for text in _later_text(turns, issue.turn, span)):
        return None
    current = getattr(profile, issue.field, None)
    if issue.verdict == "missed" and current is not None:
        return None
    if issue.verdict == "wrong_value" and (
        current is None or abs(current - value) <= max(1.0, 0.02 * value)
    ):
        return None  # '3만원 미만' → 29,999 같은 표기 차이는 고치지 않는다
    return int(value) if issue.field.endswith(("_won", "_minutes")) else float(value)


_GENERIC_BENEFIT_RE = re.compile(r"혜택|부가\s*서비스")


def _benefit_mentioned(item: str, field: str, quote: str) -> bool:
    if field == "wanted_benefits":
        return has_benefit(quote, item)
    names = {*item.split("/"), *(alias for alias, category in BENEFIT_CATEGORY_ALIASES.items() if category == item)}
    return any(name and name.casefold() in quote.casefold() for name in names)


def _retract(profile: UserProfile, issue: ProfileIssue, turns: list[str], span) -> tuple[UserProfile, str]:
    field, quote = issue.field, issue.quote
    if field not in CONSTRAINT_FIELDS or getattr(profile, field, None) in (None, []):
        return profile, "skip:field"
    if not _on_topic(field, quote) and not (field in _BENEFIT_FIELDS and _GENERIC_BENEFIT_RE.search(quote)):
        if field not in _BENEFIT_FIELDS or not any(_benefit_mentioned(i, field, quote) for i in getattr(profile, field)):
            return profile, "skip:topic"
    source = _quote_span(turns, issue.source_turn, issue.source_quote)
    later = source is not None and (
        issue.turn > issue.source_turn or (issue.turn == issue.source_turn and span[0] >= source[1])
    )
    if not (_RETRACT_RE.search(quote) and later):
        return profile, "skip:evidence"

    if field in _BENEFIT_FIELDS:
        # 목록에서 뺀다고 말한 항목만 뺀다. '음악은 빼줘'가 OTT 필수까지 지우면 안 된다.
        items = list(getattr(profile, field))
        kept = [item for item in items if not _benefit_mentioned(item, field, quote)]
        if len(kept) == len(items) and not _GENERIC_BENEFIT_RE.search(quote):
            return profile, "skip:evidence"
        if kept and len(kept) < len(items):
            return profile.model_copy(update={field: kept}), "relaxed"
        # 항목을 지목하지 않은 '혜택은 없어도 돼'는 필드 전체를 푼다.

    fields = [field]
    if field.startswith("budget") and not re.search(r"상한|하한|최대|최소|이하|이상|까지|부터", quote):
        fields = ["budget_min_won", "budget_max_won"]  # '예산은 상관없어'는 'N만원대'의 하한까지 푼 것이다
    # profiling 안의 버튼 해제와 같은 함수. 모든 보정 뒤에 적용하므로 정규식이 되살리지 못한다.
    # state.relaxed_fields 에는 넣지 않는다 - 거기 데이터 필드가 있으면 현재 요금제 기준선이 꺼진다.
    return _apply_relaxed_fields(profile, fields), "relaxed"


def _soften(profile: UserProfile, issue: ProfileIssue, turns: list[str], span) -> tuple[UserProfile, str]:
    if issue.field not in _BENEFIT_FIELDS or issue.field not in profile.hard_constraints:
        return profile, "skip:field"
    turn = turns[issue.turn - 1]
    quote_clauses, other_clauses = [], []
    for clause in _CLAUSE_RE.finditer(turn):
        start = len(_squash(turn[:clause.start()]))
        end = start + len(_squash(clause.group()))
        (quote_clauses if start < span[1] and end > span[0] else other_clauses).append(clause.group())
    # LLM 이 발화 전체를 인용해도 혜택을 말한 절만 본다. '무조건 5만원 이하'의 '무조건'은 예산에 붙은 말이다.
    names = [str(item) for item in (profile.wanted_benefits or [])]
    benefit_clauses = [c for c in quote_clauses if _BENEFIT_WORD_RE.search(c) or any(n and n in c for n in names)]
    if benefit_clauses:
        other_clauses += [c for c in quote_clauses if c not in benefit_clauses]
    wished = " ".join(benefit_clauses or quote_clauses)
    if not (_BENEFIT_WISH_RE.search(wished) or _WISH_EXTRA_RE.search(wished)) or _BENEFIT_MUST_RE.search(wished):
        return profile, "skip:evidence"
    if any(_BENEFIT_MUST_RE.search(clause) and _BENEFIT_WORD_RE.search(clause) for clause in other_clauses):
        return profile, "skip:evidence"  # 다른 혜택은 필수라고 말했다. 필터가 혜택 필드를 함께 쓰므로 손대지 않는다
    # 값은 남겨 mcda 가 가점으로 쓰게 하고, 필터에서만 뺀다. data.filter_candidates 는 혜택 필드 중
    # 하나라도 hard 면 둘 다 필터로 쓰므로 함께 뺀다(profiling._apply_benefit_constraint_strength 와 같다).
    hard = [item for item in profile.hard_constraints if item not in _BENEFIT_FIELDS]
    return profile.model_copy(update={"hard_constraints": hard}), "softened"


def _apply_issue(profile: UserProfile, issue: ProfileIssue, turns: list[str]) -> tuple[UserProfile, str]:
    """판정 하나를 코드로 확인하고, 확인되면 profile 에 적용한다. (새 profile, 한 일)"""
    span = _quote_span(turns, issue.turn, issue.quote)
    if span is None:
        return profile, "skip:quote"
    if issue.verdict == "retracted":
        return _retract(profile, issue, turns, span)
    if issue.verdict == "soften":
        return _soften(profile, issue, turns, span)
    if issue.verdict in ("wrong_value", "missed"):
        value = _verified_value(profile, issue, turns, span)
        if value is None:
            return profile, "skip:evidence"
        field = issue.field
        update: dict = {field: value}
        hard = profile.hard_constraints if field in profile.hard_constraints else [*profile.hard_constraints, field]
        opposite, side = _OPPOSITE.get(field, (None, None))
        other = getattr(profile, opposite, None) if opposite else None
        if other is not None and (other >= value if side == "max" else other <= value):
            update[opposite] = None
            hard = [item for item in hard if item != opposite]
        update["hard_constraints"] = hard
        return profile.model_copy(update=update), "set"
    # phantom 은 인용할 근거가 원래 없어 코드로 확인할 수 없다. 자동으로 지우면
    # '3만원대' → 39,999 처럼 코드가 표준값으로 바꾼 정상 조건까지 사라진다.
    return profile, "logged"


def _render_turns(state: PipelineState) -> str:
    lines, number = [], 0
    for message in state.get("messages", []):
        if isinstance(message, HumanMessage):
            number += 1
            lines.append(f"[{number}] {message.content}")
        elif isinstance(message, AIMessage) and not message.name:
            # 화면이 보낸 이전 답변('어떤 혜택을 찾으시나요?' 등). 파이프라인 내부 메시지는 name 이 있다.
            lines.append(f"    (서비스 답변) {str(message.content)[:200]}")
    return "\n".join(lines)


def _profile_view(profile: UserProfile) -> dict:
    view = {
        field: getattr(profile, field)
        for field in CONSTRAINT_FIELDS
        if getattr(profile, field) not in (None, [], "")
    }
    view["hard_constraints"] = profile.hard_constraints
    if profile.reference_fee_won is not None:
        view["reference_fee_won(지금 내는 요금 - 예산 아님)"] = profile.reference_fee_won
    return view


def _candidate_count(profile: UserProfile) -> int | None:
    try:
        return len(filter_candidates(profile.model_dump()))
    except Exception:  # 기록용 숫자다. 이것 때문에 추천을 막지 않는다
        return None


def profile_check_node(state: PipelineState, config: RunnableConfig) -> dict:
    """① 조건 검증. profiling 직후, recommend 전에 돈다."""
    profile = state.get("profile")
    turns = [str(message.content) for message in state.get("messages", []) if isinstance(message, HumanMessage)]
    if profile is None or not turns:
        return {}

    prompt = PROFILE_CHECK_PROMPT.format(
        turns=_render_turns(state),
        profile=json.dumps(_profile_view(profile), ensure_ascii=False, default=str),
    )
    try:
        check = get_eval_llm(config).with_structured_output(ProfileCheck).invoke(prompt)
    except Exception as exc:  # 검증이 실패해도 추천은 나간다
        return _log({"gate": "profile", "action": "error", "error": type(exc).__name__})

    # 화면의 '조건 풀기' 버튼으로 푼 필드는 판정이 무엇이든 건드리지 않는다. 버튼 신호가 우선이다.
    released = set(state.get("relaxed_fields") or [])
    checked, issues = profile, []
    for issue in check.issues:
        if issue.field in released:
            action = "skip:relaxed"
        else:
            checked, action = _apply_issue(checked, issue, turns)
        issues.append({"field": issue.field, "verdict": issue.verdict, "action": action, "quote": issue.quote})
    entry: dict = {"gate": "profile", "issues": issues}
    if checked is profile:
        return _log(entry)

    # 결과 확인: 교정이 후보를 얼마나 바꿨는지. 현재 요금제 기준선은 뺀 필터 기준이다.
    entry["candidates"] = {"before": _candidate_count(profile), "after": _candidate_count(checked)}
    update: dict = {"profile": checked}
    if core_signal_missing(checked):
        # 교정으로 예산·데이터 신호가 모두 사라졌다. 전체 카탈로그에서 뽑지 말고 되묻는다.
        update["profile"] = checked.model_copy(
            update={"needs_user_input": True, "followup_question": CORE_MISSING_QUESTION}
        )
        update["clarification_question"] = CORE_MISSING_QUESTION
    return {**update, **_log(entry)}


# ─────────────────────── ② 설명 검증 ───────────────────────

# '현재 요금제보다', '지금보다', '기존 요금제 대비' - 현재 요금제와 비교한 문장만 본다.
# '현재 월 6,800원'(= 지금 시점)이나 후보끼리 비교한 '저렴'은 방향 검사 대상이 아니다.
# '현재 요금제보다 … 저렴', '지금 쓰는 요금에 비하면 … 싸다'. 비교 대상 바로 뒤(다른 '보다'가 끼지 않은
# 20자 안)에 싸다는 말이 올 때만 현재 요금제와 비교한 것으로 본다. '현재보다 속도는 빠르고 2순위보다
# 저렴'이나 '현재 월 6,800원'(= 지금 시점)은 대상이 아니다.
_CHEAPER_THAN_CURRENT_RE = re.compile(
    r"(?:현재|지금|기존|쓰시는|쓰고\s*계신)\s*(?:(?:쓰시는|쓰는|내시는|내는|쓰고\s*계신|내고\s*계신)\s*)?"
    r"(?:요금제|요금|납부액|것)?\s*(?:보다|대비|에\s*비해|에\s*비하면|와\s*비교|과\s*비교)"
    r"(?:(?!보다)(?:[^.,]|(?<=\d),(?=\d))){0,20}?(?:저렴|절약|아낄|아껴|절감|(?<!비)(?:싸|쌉|쌌)|덜\s*내)"
)
# 비싸다는 고지나 할인 뒤의 인상을 함께 밝힌 문장은 정상이다('처음엔 덜 내지만 7개월 뒤에는 …').
_PRICIER_RE = re.compile(
    r"비싸|비쌉|비쌀|비쌌|더\s*(?:내|냅|부담|듭|들어|나가)|추가\s*부담|인상|이후|끝나면|종료\s*후|뒤에는|부터는"
    r"|(?:요금|가격)[^.,]{0,6}(?:올라|오르|오릅|늘어)"
)
# 약정·위약금·해지 조건은 수집 데이터에 없다. 바로 뒤에서 부정하거나 확인을 권하면 정상이다.
_PROMISE_RE = re.compile(
    r"위약금\s*(?:이|은|도)?\s*(?:걱정\s*(?:이|은|도)?\s*)?(?:없|0\s*원)"
    r"|언제든(?:지)?\s*(?:해지|해약|변경)|속도\s*(?:를|가|는|도)?\s*보장"
)
_HEDGE_AFTER_RE = re.compile(r"[^.,]{0,12}?(?:확인|여부|는지|인지|아니|아닙|않|못|수\s*없|없으니|없습니다만)")
# 금액 바로 뒤 말투. '6천원 넘게'는 버림, '1만 원대'는 구간, 나머지는 반올림으로 본다.
_FLOOR_AFTER_RE = re.compile(r"\s*(?:이\s*|을\s*|가\s*)?(?:넘|이상|초과|남짓|조금\s*넘|웃돌)")
_BAND_AFTER_RE = re.compile(r"\s*대")


def _numbers(value: Any):
    """후보 행 안의 모든 숫자와, 문자열(혜택·프로모션 설명) 속 금액."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if value == value:  # NaN 제외
            yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _numbers(item)
    elif isinstance(value, str):
        yield from _money(value)


def _allowed_amounts(state: PipelineState, recommendations: list[dict]) -> tuple[set[float], set[float]]:
    """(그대로 인용할 수 있는 금액, 반올림해 말해도 되는 금액).

    카드 문장은 다른 후보와 비교하라는 지시를 받아서(report.py 리포트 작성 규칙 4) 자기 행이 아닌
    금액·차액·12개월 합계도 쓴다. 그래서 추천 전체·현재 요금제·예산을 합친 집합으로 본다.
    반올림 비교는 요금·차액·기간 합계로만 한다. 행 안의 아무 숫자와 반올림으로 맞춰 주면
    '월 9만원' 같은 틀린 단정이 우연히 통과한다.
    """
    profile = state.get("profile")
    reference = state.get("reference") or {}
    sources = [*recommendations, reference, profile.model_dump() if profile else {}]
    exact = {float(number) for source in sources for number in _numbers(source)}

    fees = {
        float(row[key])
        for row in (*recommendations, reference)
        for key in ("discounted_fee", "monthly_fee")
        if isinstance(row.get(key), (int, float))
    }
    fees |= {float(round(_switching_monthly_fee(row))) for row in recommendations if row.get("discounted_fee") is not None}
    if profile:
        fees |= {float(v) for v in (profile.budget_max_won, profile.budget_min_won, profile.reference_fee_won) if v}
    diffs = {abs(a - b) for a in fees for b in fees if a != b}
    periods = set()
    for row in recommendations:
        months, fee, regular = row.get("discount_period_months"), row.get("discounted_fee"), row.get("monthly_fee")
        if isinstance(months, (int, float)) and months > 0 and isinstance(fee, (int, float)):
            periods.add(float(fee * months))  # 할인 기간 동안 내는 총액
            if isinstance(regular, (int, float)):
                periods.add(float((regular - fee) * months))  # 할인 기간 동안 아끼는 총액
    rounded = fees | diffs | periods | {value * COMPARE_MONTHS for value in fees | diffs}
    return exact | rounded, rounded


def _amount_known(amount: int, allowed: tuple[set[float], set[float]], mode: str = "round") -> bool:
    exact, rounded = allowed
    if any(abs(amount - value) <= 10 for value in exact):
        return True
    for unit in (100, 1_000, 10_000):
        if amount % unit:
            continue
        for value in rounded:
            if value < unit:
                continue
            if mode == "floor" and amount <= value < amount + unit:  # '6천원 넘게' = 6,000 이상 7,000 미만
                return True
            if mode == "band" and amount <= value < amount + max(unit, 10 ** (len(str(amount)) - 1)):  # '1만 원대'
                return True
            if mode == "round" and abs(amount - value) <= unit / 2:  # '약 3천원', '11만원 정도'
                return True
    return False


def _sentence_problem(sentence: str, plan: Mapping, allowed: tuple[set[float], set[float]]) -> str | None:
    unknown = []
    for _, end, amount in _money_spans(sentence):
        after = sentence[end:end + 8]
        mode = "floor" if _FLOOR_AFTER_RE.match(after) else "band" if _BAND_AFTER_RE.match(after) else "round"
        if not _amount_known(amount, allowed, mode):
            unknown.append(amount)
    if unknown:
        return "데이터에 없는 금액 " + ", ".join(f"{amount:,}원" for amount in unknown)
    if (
        (plan.get("vs_current") or {}).get("결론") == "현재보다 비싸다"
        and _CHEAPER_THAN_CURRENT_RE.search(sentence)
        and not _PRICIER_RE.search(sentence)
    ):
        return "현재보다 비싼 요금제를 더 싸다고 설명"
    for promise in _PROMISE_RE.finditer(sentence):
        if not _HEDGE_AFTER_RE.match(sentence[promise.end():]):
            return "데이터에 없는 약속(위약금·해지·속도 보장)"
    return None


def _card_violations(state: PipelineState) -> list[dict]:
    """화면에 나가는 카드 문장(ranked[].reason)만 본다. 화면은 리포트 본문을 그리지 않는다."""
    recommendations = _ranked_recommendations(state)
    allowed = _allowed_amounts(state, recommendations)
    violations = []
    for rank, (plan, row) in enumerate(zip(state.get("ranked") or [], recommendations), start=1):
        for sentence in split_sentences(plan.reason):
            problem = _sentence_problem(sentence, row, allowed)
            if problem:
                violations.append({"rank": rank, "reason": problem, "sentence": sentence})
    return violations


def _drop_sentences(state: PipelineState, violations: list[dict]) -> tuple[list, str]:
    """걸린 문장만 뺀다. 카드가 비면 _card_reason 이 원본 값으로 만든 문장으로 채운다."""
    bad = {(item["rank"], item["sentence"]) for item in violations}
    ranked = list(state.get("ranked") or [])
    for index, row in enumerate(_ranked_recommendations(state)):
        sentences = split_sentences(ranked[index].reason)
        kept = [sentence for sentence in sentences if (index + 1, sentence) not in bad]
        if len(kept) != len(sentences):
            ranked[index] = ranked[index].model_copy(update={"reason": _card_reason(" ".join(kept), row)})
    report = state.get("report", "")
    for item in violations:
        report = report.replace(item["sentence"], "")
    return ranked, report


def evaluation_node(state: PipelineState, config: RunnableConfig) -> dict:
    """② 설명 검증. report 직후에 돈다."""
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

    invariant_errors, _ = _code_checks(state)
    if invariant_errors:
        _logger.warning("evaluation invariant %s", "; ".join(invariant_errors))
    violations = _card_violations(state)
    entry: dict = {"gate": "report", "attempt": attempt, "invariant_errors": invariant_errors, "violations": violations}
    update: dict = {"attempt": attempt}

    if not violations:
        ev = Evaluation(passed=True)
    elif attempt <= MAX_REVISIONS:
        feedback = "; ".join(f"{item['rank']}순위 '{item['sentence']}' - {item['reason']}" for item in violations)
        ev = Evaluation(passed=False, feedback=feedback, retry_target="report")
        update["feedback"] = [f"[{attempt}차 설명 검증] {feedback}. 이 문장을 빼거나 데이터에 있는 값으로 고쳐 써라."]
        entry["action"] = "rewrite"
    else:
        ranked, report = _drop_sentences(state, violations)
        ev = Evaluation(passed=True, feedback=f"근거를 확인하지 못한 문장 {len(violations)}개를 뺐다.")
        update.update({"ranked": ranked, "report": report})
        entry["action"] = "dropped"

    message = AIMessage(content=f"[evaluation] passed={ev.passed} target={ev.retry_target} {ev.feedback}", name="evaluation")
    return {**update, "evaluation": ev, "messages": [message], **_log(entry)}


if __name__ == "__main__":
    from unittest.mock import patch

    from ..schemas import ScoredPlan

    def _plan(plan_id, name, fee=30000, **kw):
        row = {
            "plan_id": plan_id,
            "plan_name": name,
            "discounted_fee": fee,
            "monthly_fee": fee,
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
            "benefit_categories": [],
            "discount_period_months": 12,
        }
        row.update(kw)
        return row

    def _state(cands, ranked, report, profile):
        return {"profile": profile, "candidates": cands, "ranked": ranked, "report": report}

    def _act(profile, issue, turns):
        return _apply_issue(profile, issue, turns)[1]

    # ── 불변식 검사(로그 전용) ──
    ok_profile = UserProfile(budget_max_won=40000, hard_constraints=["budget_max_won"])
    a, b = _plan("1", "A"), _plan("2", "B", fee=35000)
    ranked = [
        ScoredPlan(plan_id="1", plan_name="A", score=90, reason=""),
        ScoredPlan(plan_id="2", plan_name="B", score=80, reason=""),
    ]
    report = "| 순위 | 요금제 | 월 요금 |\n| 1 | A | 30,000원 |\n| 2 | B | 35,000원 |"
    errors, rows = _code_checks(_state([a, b], ranked, report, ok_profile))
    assert errors == [] and len(rows) == 2, errors
    errors, _ = _code_checks(_state([a], ranked, report, ok_profile))
    assert any("환각" in e for e in errors), errors
    errors, _ = _code_checks(_state([a, _plan("2", "B", fee=99000)], ranked, report, ok_profile))
    assert any("budget_max_won" in e for e in errors), errors

    # ── 금액 읽기 ──
    assert _money("월 4만 6,200원, 1만 5천원, 1.5만원, 13,990원, 만 원, 100GB") == [46200, 15000, 15000, 13990, 10000]
    assert _bounded_values("3만원 넘는 건 싫어", "won") == {(30000, "max")}
    assert _bounded_values("3만원 넘지 않게, 데이터 20GB 이상", "won") == {(30000, "max")}
    assert _bounded_values("월 1만 5천원 이하", "won") == {(15000, "max")}
    assert _bounded_values("데이터 500MB 이상", "gb") == set()
    assert _bounded_values("데이터 10GB 이상 아니면 싫어", "gb") == {(10.0, "min")}
    assert _money("5만 포인트, 2만 8천 6백원, 8천 600원") == [28600, 8600]

    # ── ① 조건 검증: 인용이 확인된 판정만 적용한다 ──
    turns = ["월 2만원 이하, 데이터 10GB 이하로 추천해줘", "데이터 상한은 빼고 다시 추천해줘"]
    capped = UserProfile(budget_max_won=20000, max_data_gb=10, hard_constraints=["budget_max_won", "max_data_gb"])
    retract = ProfileIssue(field="max_data_gb", verdict="retracted", turn=2, quote="데이터 상한은 빼고",
                           source_turn=1, source_quote="데이터 10GB 이하")
    fixed, action = _apply_issue(capped, retract, turns)
    assert action == "relaxed" and fixed.max_data_gb is None and fixed.hard_constraints == ["budget_max_won"], fixed
    assert _act(capped, retract.model_copy(update={"quote": "상한 없애줘"}), turns) == "skip:quote"
    assert _act(capped, retract.model_copy(update={"turn": 1, "quote": "데이터 10GB 이하"}), turns) == "skip:evidence"
    # 한 발화 안의 철회, '신경 안 써' 말투, 'N만원대'의 하한까지 함께 푸는 예산 철회
    one = ["3만원대로 하려다가, 그냥 예산은 신경 안 써도 돼. 데이터 20GB 이상"]
    band = UserProfile(budget_min_won=30000, budget_max_won=39999, min_data_gb=20,
                       hard_constraints=["budget_min_won", "budget_max_won", "min_data_gb"])
    fixed, action = _apply_issue(band, ProfileIssue(field="budget_max_won", verdict="retracted", turn=1,
                                 quote="예산은 신경 안 써도 돼", source_turn=1, source_quote="3만원대로"), one)
    assert action == "relaxed" and fixed.budget_min_won is None and fixed.budget_max_won is None, fixed
    # 목록 필드는 뺀다고 말한 항목만 뺀다
    both = UserProfile(wanted_benefit_categories=["영상/OTT", "음악/오디오"], budget_max_won=50000,
                       hard_constraints=["budget_max_won", "wanted_benefit_categories"])
    fixed, action = _apply_issue(both, ProfileIssue(field="wanted_benefit_categories", verdict="retracted", turn=2,
                                 quote="음악은 빼줘", source_turn=1, source_quote="OTT랑 음악 혜택 둘 다 꼭"),
                                 ["5만원 이하, OTT랑 음악 혜택 둘 다 꼭 있어야 해", "음악은 빼줘"])
    assert action == "relaxed" and fixed.wanted_benefit_categories == ["영상/OTT"], fixed
    assert "wanted_benefit_categories" in fixed.hard_constraints

    # 단위 없는 예산을 보정이 지운 경우(_drop_phantom_budget) 인용 속 숫자로 되살린다
    budget_turns = ["예산 30000 이하, 데이터 20GB 이상"]
    missed = ProfileIssue(field="budget_max_won", verdict="missed", turn=1, quote="예산 30000 이하", value=30000)
    fixed, action = _apply_issue(UserProfile(min_data_gb=20, hard_constraints=["min_data_gb"]), missed, budget_turns)
    assert action == "set" and fixed.budget_max_won == 30000 and "budget_max_won" in fixed.hard_constraints, fixed
    # 숫자·단위·방향이 인용과 맞지 않거나, 근삿값·현재 요금·뒤에서 다시 말한 값이면 손대지 않는다
    skip = "skip:evidence"
    assert _act(UserProfile(), missed.model_copy(update={"value": 25000}), budget_turns) == skip
    assert _act(UserProfile(), missed.model_copy(update={"field": "budget_min_won"}), budget_turns) == skip
    unit_mix = ProfileIssue(field="min_data_gb", verdict="wrong_value", turn=1, quote="통화 300분 이상, 데이터 10GB 이상", value=300)
    assert _act(UserProfile(min_data_gb=10), unit_mix, ["통화 300분 이상, 데이터 10GB 이상"]) == skip
    over = ProfileIssue(field="budget_min_won", verdict="missed", turn=1, quote="3만원 넘는 건 싫어", value=30000)
    assert _act(UserProfile(budget_max_won=30000), over, ["3만원 넘는 건 싫어"]) == skip
    approx = ProfileIssue(field="budget_max_won", verdict="wrong_value", turn=1, quote="3만원대 이하", value=30000)
    assert _act(UserProfile(budget_max_won=39999), approx, ["3만원대 이하로"]) == skip
    current = ProfileIssue(field="budget_max_won", verdict="missed", turn=1, quote="지금 3만원 이하로 내고", value=30000)
    assert _act(UserProfile(), current, ["지금 3만원 이하로 내고 있어"]) == skip
    stale = ProfileIssue(field="budget_max_won", verdict="wrong_value", turn=1, quote="3만원 이하", value=30000)
    assert _act(UserProfile(budget_max_won=20000), stale, ["3만원 이하로", "아니 2만원 이하로"]) == skip
    assert _act(UserProfile(budget_max_won=20000), stale, ["3만원 이하로 해줘. 아 아니다, 2만원 이하로"]) == skip
    assert _act(UserProfile(), stale.model_copy(update={"verdict": "missed"}), ["3만원 이하로", "예산 조건은 빼고 다시 추천해줘"]) == skip
    compound = ProfileIssue(field="budget_max_won", verdict="wrong_value", turn=1, quote="월 1만 5천원 이하", value=15000)
    assert _act(UserProfile(budget_max_won=5000), compound, ["월 1만 5천원 이하"]) == "set"
    # 필드와 인용의 주제가 다르면 손대지 않는다(데이터 인용으로 테더링, 통화 인용으로 예산)
    tether = ProfileIssue(field="min_tethering_gb", verdict="missed", turn=1, quote="데이터 20GB 이상", value=20)
    assert _act(UserProfile(min_data_gb=20), tether, ["데이터 20GB 이상"]) == skip
    voice_off = ProfileIssue(field="budget_max_won", verdict="retracted", turn=2, quote="통화는 상관없어",
                             source_turn=1, source_quote="3만원 이하")
    assert _act(UserProfile(budget_max_won=30000), voice_off, ["3만원 이하", "통화는 상관없어"]) == "skip:topic"
    # 단위 없이 말한 지금 요금도 예산으로 넣지 않는다
    paying = ProfileIssue(field="budget_min_won", verdict="missed", turn=1, quote="지금 55000 이상 내는데", value=55000)
    assert _act(UserProfile(), paying, ["지금 55000 이상 내는데 더 싼 거"]) == skip
    # 잘못 들어간 하한과 같은 값의 상한을 넣으면 하한을 지운다(정확히 3만원짜리만 남지 않게)
    flipped = UserProfile(budget_min_won=30000, min_data_gb=10, hard_constraints=["budget_min_won", "min_data_gb"])
    cap = ProfileIssue(field="budget_max_won", verdict="missed", turn=1, quote="3만원 넘는 건 싫어", value=30000)
    fixed, action = _apply_issue(flipped, cap, ["3만원 넘는 건 싫어. 데이터 10GB 이상"])
    assert action == "set" and (fixed.budget_min_won, fixed.budget_max_won) == (None, 30000), fixed
    assert "budget_min_won" not in fixed.hard_constraints
    # 항목을 지목하지 않은 혜택 철회는 필드 전체를 푼다
    generic = ProfileIssue(field="wanted_benefits", verdict="retracted", turn=2, quote="혜택은 없어도 돼",
                           source_turn=1, source_quote="넷플릭스 꼭")
    fixed, action = _apply_issue(UserProfile(wanted_benefits=["넷플릭스"], hard_constraints=["wanted_benefits"]),
                                 generic, ["넷플릭스 꼭 있어야 해", "혜택은 없어도 돼"])
    assert action == "relaxed" and fixed.wanted_benefits is None, fixed

    # '무조건'이 예산에 붙어 혜택까지 필수가 된 경우: 혜택 절의 희망 말투로 필터에서만 뺀다
    wish_turns = ["무조건 3만원 이하로. 넷플릭스 있으면 더 좋고"]
    must_benefit = UserProfile(budget_max_won=30000, wanted_benefits=["넷플릭스"], wanted_benefit_categories=["영상/OTT"],
                               hard_constraints=["budget_max_won", "wanted_benefits", "wanted_benefit_categories"])
    soften = ProfileIssue(field="wanted_benefits", verdict="soften", turn=1, quote="넷플릭스 있으면 더 좋고")
    fixed, action = _apply_issue(must_benefit, soften, wish_turns)
    assert action == "softened" and fixed.wanted_benefits == ["넷플릭스"] and fixed.hard_constraints == ["budget_max_won"]
    # 인용을 잘라 와도 같은 절에 '반드시'가 있으면, 다른 혜택을 필수라고 했으면 손대지 않는다
    cut = ["밀리의 서재 있으면 좋겠다 수준이 아니라 반드시 있어야 해"]
    assert _act(must_benefit, soften.model_copy(update={"quote": "밀리의 서재 있으면 좋겠다"}), cut) == skip
    mixed = ["OTT 혜택은 꼭 있어야 해. 음악 혜택은 있으면 좋겠어"]
    assert _act(must_benefit, soften.model_copy(update={"quote": "음악 혜택은 있으면 좋겠어"}), mixed) == skip
    # LLM 이 발화 전체를 인용해도 혜택 절만 본다
    whole = soften.model_copy(update={"quote": wish_turns[0]})
    assert _act(must_benefit, whole, wish_turns) == "softened"
    phantom = ProfileIssue(field="budget_max_won", verdict="phantom", turn=1, quote="무조건 3만원 이하로")
    assert _apply_issue(must_benefit, phantom, wish_turns) == (must_benefit, "logged")

    # 노드: LLM 판정을 흉내 내 전체 흐름(적용·후보 수 기록·버튼 우선·fail-open)을 확인한다
    class _FakeLLM:
        def __init__(self, result):
            self.result = result

        def with_structured_output(self, schema):
            return self

        def invoke(self, prompt):
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    node_state = {"messages": [HumanMessage(content=t) for t in turns], "profile": capped}
    with patch(f"{__name__}.get_eval_llm", return_value=_FakeLLM(ProfileCheck(issues=[retract]))):
        out = profile_check_node(node_state, {})
    assert out["profile"].max_data_gb is None, out
    counts = out["eval_log"][0]["candidates"]
    assert counts["after"] > counts["before"], counts
    restore = ProfileIssue(field="budget_max_won", verdict="missed", turn=1, quote="월 2만원 이하", value=20000)
    with patch(f"{__name__}.get_eval_llm", return_value=_FakeLLM(ProfileCheck(issues=[restore]))):
        out = profile_check_node({**node_state, "profile": UserProfile(), "relaxed_fields": ["budget_max_won"]}, {})
    assert "profile" not in out and out["eval_log"][0]["issues"][0]["action"] == "skip:relaxed", out
    with patch(f"{__name__}.get_eval_llm", return_value=_FakeLLM(RuntimeError("timeout"))):
        out = profile_check_node(node_state, {})
    assert "profile" not in out and out["eval_log"][0]["action"] == "error", out
    # 교정으로 예산·데이터 신호가 모두 사라지면 되묻는다
    only_budget = UserProfile(budget_max_won=20000, hard_constraints=["budget_max_won"])
    drop_budget = ProfileIssue(field="budget_max_won", verdict="retracted", turn=2, quote="예산은 상관없어",
                               source_turn=1, source_quote="2만원 이하")
    ask_state = {"messages": [HumanMessage(content="2만원 이하"), HumanMessage(content="예산은 상관없어")],
                 "profile": only_budget}
    with patch(f"{__name__}.get_eval_llm", return_value=_FakeLLM(ProfileCheck(issues=[drop_budget]))):
        out = profile_check_node(ask_state, {})
    assert out["clarification_question"] == CORE_MISSING_QUESTION and out["profile"].needs_user_input, out

    # ── ② 설명 검증: 금액·방향·약속 ──
    promo = _plan("7", "P", fee=38990, discounted_fee=13990, discount_period_months=6,
                  vs_current={"결론": "현재보다 비싸다"})
    allowed = _allowed_amounts({"reference": {"discounted_fee": 20000}, "profile": None}, [promo])
    ok = [
        "현재 월 13,990원, 6개월 뒤 38,990원입니다.",
        "할인이 끝나면 월 3만 8,990원입니다.",
        "지금보다 월 6,010원 싸지만 할인이 끝나면 더 비쌉니다.",
        "정가보다 약 2만 5천원 저렴합니다.",
        "현재 월 13,990원으로 세 후보 중 가장 저렴합니다.",
        "할인 6개월 동안 내는 요금은 총 83,940원입니다.",
        "12개월 평균으로는 현재 요금제보다 월 6,490원 더 냅니다.",
        "위약금이 있는지 가입 전에 확인하세요.",
        "속도를 보장하지는 않습니다.",
        "모든 이용 상황의 속도 보장은 아닙니다.",
        "실제 속도를 보장할 수 없습니다.",
        "현재보다 속도는 빠르고 2순위보다 저렴합니다.",
        "처음 6개월은 현재 요금제보다 덜 내지만 이후에는 월 38,990원입니다.",
        "12개월 평균으로 현재보다 월 6천원 넘게 더 냅니다.",
        "할인 중에는 1만 원대로 쓸 수 있습니다.",
    ]
    for sentence in ok:
        assert _sentence_problem(sentence, promo, allowed) is None, sentence
    assert "금액" in _sentence_problem("월 9,900원에 쓸 수 있습니다.", promo, allowed)
    assert "금액" in _sentence_problem("할인이 끝나면 월 9만원으로 오릅니다.", promo, allowed)
    assert "비싼" in _sentence_problem("지금 쓰시는 요금제보다 저렴합니다.", promo, allowed)
    assert "약속" in _sentence_problem("위약금 없이 언제든 해지할 수 있습니다.", promo, allowed)
    assert "비싼" in _sentence_problem("지금 쓰는 요금에 비하면 저렴해요.", promo, allowed)
    assert "약속" in _sentence_problem("끊기지 않고 속도도 보장됩니다.", promo, allowed)
    assert "약속" in _sentence_problem("위약금 걱정이 없습니다.", promo, allowed)

    # 노드: 1차는 재작성, 2차는 문장 삭제 후 통과
    card = [ScoredPlan(plan_id="7", plan_name="P", score=90, reason="데이터가 넉넉합니다. 위약금 없이 해지할 수 있습니다.")]
    report_state = {"profile": UserProfile(), "candidates": [promo], "ranked": card,
                    "report": "### 1순위 — P\n데이터가 넉넉합니다. 위약금 없이 해지할 수 있습니다.\n\n요금 조건: 현재 월 13,990원"}
    first = evaluation_node(report_state, {})
    assert not first["evaluation"].passed and first["evaluation"].retry_target == "report", first
    assert first["feedback"] and "위약금" in first["feedback"][0]
    second = evaluation_node({**report_state, "attempt": 1}, {})
    assert second["evaluation"].passed and second["eval_log"][0]["action"] == "dropped", second
    assert second["ranked"][0].reason == "데이터가 넉넉합니다.", second["ranked"][0].reason
    assert "위약금" not in second["report"]
    clean = evaluation_node({**report_state, "ranked": [card[0].model_copy(update={"reason": "데이터가 넉넉합니다."})]}, {})
    assert clean["evaluation"].passed and "feedback" not in clean

    print("self-check ok")
