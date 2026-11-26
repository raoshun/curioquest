"""サーバー起動のエントリーポイント。

uvicorn の --factory オプションから呼ぶことを想定する:

    uvicorn --factory curioquest.api.main:create_standalone_app

CURIOQUEST_* 環境変数を読み込んで、その場でコンポーネントを組み
立てる。モデルのダウンロードや音声エンジンへの接続は発生しない
（遅延接続のため、起動自体は常に軽い）。
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI

from curioquest.api.app import create_app
from curioquest.api.deps import build_components, start_voice_warmup
from curioquest.config import settings_from_env

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
ENV_HOST = "CURIOQUEST_HOST"
ENV_PORT = "CURIOQUEST_PORT"


def create_standalone_app() -> FastAPI:
    """環境変数からアプリを1つ組み立てる（起動時に1回だけ呼ばれる）。"""
    settings = settings_from_env()
    components = build_components(settings)
    if settings.tts_warmup_enabled:
        # 定型応答の音作りは裏で。VOICEVOX 未起動でも起動は止めない (Issue #8)
        start_voice_warmup(components)
    return create_app(components)


def main() -> None:
    """`curioquest-api` コンソールコマンド。UVICORN_FLAGS 相当は環境変数のみ対応。"""
    host = os.environ.get(ENV_HOST, DEFAULT_HOST)
    port = int(os.environ.get(ENV_PORT, str(DEFAULT_PORT)))
    uvicorn.run(create_standalone_app(), host=host, port=port)


if __name__ == "__main__":
    main()
