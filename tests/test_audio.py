"""STT・TTS アダプタのテスト。

VoiceVoxTts は httpx.MockTransport、FasterWhisperStt はダミーモデル注入で
検証する。実エンジン（ネットワーク・重いモデル）は一切使わない。
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from curioquest.audio import (
    AudioServiceUnavailable,
    CharacterVoice,
    FakeStt,
    FakeTts,
    SpeechToText,
    TextToSpeech,
    VoiceNotConfigured,
    VoiceVoxTts,
    voice_from_character,
)
from curioquest.audio.whisper import INSTALL_HINT, FasterWhisperStt
from curioquest.domain.models import Character

VOICEVOX_URL = "http://voicevox.test"


def make_character(**overrides: Any) -> Character:
    base: dict[str, Any] = {
        "id": "char-001",
        "name": "ラボ博士",
        "description": "科学が好きな博士",
        "personality": "好奇心旺盛",
        "thinking_style": "仮説を立てて考える",
        "system_prompt": "あなたはラボ博士です。",
    }
    base.update(overrides)
    return Character(**base)


def make_tts(handler: Any, **kwargs: Any) -> tuple[VoiceVoxTts, httpx.Client]:
    """MockTransport を差し込んだ VoiceVoxTts と、後検証用クライアントを返す。"""
    client = httpx.Client(
        base_url=VOICEVOX_URL,
        transport=httpx.MockTransport(handler),
    )
    tts = VoiceVoxTts(base_url=VOICEVOX_URL, client=client, **kwargs)
    return tts, client


def ok_audio_query() -> dict[str, Any]:
    return {"speedScale": 1.0, "pitchScale": 0.0, "intonationScale": 1.0, "volumeScale": 1.0}


def default_handler(seen: list[httpx.Request]) -> Any:
    """標準的な 2 応答（audio_query → wav）のハンドラ。"""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/audio_query":
            return httpx.Response(200, json=ok_audio_query())
        if request.url.path == "/synthesis":
            return httpx.Response(200, content=b"WAVDATA", headers={"content-type": "audio/wav"})
        return httpx.Response(404)

    return handler


class TestVoiceFromCharacter:
    def test_maps_voice_fields(self) -> None:
        character = make_character(
            voice_speaker_id=42,
            voice_speed_scale=1.3,
            voice_pitch_scale=-0.2,
        )
        voice = voice_from_character(character)
        assert voice == CharacterVoice(
            engine="voicevox",
            speaker_id=42,
            speed_scale=1.3,
            pitch_scale=-0.2,
        )

    def test_defaults(self) -> None:
        voice = voice_from_character(make_character())
        assert voice.engine == "voicevox"
        assert voice.speaker_id is None
        assert voice.speed_scale == 1.0
        assert voice.pitch_scale == 0.0


class TestProtocolConformance:
    def test_fakes_conform(self) -> None:
        assert isinstance(FakeStt(), SpeechToText)
        assert isinstance(FakeTts(), TextToSpeech)

    def test_adapters_conform(self) -> None:
        assert isinstance(VoiceVoxTts(), TextToSpeech)
        assert isinstance(make_stt(FakeWhisperModel([])), SpeechToText)

    def test_object_does_not_conform(self) -> None:
        assert not isinstance(object(), SpeechToText)
        assert not isinstance(object(), TextToSpeech)


class TestFakeStt:
    def test_fifo_then_default(self) -> None:
        stt = FakeStt(texts=["あ", "い"], default_text="終わり")
        assert stt.transcribe(b"1") == "あ"
        assert stt.transcribe(b"22") == "い"
        assert stt.transcribe(b"333") == "終わり"

    def test_does_not_retain_audio_bytes(self) -> None:
        stt = FakeStt()
        stt.transcribe(b"secret-voice" * 10)
        assert stt.audio_sizes == [120]
        retained = [v for v in vars(stt).values() if isinstance(v, bytes)]
        assert retained == []

    def test_default_text_empty_when_unprimed(self) -> None:
        assert FakeStt().transcribe(b"x") == ""


class TestFakeTts:
    def test_fifo_then_default_audio(self) -> None:
        voice = CharacterVoice(speaker_id=7)
        tts = FakeTts(voices=[b"A", b"B"])
        assert tts.synthesize("一", voice) == b"A"
        assert tts.synthesize("二", voice) == b"B"
        assert tts.synthesize("三", voice) == b"RIFF0000WAVEfake"

    def test_records_calls_without_volumes(self) -> None:
        voice = CharacterVoice(speaker_id=7)
        tts = FakeTts()
        tts.synthesize("こんにちは", voice)
        assert tts.calls == [{"text": "こんにちは", "voice": voice}]
        assert not any(isinstance(v, bytes) for v in vars(tts).values())


class TestVoiceVoxTts:
    def test_synthesize_uses_default_speaker(self) -> None:
        seen: list[httpx.Request] = []
        tts, _ = make_tts(default_handler(seen), default_speaker_id=5)
        audio = tts.synthesize("先生", CharacterVoice())
        assert audio == b"WAVDATA"
        query, synthesis = seen
        assert query.method == "POST"
        assert query.url.path == "/audio_query"
        assert query.url.params["speaker"] == "5"
        assert query.url.params["text"] == "先生"
        assert synthesis.url.path == "/synthesis"
        assert synthesis.url.params["speaker"] == "5"

    def test_synthesis_sends_character_scales(self) -> None:
        seen: list[httpx.Request] = []
        tts, _ = make_tts(default_handler(seen), default_speaker_id=5)
        voice = CharacterVoice(speaker_id=None, speed_scale=1.3, pitch_scale=-0.2)
        tts.synthesize("問い", voice)
        sent = json.loads(seen[1].content)
        assert sent["speedScale"] == 1.3
        assert sent["pitchScale"] == -0.2
        assert sent["intonationScale"] == 1.0

    def test_character_speaker_wins_over_default(self) -> None:
        seen: list[httpx.Request] = []
        tts, _ = make_tts(default_handler(seen), default_speaker_id=5)
        tts.synthesize("こんにちは", CharacterVoice(speaker_id=42))
        assert seen[0].url.params["speaker"] == "42"
        assert seen[1].url.params["speaker"] == "42"

    def test_japanese_is_url_encoded(self) -> None:
        seen: list[httpx.Request] = []
        tts, _ = make_tts(default_handler(seen), default_speaker_id=5)
        tts.synthesize("先生", CharacterVoice())
        assert "%E5%85%88%E7%94%9F" in str(seen[0].url)

    def test_voice_not_configured_before_http(self) -> None:
        seen: list[httpx.Request] = []
        tts, _ = make_tts(default_handler(seen))
        with pytest.raises(VoiceNotConfigured):
            tts.synthesize("hello", CharacterVoice())
        assert seen == []

    def test_query_http_error_maps_to_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        tts, _ = make_tts(handler, default_speaker_id=5)
        with pytest.raises(AudioServiceUnavailable, match="クエリ取得"):
            tts.synthesize("hello", CharacterVoice())

    def test_synthesis_http_error_maps_to_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/audio_query":
                return httpx.Response(200, json=ok_audio_query())
            return httpx.Response(500)

        tts, _ = make_tts(handler, default_speaker_id=5)
        with pytest.raises(AudioServiceUnavailable, match="合成"):
            tts.synthesize("hello", CharacterVoice())

    def test_connection_error_maps_to_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        tts, _ = make_tts(handler, default_speaker_id=5)
        with pytest.raises(AudioServiceUnavailable, match="refused"):
            tts.synthesize("hello", CharacterVoice())

    def test_list_speakers(self) -> None:
        payload = [{"name": "四国めたん", "styles": [{"id": 3, "name": "スタイル"}]}]

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/speakers"
            return httpx.Response(200, json=payload)

        tts, _ = make_tts(handler)
        assert tts.list_speakers() == payload

    def test_lazy_client_and_close(self) -> None:
        tts = VoiceVoxTts(base_url=VOICEVOX_URL, default_speaker_id=3)
        client = tts.client
        assert isinstance(client, httpx.Client)
        assert tts.client is client
        tts.close()
        assert client.is_closed
        tts.close()  # 冪等

    def test_context_manager_closes_client(self) -> None:
        seen: list[httpx.Request] = []
        tts, client = make_tts(default_handler(seen), default_speaker_id=5)
        with tts as active:
            assert active.synthesize("こんにちは", CharacterVoice()) == b"WAVDATA"
        assert client.is_closed


def make_stt(model: Any, **kwargs: Any) -> FasterWhisperStt:
    """ダミーモデルを注入した FasterWhisperStt を返す。"""
    stt = FasterWhisperStt(**kwargs)
    stt._model = model
    return stt


class FakeWhisperModel:
    """faster_whisper.WhisperModel を模したダミー。"""

    def __init__(self, texts: list[str] | None = None, error: Exception | None = None) -> None:
        self.texts = texts or []
        self.error = error
        self.calls: list[tuple[bytes, dict[str, Any]]] = []

    def transcribe(self, audio: Any, **kwargs: Any) -> Any:
        self.calls.append((audio.read(), kwargs))
        if self.error is not None:
            raise self.error
        segments = (SimpleNamespace(text=t) for t in self.texts)
        return segments, SimpleNamespace(language="ja")


class TestFasterWhisperStt:
    def test_import_does_not_load_library(self) -> None:
        assert "faster_whisper" not in sys.modules
        make_stt(FakeWhisperModel(["よっ"]))
        assert "faster_whisper" not in sys.modules

    def test_transcribe_joins_segments(self) -> None:
        model = FakeWhisperModel(texts=["ペンギンは", " とべない"])
        stt = make_stt(model)
        assert stt.transcribe(b"RIFFfakedata") == "ペンギンは とべない"
        audio, kwargs = model.calls[0]
        assert audio == b"RIFFfakedata"
        assert kwargs == {"language": "ja", "beam_size": 5}

    def test_transcribe_empty_returns_empty_string(self) -> None:
        stt = make_stt(FakeWhisperModel([]))
        assert stt.transcribe(b"x") == ""

    def test_config_passes_through(self) -> None:
        model = FakeWhisperModel(["あ"])
        stt = make_stt(model, language=None, beam_size=1)
        stt.transcribe(b"x")
        assert model.calls[0][1] == {"language": None, "beam_size": 1}

    def test_model_error_wrapped(self) -> None:
        stt = make_stt(FakeWhisperModel(error=RuntimeError("cuda oom")))
        with pytest.raises(AudioServiceUnavailable, match="音声認識に失敗"):
            stt.transcribe(b"x")

    def test_missing_library_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # sys.modules 上の None マーカーは ImportError を起こす
        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        stt = FasterWhisperStt()
        with pytest.raises(AudioServiceUnavailable) as exc_info:
            stt.transcribe(b"x")
        assert INSTALL_HINT in str(exc_info.value)
