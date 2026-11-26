"""SSE ストリーミング ルート。テキストの差分と音声セグメントを並行で届ける。

通常のターン API は応答生成と音声合成がすべて終わってから返すため、音声対話中に
画面が無反応になりがちだった (Issue #10)。このモジュールはターン処理を SSE で
ストリーム化し、LLM の差分 (delta) を即座に、音声は文単位で合成できた順 (audio) に
流す。ターン確定は最後の turn イベントで届く。
"""

from __future__ import annotations

import base64
import json
import logging
import queue
import threading
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from curioquest.api.deps import API, AppComponents, cq, day_or_today
from curioquest.api.schemas import MessageIn, TurnOut
from curioquest.api.voice import _read_voice_body
from curioquest.audio import AudioError, CharacterVoice, voice_from_character
from curioquest.db.store import NotFound
from curioquest.engine.engine import DailyLimitExceeded, SessionClosed
from curioquest.llm.base import LLMError
from curioquest.utils.sentences import SentenceAssembler

logger = logging.getLogger(__name__)

# SSE ストリーム全体の上限。LLM 生成＋文単位 TTS を織り込んでも余裕のある値。
STREAM_TIMEOUT_SEC = 300.0
# エンジンが黙っている間に空コメントを出し、プロキシ側のアイドル切断を防ぐ。
KEEPALIVE_INTERVAL_SEC = 15.0

_STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class _EngineFailure:
    """エンジン裏スレッドの例外マーカー。内部制御用でクライアントへは送らない。"""

    def __init__(self, error: Exception) -> None:
        self.error = error


class _EngineResult:
    """エンジン裏スレッドのターン結果マーカー。"""

    def __init__(self, result: Any) -> None:
        self.result = result


def _error_event(exc: Exception) -> dict[str, Any]:
    """エンジン／永続化の例外を SSE の error イベントへ変換する。"""
    if isinstance(exc, NotFound):
        return {"type": "error", "detail": "セッションが見つかりません", "status": 404}
    if isinstance(exc, (SessionClosed, DailyLimitExceeded)):
        return {"type": "error", "detail": str(exc), "status": 409}
    if isinstance(exc, LLMError):
        status = exc.status_code or 502
        return {"type": "error", "detail": str(exc), "status": status}
    if isinstance(exc, HTTPException):
        return {"type": "error", "detail": str(exc.detail), "status": exc.status_code}
    logger.exception("stream_turn unexpected failure", exc_info=exc)
    return {"type": "error", "detail": "ターン処理に失敗しました", "status": 500}


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_events(
    comp: AppComponents,
    *,
    session_id: str,
    text: str,
    seconds_spent: int,
    day: str,
    with_audio: bool,
    voice: CharacterVoice | None,
    extra_turn_fields: dict[str, Any] | None,
) -> Iterator[str]:
    """エンジン実行を裏スレッドに置いたまま SSE 行を yield する。

    delta はエンジンコールバックから、audio は 1 ワーカ TTS ジョブから同じキューへ
    流れ込む。クライアントは index 順で再生するため、到着順のまま流す。エンジン
    終了後は結果マーカーより前に積み上がった audio をすべて送り、turn で終える。
    """
    out: queue.Queue[dict[str, Any] | _EngineResult | _EngineFailure] = queue.Queue()
    assembler = SentenceAssembler()
    jobs: list[Future[None]] = []
    make_executor = with_audio and comp.tts is not None
    executor: ThreadPoolExecutor | None = (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="stream-tts")
        if make_executor
        else None
    )

    def submit_segment(segment: str) -> None:
        index = len(jobs)

        def job() -> None:
            assert comp.tts is not None and voice is not None
            try:
                wav = comp.tts.synthesize(segment, voice)
            except Exception as exc:  # noqa: BLE001 - 1 文の失敗でターンを止めない
                logger.info("stream_turn audio %d failed: %s", index, exc)
                out.put({"type": "audio_error", "index": index})
                return
            encoded = base64.b64encode(wav).decode()
            out.put({"type": "audio", "index": index, "data_b64": encoded})

        jobs.append(executor.submit(job))  # executor は with_audio 時のみ生成

    def on_delta(delta: str) -> None:
        out.put({"type": "delta", "text": delta})
        if executor is not None:
            for segment in assembler.feed(delta):
                submit_segment(segment)

    def run() -> None:
        try:
            result = comp.engine.handle_turn(
                session_id,
                text,
                seconds_spent=seconds_spent,
                day=day,
                on_delta=on_delta,
            )
            if executor is not None:
                for segment in assembler.flush():
                    submit_segment(segment)
            out.put(_EngineResult(result))
        except Exception as exc:  # noqa: BLE001 - 裏スレッドなので呼び出し元へ投げられない
            out.put(_EngineFailure(exc))

    thread = threading.Thread(target=run, name=f"stream-turn-{session_id}", daemon=True)
    thread.start()
    delivered = 0
    try:
        deadline = time.monotonic() + STREAM_TIMEOUT_SEC
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield _sse(
                    {"type": "error", "detail": "応答が長時間返りませんでした", "status": 504}
                )
                return
            try:
                item = out.get(timeout=min(remaining, KEEPALIVE_INTERVAL_SEC))
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if isinstance(item, _EngineFailure):
                yield _sse(_error_event(item.error))
                return
            if isinstance(item, _EngineResult):
                # 結果確定までに提出した音声セグメントをすべて届けてから turn を送る。
                total = len(jobs)
                while delivered < total:
                    wait = max(deadline - time.monotonic(), 0.001)
                    try:
                        late = out.get(timeout=min(wait, KEEPALIVE_INTERVAL_SEC))
                    except queue.Empty:
                        yield _sse(
                            {
                                "type": "error",
                                "detail": "音声合成が完了しませんでした",
                                "status": 504,
                            }
                        )
                        return
                    if isinstance(late, dict) and late.get("type") in ("audio", "audio_error"):
                        delivered += 1
                        yield _sse(late)
                turn = TurnOut.from_result(item.result)
                payload: dict[str, Any] = {"type": "turn", "turn": turn.model_dump(mode="json")}
                if extra_turn_fields:
                    payload.update(extra_turn_fields)
                yield _sse(payload)
                return
            if item.get("type") in ("audio", "audio_error"):
                delivered += 1
            yield _sse(item)
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


