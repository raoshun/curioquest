"""音声認識（STT）・音声合成（TTS）の抽象化（02-features 4-1、03-architecture 10-2）。

不変条件: 音声バイトは認識後に保持しない（05-safety-quality）。
本パッケージのどのクラスも音声バイトをメンバ変数に保持しないことでこれを担保する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from curioquest.domain.models import Character


class AudioError(RuntimeError):
    """音声サービス関連のエラー基盤。"""


class AudioServiceUnavailable(AudioError):
    """STT/TTS エンジンへ到達できない、またはインストールされていない。"""


class VoiceNotConfigured(AudioError):
    """話者 ID が未設定。キャラクター定義または設定で指定する。"""


@dataclass(frozen=True)
class CharacterVoice:
    """キャラクターの音声パラメータ（02-features 5-2）。

    話者 ID は設定で管理し、特定の話者に依存しない設計とする。
    """

    engine: str = "voicevox"
    speaker_id: int | None = None
    speed_scale: float = 1.0
    pitch_scale: float = 0.0


def voice_from_character(character: Character) -> CharacterVoice:
    """ドメインの Character 定義から音声パラメータを組み立てる。"""
    return CharacterVoice(
        engine=character.voice_engine,
        speaker_id=character.voice_speaker_id,
        speed_scale=character.voice_speed_scale,
        pitch_scale=character.voice_pitch_scale,
    )


@runtime_checkable
class SpeechToText(Protocol):
    """音声 → テキスト。入力された音声バイトは変換後に破棄される前提。"""

    def transcribe(self, audio: bytes) -> str:
        """音声バイトを認識テキストへ変換する。認識できなければ空文字を返す。"""
        ...


@runtime_checkable
class TextToSpeech(Protocol):
    """テキスト → 音声（WAV バイト列）。"""

    def synthesize(self, text: str, voice: CharacterVoice) -> bytes:
        """指定キャラクターの声で音声を合成する。"""
        ...
