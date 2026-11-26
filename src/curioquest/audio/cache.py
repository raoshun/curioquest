"""TTS 合成キャッシュ（08-voice-cache 13-3）。

同じテキスト・同じ声の設定での再合成を打ち切るデコレータ。エンジン種別・
バージョンタグ・話者・語速・音高を交差に含むため、声の設定が変われば
キーが変わり、能動的な無効化なしに古い音は再利用されない。

不変条件:
- 合成失敗は内側アダプタの例外をそのまま伝搬し、何もキャッシュしない
  （API 層のテキスト第一優先フォールバックは voice.py のまま不変）。
- キャッシュするのは合成出力（WAV）だけであり、マイク入力音声は扱わない。
  05-safety-quality の「音声を入力として保存しない」方針には抵触しない。
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

from curioquest.audio.base import CharacterVoice, TextToSpeech

logger = logging.getLogger("curioquest.audio.cache")

# メモリに保持するエントリ数上限。定型文中心では 128 で十分収まる
# （08-voice-cache 13-4 の個数見積もり）。超過分は LRU でディスクへ退避する。
DEFAULT_MEMORY_MAX_ENTRIES = 128


class CachingTts:
    """内側 TTS の合成結果をメモリ + ディスク(WAV)へキャッシュする。

    cache_dir は db_path と同じデータ領域配下を使う（デフォルト設定では
    data/tts_cache/）。ファイル名は交差の SHA-256 で、テキストを直接
    保存しないため日本語テキストのファイル名問題も起きない。
    """

    def __init__(
        self,
        inner: TextToSpeech,
        cache_dir: str | Path,
        *,
        engine_tag: str = "voicevox-v1",
        default_speaker_id: int | None = None,
        memory_max_entries: int = DEFAULT_MEMORY_MAX_ENTRIES,
    ) -> None:
        self.inner = inner
        self.cache_dir = Path(cache_dir)
        self.engine_tag = engine_tag
        self.default_speaker_id = default_speaker_id
        self._memory_max = max(1, memory_max_entries)
        self._memory: OrderedDict[str, bytes] = OrderedDict()
        # 同一キーの同時ミスで合成が多重走らないためのキー別ロック
        self._key_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self.hits = 0
        self.misses = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def synthesize(self, text: str, voice: CharacterVoice) -> bytes:
        speaker_id = voice.speaker_id if voice.speaker_id is not None else self.default_speaker_id
        if speaker_id is None:
            # 話者が解決できない合成はキャッシュ対象にしない（内側で設定エラー）
            return self.inner.synthesize(text, voice)
        resolved = replace(voice, speaker_id=speaker_id)
        key = self._cache_key(text, resolved)

        cached = self._read(key)
        if cached is not None:
            self.hits += 1
            return cached

        with self._key_lock(key):
            # ロック取得待ちの間に他スレッドが合成済みかもしれない（二重チェック）
            cached = self._read(key)
            if cached is not None:
                self.hits += 1
                return cached
            self.misses += 1
            wav = self.inner.synthesize(text, resolved)
            self._write(key, wav)
            return wav

    def warm(self, texts: list[str], voices: list[CharacterVoice]) -> int:
        """起動時ウォームアップ（08-voice-cache 13-1 固定応答 × キャラ）。

        失敗した組は警告のみで続行する（VOICEVOX 未起動で起動を止めない）。
        合成できた数を返す。
        """
        synthesized = 0
        for voice in voices:
            for text in texts:
                try:
                    before = self.misses
                    self.synthesize(text, voice)
                    if self.misses > before:
                        synthesized += 1
                except Exception as exc:  # noqa: BLE001 - 起動を止めない
                    logger.warning("ウォームアップ合成に失敗しました（継続）: %s", exc)
        return synthesized

    def clear(self) -> int:
        """キャッシュ全消去。削除したディスク エントリ数を返す（おとなエリア用）。"""
        self._memory.clear()
        removed = 0
        if self.cache_dir.is_dir():
            for path in self.cache_dir.glob("*.wav"):
                try:
                    path.unlink()
                    removed += 1
                except OSError as exc:
                    logger.warning("キャッシュ削除に失敗しました %s: %s", path.name, exc)
        with self._locks_guard:
            self._key_locks.clear()
        return removed

    # -------------------------------------------------------------- helpers

    def _cache_key(self, text: str, voice: CharacterVoice) -> str:
        cross = "\x1f".join(
            (
                self.engine_tag,
                text,
                str(voice.speaker_id),
                repr(voice.speed_scale),
                repr(voice.pitch_scale),
            )
        )
        return hashlib.sha256(cross.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.wav"

    def _key_lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._key_locks.setdefault(key, threading.Lock())

    def _read(self, key: str) -> bytes | None:
        wav = self._memory.get(key)
        if wav is not None:
            self._memory.move_to_end(key)
            return wav
        path = self._path(key)
        try:
            wav = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            # 壊れたキャッシュファイルは miss 扱いとし、再合成で上書きさせる
            logger.warning("キャッシュ読み出しに失敗しました %s: %s", path.name, exc)
            return None
        self._remember(key, wav)
        return wav

    def _write(self, key: str, wav: bytes) -> None:
        self._remember(key, wav)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._path(key)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(wav)
            os.replace(tmp, path)
        except OSError as exc:
            # 書き込み失敗は失速要因にならないよう警告だけ（メモリ分は有効）
            logger.warning("キャッシュ書き込みに失敗しました %s: %s", path.name, exc)

    def _remember(self, key: str, wav: bytes) -> None:
        self._memory[key] = wav
        self._memory.move_to_end(key)
        while len(self._memory) > self._memory_max:
            self._memory.popitem(last=False)
