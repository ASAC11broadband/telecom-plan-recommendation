"""LLM 팩토리. 프로바이더를 바꾸려면 이 파일만 고치면 된다."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI

MODEL = "gpt-4o-mini"
TEMPERATURE = 0.0


def get_llm(config: RunnableConfig | None = None) -> BaseChatModel:
    """OPENAI_API_KEY 환경변수 필요."""
    return ChatOpenAI(model=MODEL, temperature=TEMPERATURE)
