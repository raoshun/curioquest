# CurioQuest バックエンド（FastAPI・faster-whisper STT をプロセス内に持つ）。
# TTS の VOICEVOX Engine は docker-compose.yml の別サービスとして併走させる。
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 依存だけ先にインストール（uv.lock がレイヤキャッシュの鍵）。
# [stt] 込みでもモデルのダウンロードは発生せず、接続は初回認識時のみ。
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev --extra stt

ADD . /app

# 本体（src/curioquest と static 同梱物）を同じ venv へ導入する。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra stt

# DB は /data ボリューム直下（初回起動でシード済みになる）。
ENV CURIOQUEST_DB_PATH=/data/curioquest.db

EXPOSE 8000

# --factory で起動時に組み立てる（設定はすべて環境変数経由）。
CMD ["uvicorn", "curioquest.api.main:create_standalone_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8000"]
