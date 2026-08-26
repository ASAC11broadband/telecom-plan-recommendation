from __future__ import annotations
import json
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from ..data import filter_candidates, find_candidate
from ..schemas import RankingResult
from ..state import PipelineState, feedback_block, get_llm

RECOMMEND_PROMPT = """ 더 좋은 요금제 추천해줘 
"""


def _make_prompt(profile: dict,
                 candidates: list[dict],
                 feedback: list[str]) -> str:
    return (
        f"{RECOMMEND_PROMPT}\n"
        f"{feedback_block({'feedback': feedback})}\n"
        f"[사용자 요구사항]\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n\n"
        f"[추천 후보 요금제]\n{json.dumps(candidates, ensure_ascii=False, indent=2)}"
    )


def recommend_node(state: PipelineState, config: RunnableConfig) -> dict:
    profile = state.get("profile")
    feedback = state.get("feedback", [])

    if profile is None:
        return {
            "ranked": [],
            "candidates": [],
            "recommend_note": "사용자 프로필이 없습니다.",
            "messages": [AIMessage(content="[recommend] profile 없음", name="recommend")],
        }

    profile_dict = profile.model_dump(exclude_none=True)
    candidates, recommend_note = filter_candidates(profile_dict, limit=20)

    # 같은 요금제가 plan_id 만 다른 행으로 여러 개 올라온다 (연령 조건 등).
    # 그대로 두면 TOP 3 가 전부 같은 이름으로 채워진다.
    seen: set[str] = set()
    candidates = [c for c in candidates if not (c["plan_name"] in seen or seen.add(c["plan_name"]))]

    if not candidates:
        return {
            "ranked": [],
            "candidates": [],
            "recommend_note": "조건을 만족하는 요금제가 없습니다.",
            "messages": [AIMessage(content="[recommend] 후보 요금제 없음", name="recommend")],
        }

    llm = get_llm(config).with_structured_output(RankingResult)
    result = llm.invoke([SystemMessage(content=_make_prompt(profile_dict, candidates, feedback))])

    # 후보에 실제로 있는 요금제만 신뢰 (LLM 이 이름에 접미사를 붙이는 경우 대비 부분일치)
    ranked = [p for p in result.plans if find_candidate(candidates, p.plan_name)][:3]

    return {
        "candidates": candidates,
        "recommend_note": recommend_note,
        "ranked": ranked,
        "messages": [
            AIMessage(
                content=f"[recommend] 후보 {len(candidates)}개 → TOP {len(ranked)}개",
                name="recommend",
            )
        ],
    }

# 프로필 -> TOdo 이므로 가짜프로필 생성

# if __name__ == "__main__":
#     # profiling 이 아직 TODO 라 프로필을 직접 만들어 이 단계만 돌려본다.
#     #   python -m agent.agents.recommend
#     from ..schemas import UserProfile

#     for profile in [
#         UserProfile(budget_max_won=30000, min_data_gb=50),
#         UserProfile(budget_max_won=50000, data_unlimited=True, ott_wanted=["넷플릭스"]),
#     ]:
#         print(f"\n=== {profile.model_dump(exclude_none=True)}")
#         out = recommend_node({"profile": profile}, None)
#         print(f"후보 {len(out['candidates'])}개")
#         for p in out["ranked"]:
#             print(f"  {p.score:3d} | {p.plan_name[:34]:36s} | {p.reason[:60]}")
#         if out["recommend_note"]:
#             print(f"  NOTE: {out['recommend_note'][:60]}")
