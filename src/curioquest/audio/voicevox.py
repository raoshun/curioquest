"""VOICEVOX Engine による音声合成アダプタ（02-features 5-2、06-roadmap Phase 0）。

HTTP API は公式エンジン準拠:
1. POST /audio_query?speaker=&text= で AudioQuery 初期値を取得
2. AudioQuery の speedScale / pitchScale をキャラクター設定で上書き
3. POST /synthesis?speaker= へ AudioQuery を送って WAV を受け取る

話者 ID は設定で管理し、特定の話者に依存しない。キャラクターが未設定のときは
default_speaker_id にフォールバックする。
"""

from __future__ import annotations

from typing import Any

import httpx

from curioquest.audio.base import (
    AudioServiceUnavailable,
    CharacterVoice,
    VoiceNotConfigured,
)

DEFAULT_TIMEOUT_SECONDS = 30.0


class VoiceVoxTts:
    """VOICEVOX Engine へ HTTP アクセスして音声を合成する。

    base_url は Settings.voicevox_base_url（既定 http://localhost:50021）から渡す。
    default_speaker_id はキャラクターの話者 ID が未設定のときのフォールバック。
    client はテストで MockTransport を差し込めるよう注入可能な差し替え口。
    """

    def __init__(
        self,
        base_url: str = "http://localhost:50021",
        default_speaker_id: int | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url
        self.default_speaker_id = default_speaker_id
        self.timeout_seconds = timeout_seconds
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> VoiceVoxTts:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def synthesize(self, text: str, voice: CharacterVoice) -> bytes:
        speaker_id = voice.speaker_id if voice.speaker_id is not None else self.default_speaker_id
        if speaker_id is None:
            raise VoiceNotConfigured(
                "VOICEVOX の話者 ID が未設定です。キャラクターまたは "
                "CURIOQUEST_VOICEVOX 設定で話者 ID を指定してください"
            )
        query = self._create_audio_query(text, speaker_id)
        query["speedScale"] = voice.speed_scale
        query["pitchScale"] = voice.pitch_scale
        return self._synthesis(query, speaker_id)

    def list_speakers(self) -> list[dict[str, Any]]:
        """利用可能なスタイル一覧。話者 ID 設定の補助に使う。"""
        try:
            response = self.client.get("/speakers")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AudioServiceUnavailable(f"VOICEVOX に接続できません: {exc}") from exc
        speakers: list[dict[str, Any]] = response.json()
        return speakers

    def _create_audio_query(self, text: str, speaker_id: int) -> dict[str, Any]:
        try:
            response = self.client.post(
                "/audio_query", params={"speaker": speaker_id, "text": text}
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AudioServiceUnavailable(f"VOICEVOX のクエリ取得に失敗しました: {exc}") from exc
        query: dict[str, Any] = response.json()
        return query

    def _synthesis(self, query: dict[str, Any], speaker_id: int) -> bytes:
        try:
            response = self.client.post("/synthesis", params={"speaker": speaker_id}, json=query)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AudioServiceUnavailable(f"VOICEVOX の合成に失敗しました: {exc}") from exc
        return response.content
