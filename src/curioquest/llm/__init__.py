"""LLM プロバイダパッケージ。

既定で OpenAI 互換プロバイダを提供する。高速化・評価分離の設定は Settings 経由。
"""

from __future__ import annotations

from curioquest.llm.base import (
    ChatMessage,
    GeneratedMission,
    LLMError,
    LLMProvider,
    SchemaParser,
    ThinkingEvaluation,
)
from curioquest.llm.fake import FakeLLM
from curioquest.llm.openai_compat import OpenAICompatClient, OpenAICompatLLM

__all__ = [
    "ChatMessage",
    "FakeLLM",
    "GeneratedMission",
    "LLMError",
    "LLMProvider",
    "OpenAICompatClient",
    "OpenAICompatLLM",
    "SchemaParser",
    "ThinkingEvaluation",
]
