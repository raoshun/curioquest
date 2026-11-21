"""Settings から LLM プロバイダを組み立てる。

`llm_base_url` が `fake://` のときは FakeLLM を返し、開発・テストでモデルを起動せずに済ませる。
"""

from __future__ import annotations

from curioquest.config import Settings
from curioquest.llm.base import LLMProvider
from curioquest.llm.fake import FakeLLM
from curioquest.llm.openai_compat import OpenAICompatLLM

FAKE_SCHEME = "fake://"


def provider_from_settings(settings: Settings) -> LLMProvider:
    """設定に応じた LLM プロバイダを生成する。"""
    if settings.llm_base_url.startswith(FAKE_SCHEME):
        return FakeLLM()
    return OpenAICompatLLM(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
    )
