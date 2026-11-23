"""環境変数からの設定読み込み。

ホーム LAN 構成（03-architecture 10-1/10-2）で使う接続先を一元管理する。
秘密情報をリポジトリに置かず、すべて環境変数で与える。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_LLM_BASE_URL = "CURIOQUEST_LLM_BASE_URL"
ENV_LLM_MODEL = "CURIOQUEST_LLM_MODEL"
ENV_LLM_API_KEY = "CURIOQUEST_LLM_API_KEY"
ENV_VOICEVOX_BASE_URL = "CURIOQUEST_VOICEVOX_BASE_URL"
ENV_VOICEVOX_DEFAULT_SPEAKER = "CURIOQUEST_VOICEVOX_DEFAULT_SPEAKER"
ENV_STT_MODEL = "CURIOQUEST_STT_MODEL"
ENV_DB_PATH = "CURIOQUEST_DB_PATH"


@dataclass(frozen=True)
class Settings:
    """アプリ設定。デフォルトは自宅サーバーの一般的なポート構成。"""

    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen2.5"
    llm_api_key: str | None = None
    voicevox_base_url: str = "http://localhost:50021"
    # キャラクターに speaker が未設定の場合に使う既定の話声（VOICEVOX の ID）
    voicevox_default_speaker: int | None = None
    stt_model: str = "small"
    db_path: str = "data/curioquest.db"


def settings_from_env() -> Settings:
    """環境変数から設定を組み立てる。未設定の項目はデフォルトを使う。"""
    speaker_raw = os.environ.get(ENV_VOICEVOX_DEFAULT_SPEAKER, "").strip()
    return Settings(
        llm_base_url=os.environ.get(ENV_LLM_BASE_URL, "http://localhost:11434/v1"),
        llm_model=os.environ.get(ENV_LLM_MODEL, "qwen2.5"),
        llm_api_key=os.environ.get(ENV_LLM_API_KEY),
        voicevox_base_url=os.environ.get(ENV_VOICEVOX_BASE_URL, "http://localhost:50021"),
        voicevox_default_speaker=int(speaker_raw) if speaker_raw.isdigit() else None,
        stt_model=os.environ.get(ENV_STT_MODEL, "small"),
        db_path=os.environ.get(ENV_DB_PATH, "data/curioquest.db"),
    )
