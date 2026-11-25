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
ENV_MISSION_GENERATION = "CURIOQUEST_MISSION_GENERATION"
ENV_TTS_CACHE = "CURIOQUEST_TTS_CACHE"
ENV_TTS_CACHE_DIR = "CURIOQUEST_TTS_CACHE_DIR"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _env_flag(name: str, default: bool) -> bool:
    """環境変数を真偽値として読む。空・不明な値は default に従う。"""
    raw = os.environ.get(name, "").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return default


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
    # 個人最適ミッションの LLM 生成 (6-3)。材料が無い・LLM が失敗したときは定番DBへ倒す
    mission_generation_enabled: bool = True
    # TTS 合成キャッシュ (08-voice-cache 13-3)。None でデータ領域の data/tts_cache/
    tts_cache_enabled: bool = True
    tts_cache_dir: str | None = None


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
        mission_generation_enabled=_env_flag(ENV_MISSION_GENERATION, True),
        tts_cache_enabled=_env_flag(ENV_TTS_CACHE, True),
        tts_cache_dir=os.environ.get(ENV_TTS_CACHE_DIR) or None,
    )
