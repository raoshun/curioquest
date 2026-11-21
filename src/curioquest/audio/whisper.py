"""faster-whisper による音声認識アダプタ（06-roadmap Phase 0 検証項目）。

faster-whisper はオプショナル依存（curioquest[stt]）。インポートは遅延させ、
テスト環境や軽量構成ではインストールされていなくてもパッケージ自体は読めるようにする。
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from curioquest.audio.base import AudioServiceUnavailable

INSTALL_HINT = (
    "faster-whisper が未インストールです。pip install 'curioquest[stt]' で導入してください"
)


@dataclass
class FasterWhisperStt:
    """ローカル faster-whisper モデルで音声バイトを認識する。

    model_name は Settings.stt_model（tiny/base/small など）から渡す。
    モデル初回ロードは重いので、最初の transcribe まで遅延ロードする。
    """

    model_name: str = "small"
    language: str | None = "ja"
    beam_size: int = 5
    _model: object | None = field(default=None, init=False, repr=False)

    def _load_model(self) -> object:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise AudioServiceUnavailable(INSTALL_HINT) from exc
            self._model = WhisperModel(self.model_name)
        return self._model

    def transcribe(self, audio: bytes) -> str:
        """音声バイトをテキストへ変換する。音声そのものは保存しない。"""
        model = self._load_model()
        buffer = io.BytesIO(audio)
        try:
            segments, _info = model.transcribe(
                buffer, language=self.language, beam_size=self.beam_size
            )
        except Exception as exc:  # faster-whisper は広い例外を投げるので分類不能として包む
            raise AudioServiceUnavailable(f"音声認識に失敗しました: {exc}") from exc
        return "".join(segment.text for segment in segments).strip()
