"""CachingTts のキャッシュ挙動テスト (08-voice-cache 13-3、Issue #5)。"""

from __future__ import annotations

import pytest

from curioquest.audio.base import AudioServiceUnavailable, CharacterVoice
from curioquest.audio.cache import CachingTts
from curioquest.audio.fake import FakeTts

VOICE = CharacterVoice(speaker_id=1, speed_scale=1.0, pitch_scale=0.0)


class FlakyTts(FakeTts):
    """初回呼び出しだけ失敗する TTS。失敗が伝搬しキャッシュされないことの確認用。"""

    fail_first: bool = True

    def synthesize(self, text, voice):  # type: ignore[override]
        if self.fail_first:
            self.fail_first = False
            raise AudioServiceUnavailable("VOICEVOX 未起動")
        return super().synthesize(text, voice)


def test_second_synthesize_is_cache_hit(tmp_path):
    inner = FakeTts()
    tts = CachingTts(inner, tmp_path / "tts_cache")

    first = tts.synthesize("すごいね！もういっかい きいていい？", VOICE)
    second = tts.synthesize("すごいね！もういっかい きいていい？", VOICE)

    assert first == second
    assert len(inner.calls) == 1
    assert tts.hits == 1 and tts.misses == 1


def test_voice_params_change_the_key(tmp_path):
    inner = FakeTts()
    tts = CachingTts(inner, tmp_path / "tts_cache")

    tts.synthesize("おしやべりじゅんびしよう", VOICE)
    tts.synthesize("おしやべりじゅんびしよう", CharacterVoice(speaker_id=2))
    tts.synthesize("おしやべりじゅんびしよう", CharacterVoice(speaker_id=1, speed_scale=1.2))
    tts.synthesize("おしやべりじゅんびしよう", CharacterVoice(speaker_id=1, pitch_scale=0.5))

    assert len(inner.calls) == 4
    assert tts.hits == 0


def test_disk_cache_survives_new_instance(tmp_path):
    cache_dir = tmp_path / "tts_cache"
    first = CachingTts(FakeTts(), cache_dir)
    wav = first.synthesize("たんけんおわり！また あそぼうね", VOICE)

    inner2 = FakeTts()
    second = CachingTts(inner2, cache_dir)
    assert second.synthesize("たんけんおわり！また あそぼうね", VOICE) == wav
    assert inner2.calls == []
    assert second.hits == 1


def test_engine_tag_change_invalidates(tmp_path):
    cache_dir = tmp_path / "tts_cache"
    CachingTts(FakeTts(), cache_dir, engine_tag="voicevox-v1").synthesize(
        "せいかい！ ずかん とうろく 完了", VOICE
    )

    inner2 = FakeTts()
    second = CachingTts(inner2, cache_dir, engine_tag="voicevox-v2")
    second.synthesize("せいかい！ ずかん とうろく 完了", VOICE)
    assert len(inner2.calls) == 1


def test_failure_propagates_and_is_not_cached(tmp_path):
    inner = FlakyTts()
    tts = CachingTts(inner, tmp_path / "tts_cache")

    with pytest.raises(AudioServiceUnavailable):
        tts.synthesize("きけん！", VOICE)
    # 失敗後に同じインスタンスでも再生成される（失敗はキャッシュされない）
    assert tts.synthesize("きけん！", VOICE) == b"RIFF0000WAVEfake"
    assert len(list((tmp_path / "tts_cache").glob("*.wav"))) == 1


def test_unresolved_speaker_bypasses_cache(tmp_path):
    inner = FakeTts()
    tts = CachingTts(inner, tmp_path / "tts_cache")

    tts.synthesize("はじまりのあいさつ", CharacterVoice())
    tts.synthesize("はじまりのあいさつ", CharacterVoice())

    assert len(inner.calls) == 2
    assert tts.hits == 0
    assert not (tmp_path / "tts_cache").exists()


def test_default_speaker_is_resolved_into_key(tmp_path):
    inner = FakeTts()
    tts = CachingTts(inner, tmp_path / "tts_cache", default_speaker_id=7)

    tts.synthesize("こんにちは", CharacterVoice())
    tts.synthesize("こんにちは", CharacterVoice(speaker_id=7))

    # 未設定は既定話者と同じキーになり、実合成は 1 回だけ
    assert len(inner.calls) == 1
    assert inner.calls[0]["voice"].speaker_id == 7


def test_clear_removes_memory_and_disk(tmp_path):
    cache_dir = tmp_path / "tts_cache"
    tts = CachingTts(FakeTts(), cache_dir)
    tts.synthesize("A", VOICE)
    tts.synthesize("B", VOICE)
    assert len(list(cache_dir.glob("*.wav"))) == 2

    assert tts.clear() == 2
    assert list(cache_dir.glob("*.wav")) == []

    inner2 = FakeTts()
    reopened = CachingTts(inner2, cache_dir)
    reopened.synthesize("A", VOICE)
    assert len(inner2.calls) == 1


def test_warm_counts_and_survives_failures(tmp_path):
    inner = FlakyTts()
    tts = CachingTts(inner, tmp_path / "tts_cache")

    synthesized = tts.warm(["また あそぼう", "せいこう！"], [VOICE])

    # 初回の 1 件は失敗するが続きは合成され、例外は外に出ない
    assert synthesized == 1
    assert len(inner.calls) == 1
