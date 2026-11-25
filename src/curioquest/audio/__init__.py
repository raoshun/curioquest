"""音声処理パッケージ。

STT・TTS はインターフェースのみを既定で公開し、実エンジンは遅延インポートで
読み込む（テスト環境に重い依存を要求しない）。faster-whisper は
curioquest.audio.whisper を import しても重いライブラリ自体は読まない。
"""

from __future__ import annotations

from curioquest.audio.base import (
    AudioError,
    AudioServiceUnavailable,
    CharacterVoice,
    SpeechToText,
    TextToSpeech,
    VoiceNotConfigured,
    voice_from_character,
)
from curioquest.audio.cache import CachingTts
from curioquest.audio.fake import FakeStt, FakeTts
from curioquest.audio.voicevox import VoiceVoxTts
from curioquest.audio.whisper import FasterWhisperStt

__all__ = [
    "AudioError",
    "AudioServiceUnavailable",
    "CachingTts",
    "CharacterVoice",
    "FasterWhisperStt",
    "FakeStt",
    "FakeTts",
    "SpeechToText",
    "TextToSpeech",
    "VoiceNotConfigured",
    "VoiceVoxTts",
    "voice_from_character",
]