def _load_character_voice(comp: AppComponents, character_id: str) -> CharacterVoice:
    return voice_from_character(comp.store.get_character(character_id))


def add_streaming_routes(app: FastAPI) -> None:
    """アプリへ SSE ストリーミング ルートを登録する（create_app から呼ぶ）。"""

    def stream_response(
        comp: AppComponents,
        session_id: str,
        text: str,
        seconds_spent: int,
        day: str,
        *,
        with_audio: bool,
        extra_turn_fields: dict[str, Any] | None = None,
    ) -> StreamingResponse:
        if with_audio and comp.tts is not None:
            character_id = comp.store.get_session(session_id).character_id
            voice = _load_character_voice(comp, character_id)
        else:
            voice = None
        generator = _stream_events(
            comp,
            session_id=session_id,
            text=text,
            seconds_spent=seconds_spent,
            day=day,
            with_audio=with_audio,
            voice=voice,
            extra_turn_fields=extra_turn_fields,
        )
        return StreamingResponse(generator, media_type="text/event-stream", headers=_STREAM_HEADERS)

    @app.post(f"{API}/sessions/{{session_id}}/message/stream")
    async def post_message_stream(
        session_id: str, body: MessageIn, request: Request
    ) -> StreamingResponse:
        comp = cq(request)
        try:
            comp.store.get_session(session_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="セッションが見つかりません") from exc
        return stream_response(
            comp,
            session_id,
            body.text,
            body.seconds_spent,
            day_or_today(body.day),
            with_audio=False,
        )

    @app.post(f"{API}/sessions/{{session_id}}/voice/stream")
    async def post_voice_stream(
        session_id: str,
        request: Request,
        seconds_spent: int = Query(default=0, ge=0, le=3600),
        day: str | None = Query(default=None),
    ) -> StreamingResponse:
        comp = cq(request)
        if comp.stt is None:
            raise HTTPException(status_code=503, detail="この設定では音声を入力できません")
        try:
            comp.store.get_session(session_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="セッションが見つかりません") from exc
        audio = await _read_voice_body(request)
        try:
            transcript = await run_in_threadpool(comp.stt.transcribe, audio)
        except AudioError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"音声認識に失敗したため発話を処理できませんでした: {exc}",
            ) from exc
        return stream_response(
            comp,
            session_id,
            transcript,
            seconds_spent,
            day_or_today(day),
            with_audio=True,
            extra_turn_fields={"transcript": transcript},
        )
