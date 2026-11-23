"""音声送受信ターン API（03-architecture 12 の「音声を受け取る API」）。

音声バイト → 認識 → 対話ターン → 合成音の返却までを 1 リクエストで行う。
音声バイトは認識にのみ使い保存しない。認識できない場合は対話ターンを実行
せず 503 を返す（失敗した音声で利用時間を消費しない）。合成できない・合成に
失敗した場合は対話ターン結果を残した上で voice_available=false とし、
テキストの応答を届ける（テキスト第一優先）。
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Request

from curioquest.api.deps import API, AppComponents, cq, day_or_today
from curioquest.api.schemas import TurnOut, VoiceOut
from curioquest.audio import AudioError, voice_from_character

# アップロード音声の上限。マイクは 60 秒までで、WAV 48kHz・16bit・モノでも
# 約 5.5MiB。余裕を見て 12MiB とし、超過は即座に 413 で弾く（メモリ枯渇防止）。
MAX_VOICE_BODY_BYTES = 12 * 1024 * 1024
_TOO_LARGE_DETAIL = "音声がが大きすぎて送れなかったよ。みじかい はなしで もういっかい ためして。"


async def _read_voice_body(request: Request) -> bytes:
    """サイズ上限付きで音声本文を読む。宣言値（Content-Length）・実測の両方を検査する。"""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_VOICE_BODY_BYTES:
        raise HTTPException(status_code=413, detail=_TOO_LARGE_DETAIL)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_VOICE_BODY_BYTES:
            raise HTTPException(status_code=413, detail=_TOO_LARGE_DETAIL)
    return bytes(body)


def add_voice_routes(app: FastAPI) -> None:
    """アプリへ音声ターン ルートを登録する（create_app から呼ぶ）。"""

    @app.post(f"{API}/sessions/{{session_id}}/voice", response_model=VoiceOut)
    async def post_voice(
        session_id: str,
        request: Request,
        seconds_spent: int = Query(default=0, ge=0, le=3600),
        day: str | None = Query(default=None),
    ) -> VoiceOut:
        comp = cq(request)
        if comp.stt is None:
            raise HTTPException(status_code=503, detail="この設定では音声を入力できません")
        audio = await _read_voice_body(request)
        try:
            text = comp.stt.transcribe(audio)
        except AudioError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"音声認識に失敗したため発話を処理できませんでした: {exc}",
            ) from exc
        result = comp.engine.handle_turn(
            session_id,
            text,
            seconds_spent=seconds_spent,
            day=day_or_today(day),
        )
        return _voice_out(comp, TurnOut.from_result(result), result.session.character_id)


def _voice_out(comp: AppComponents, turn: TurnOut, character_id: str) -> VoiceOut:
    """応答テキストをキャラクターの声で合成する。できなければテキストだけで返す。"""
    if comp.tts is None:
        return VoiceOut.from_turn(turn, None, audio_error="この設定では音声出力を使えません")
    try:
        character = comp.store.get_character(character_id)
        wav = comp.tts.synthesize(turn.reply, voice_from_character(character))
    except AudioError as exc:
        return VoiceOut.from_turn(turn, None, audio_error=f"音声合成に失敗しました: {exc}")
    return VoiceOut.from_turn(turn, wav)
