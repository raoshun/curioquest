"""決定論的な Fake STT/TTS。テスト・デモで実エンジン不要にする。

音声バイトは transcribe 呼び出しの記録に残さない（長さのみ記録し、不変条件を守る）。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from curioquest.audio.base import CharacterVoice


@dataclass
class FakeStt:
    """SpeechToText を満たすスタブ。

    texts: 認識結果を呼び出し順に消費する。尽きたら default_text。
    audio_sizes: 受け取った音声バイトの長さのみ記録する（音声は保持しない）。
    """

    texts: Sequence[str] = ()
    default_text: str = ""
    audio_sizes: list[int] = field(default_factory=list)
    _texts: deque[str] = field(default_factory=deque, init=False, repr=False)

    def __post_init__(self) -> None:
        self._texts = deque(self.texts)
        self.audio_sizes = []

    def transcribe(self, audio: bytes) -> str:
        self.audio_sizes.append(len(audio))
        return self._texts.popleft() if self._texts else self.default_text


@dataclass
class FakeTts:
    """TextToSpeech を満たすスタブ。

    voices: 合成結果の音声バイトを呼び出し順に消費する。尽きたら既定のダミー WAV。
    calls: (text, voice) を記録するので話者・話速の反映を検証できる。
    """

    voices: Sequence[bytes] = ()
    calls: list[dict[str, Any]] = field(default_factory=list)
    _voices: deque[bytes] = field(default_factory=deque, init=False, repr=False)

    def __post_init__(self) -> None:
        self._voices = deque(self.voices)
        self.calls = []

    def synthesize(self, text: str, voice: CharacterVoice) -> bytes:
        self.calls.append({"text": text, "voice": voice})
        if self._voices:
            return self._voices.popleft()
        return b"RIFF0000WAVEfake"
