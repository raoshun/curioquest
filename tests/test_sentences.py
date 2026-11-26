"""SentenceAssembler の文分割テスト。"""

from __future__ import annotations

from curioquest.utils.sentences import SentenceAssembler


class TestSentenceAssembler:
    def test_splits_on_japanese_full_stop(self) -> None:
        asm = SentenceAssembler()
        segments = asm.feed("そらってね、ひかりの まほうなんだ。どうして だと おもう？")
        assert segments == ["そらってね、ひかりの まほうなんだ。", "どうして だと おもう？"]
        assert asm.flush() == []

    def test_buffers_until_sentence_completes(self) -> None:
        asm = SentenceAssembler()
        assert asm.feed("なぜ だと") == []
        assert asm.feed(" おもう？") == ["なぜ だと おもう？"]

    def test_merges_short_interjections_with_next_sentence(self) -> None:
        asm = SentenceAssembler()
        assert asm.feed("うん。") == []
        assert asm.feed("その あかりは なにかな。") == ["うん。その あかりは なにかな。"]

    def test_flush_emits_trailing_remainder(self) -> None:
        asm = SentenceAssembler()
        assert asm.feed("さいごの つづき") == []
        assert asm.flush() == ["さいごの つづき"]
        assert asm.flush() == []

    def test_flush_drops_punctuation_only_remainder(self) -> None:
        asm = SentenceAssembler()
        assert asm.feed("！！！") == []
        assert asm.flush() == []

    def test_handles_newline_as_delimiter(self) -> None:
        asm = SentenceAssembler()
        segments = asm.feed("第一の こたえ。\n第二の こたえです")
        assert segments == ["第一の こたえ。"]
        assert asm.flush() == ["第二の こたえです"]

    def test_force_splits_long_text_without_delimiter(self) -> None:
        asm = SentenceAssembler(max_segment_chars=20)
        segments = asm.feed("あ" * 45)
        assert all(len(seg) <= 20 for seg in segments)
        tail = asm.flush()
        assert "".join(segments) + "".join(tail) == "あ" * 45

    def test_force_split_prefers_comma(self) -> None:
        asm = SentenceAssembler(max_segment_chars=20)
        text = "い" * 15 + "、" + "う" * 15
        segments = asm.feed(text)
        assert segments[0] == "い" * 15 + "、"
        rest = "".join(segments[1:]) + "".join(asm.flush())
        assert rest == "う" * 15

    def test_ascii_sentences_split(self) -> None:
        asm = SentenceAssembler()
        segments = asm.feed("The sky is blue. Why do you think?")
        assert segments == ["The sky is blue.", "Why do you think?"]

    def test_reassembled_segments_cover_full_text(self) -> None:
        text = (
            "ふしぎだよね。そらがあおいは、ひかりが ちって まぶしいから。\n"
            "ゆうやだと あかいのは なんで だと おもう？"
        )
        asm = SentenceAssembler()
        segments: list[str] = []
        for i in range(0, len(text), 3):
            segments.extend(asm.feed(text[i : i + 3]))
        segments.extend(asm.flush())
        stripped = "".join(text.split())
        assert "".join("".join(seg.split()) for seg in segments) == stripped
