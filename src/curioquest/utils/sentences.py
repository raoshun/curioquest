"""ストリームテキストを音声合成向きの文単位へ分割する。

LLM の応答差分を逐次入力し、文末記号まで揃った文だけを順に返す。
短すぎる断片 (相槌など) は次の文と結合し、区切りが無いまま長くなった場合は
読点・空白で無理のない位置か、それも無ければ最大長で強制分割する。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 文末とみなす記号。改行も区切りとして扱う。
SENTENCE_END_CHARS = "。．.!?！？…\n"
# 音声合成に出す最小の文字数。これ未満の断片は次の文と結合する。
DEFAULT_MIN_SEGMENT_CHARS = 4
# 区切りの無いまま蓄積できる最大長。超えたら強制分割する。
DEFAULT_MAX_SEGMENT_CHARS = 120


def _has_voice(segment: str) -> bool:
    """記号だけ・空白だけのセグメントを除外する。"""
    return any(ch.isalnum() for ch in segment)


@dataclass
class SentenceAssembler:
    """feed()/flush() で文単位セグメントを順に得る状態機械。

    feed(): テキスト差分を渡すと、完成したセグメント一覧を返す (0 以上)。
    flush(): 入力が終わった時点で残りをすべてセグメントとして返す。
    """

    min_segment_chars: int = DEFAULT_MIN_SEGMENT_CHARS
    max_segment_chars: int = DEFAULT_MAX_SEGMENT_CHARS
    _pending: str = field(default="", init=False, repr=False)

    def feed(self, text: str) -> list[str]:
        segments: list[str] = []
        for char in text:
            self._pending += char
            if char in SENTENCE_END_CHARS:
                candidate = self._pending.strip()
                if len(candidate) >= self.min_segment_chars:
                    self._consume(segments, self._pending)
            if len(self._pending) > self.max_segment_chars:
                self._force_split(segments)
        return segments

    def flush(self) -> list[str]:
        segments: list[str] = []
        if self._pending:
            self._consume(segments, self._pending)
        return segments

    # -- 内部補助 ---------------------------------------------------------

    def _consume(self, segments: list[str], raw: str) -> None:
        self._pending = ""
        self._emit(segments, raw)

    def _emit(self, segments: list[str], raw: str) -> None:
        segment = raw.strip()
        if segment and _has_voice(segment):
            segments.append(segment)

    def _force_split(self, segments: list[str]) -> None:
        """読点・空白のいずれか直後で切り、無ければ最大長で切る。"""
        window = self._pending[: self.max_segment_chars]
        cut = max(window.rfind(marker) + 1 for marker in ("、", " ", "\u3000"))
        if cut <= 0:
            cut = self.max_segment_chars
        head, self._pending = self._pending[:cut], self._pending[cut:]
        self._emit(segments, head)
