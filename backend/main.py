# -*- coding: utf-8 -*-
"""FastAPI 진입점. 화면이 부르는 4개 엔드포인트만 둔다.

    uvicorn backend.main:app --reload --port 8000

같은 와이파이에서 폰으로 접속할 때도 프론트(Vite)가 /api 를 프록시하므로
이 서버는 127.0.0.1 에만 떠 있으면 된다. CORS 설정이 필요 없는 이유.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from agent.data import all_plans, get_plan
from agent.graph import graph
from agent.state import get_llm
from .plans import (
    COMPARE_MONTHS,
    SORTS,
    apply_filters,
    facet_counts,
    to_plan_item,
    to_plan_items,
)

app = FastAPI(title="모모플랜 API")


@lru_cache(maxsize=1)
def _rows() -> tuple[dict, ...]:
    """CSV 전체를 화면용 dict 로 한 번만 변환해 재사용한다. 읽기 전용으로만 쓴다."""
    return tuple(all_plans())


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class RecommendRequest(BaseModel):
    # 프론트가 대화 전체를 매번 보낸다. 서버는 상태를 갖지 않는다.
    messages: list[Message] = Field(..., min_length=1)


class AskRequest(BaseModel):
    planId: str
    question: str


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/recommend")
def recommend(req: RecommendRequest) -> dict:
    """LLM 4단계 파이프라인. 20~60초 걸린다."""
    history = [
        HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content)
        for m in req.messages
    ]
    try:
        state = graph.invoke({"messages": history})
    except Exception as exc:  # LLM 장애·키 누락은 화면이 이유를 보여줘야 한다
        raise HTTPException(status_code=502, detail=f"추천 파이프라인 실패: {exc}") from exc

    profile = state.get("profile")
    ranked = [plan.model_dump() for plan in state.get("ranked", [])]
    evaluation = state.get("evaluation")

    candidates = state.get("candidates", [])
    reference = state.get("reference")
    followup = (profile.followup_question if profile else None) or state.get("clarification_question")
    plans = to_plan_items(candidates, ranked)

    return {
        "plans": plans,
        # 데이터·요금을 둘 다 못 잡아 추천 전에 멈춘 경우. 화면은 결과 대신 질문을 띄운다.
        "needsMoreInput": not plans and bool(followup),
        "candidateCount": len(candidates),
        "totalCount": len(_rows()),
        "report": state.get("report", ""),
        # 선택한 추천 상품과 현재 요금제를 화면에서 직접 비교할 수 있도록 원본 기준 상품도 전달한다.
        "referencePlan": to_plan_item(reference) if reference else None,
        "profile": profile.model_dump(exclude_none=True) if profile else None,
        # 조건이 부족해도 대개 추천은 낸다. 질문은 결과와 함께 내려보내 화면에서 이어 묻는다.
        "followupQuestion": followup,
        "assumptions": profile.assumptions if profile else [],
        "evaluation": evaluation.model_dump() if evaluation else None,
    }


@lru_cache(maxsize=1)
def _facets() -> dict:
    """필터 항목별 건수는 전체 데이터 기준 고정값이라 한 번만 센다."""
    return facet_counts(list(_rows()))


@app.get("/api/stats")
def stats() -> dict:
    """홈 화면 커버리지 숫자. CSV 를 그대로 센다."""
    rows = list(_rows())
    brands = {r["mvno_brand"] for r in rows if r["mvno_brand"]}
    return {
        "total": len(rows),
        "mvno": sum(1 for r in rows if r["carrier_type"] == "MVNO"),
        "mno": sum(1 for r in rows if r["carrier_type"] == "MNO"),
        "brands": len(brands),
        "compareMonths": COMPARE_MONTHS,
        # 필터 항목별 건수. 전체 기준 고정값이라 화면에서 그대로 표시한다.
        "facets": _facets(),
    }


@app.get("/api/plans")
def list_plans(
    q: Optional[str] = None,
    networks: str = "",
    data: str = "",
    voice: str = "",
    flags: str = "",
    price: str = "",
    sort: str = "fee_asc",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """탐색 화면. 그룹 안에서는 OR, 그룹 사이에서는 AND 로 걸린다."""
    if sort not in SORTS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 정렬: {sort}")
    selected = {
        group: [key for key in value.split(",") if key]
        for group, value in (("networks", networks), ("data", data), ("voice", voice), ("flags", flags), ("price", price))
    }
    rows = apply_filters(list(_rows()), selected, q)

    key, reverse = SORTS[sort]
    rows.sort(key=key, reverse=reverse)

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    start = (page - 1) * page_size
    return {
        "total": len(rows),
        "page": page,
        "pageSize": page_size,
        "plans": to_plan_items(rows[start : start + page_size]),
    }


@app.get("/api/plans/{plan_id}")
def plan_detail(plan_id: str) -> dict:
    row = get_plan(plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail="요금제를 찾을 수 없습니다.")
    return to_plan_item(row)


@app.post("/api/ask")
def ask(req: AskRequest) -> dict:
    """특정 요금제에 대한 단발 질문. 대화 이력 없음."""
    row = get_plan(req.planId)
    if row is None:
        raise HTTPException(status_code=404, detail="요금제를 찾을 수 없습니다.")
    prompt = (
        "아래 요금제 데이터만 근거로 사용자 질문에 2~3문장으로 답하라. "
        "데이터에 없는 내용은 '제공된 자료로는 확인되지 않습니다'라고 답하고 추측하지 마라.\n\n"
        f"[요금제]\n{row}\n\n[질문]\n{req.question}"
    )
    try:
        answer = get_llm().invoke(prompt).content
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"응답 생성 실패: {exc}") from exc
    return {"answer": answer}
