"""起動時ウォームアップ (Issue #8、08-voice-cache 13-1) のテスト。"""

from __future__ import annotations

from dataclasses import replace

from curioquest.api.deps import AppComponents, start_voice_warmup, warm_voice_cache
from curioquest.audio import AudioServiceUnavailable, CachingTts
from curioquest.audio.fake import FakeTts
from curioquest.db.database import Database
from curioquest.db.seed import seed_default_data
from curioquest.db.store import DataStore
from curioquest.engine import CANNED_REPLIES, DialogueEngine
from curioquest.llm.fake import FakeLLM


class BoomTts:
    """常に合成へ失敗する TTS。"""

    def synthesize(self, text: str, voice: object) -> bytes:
        raise AudioServiceUnavailable("VOICEVOX が起動していません")


def make_components() -> AppComponents:
    db = Database.in_memory()
    db.init_schema()
    store = DataStore(db)
    family_id = seed_default_data(store)
    engine = DialogueEngine(store=store, llm=FakeLLM(default_response="なるほど"))
    return AppComponents(store=store, engine=engine, family_id=family_id)


def test_warm_synthesizes_all_canned_replies(tmp_path):
    inner = FakeTts()
    comp = replace(
        make_components(),
        tts=CachingTts(inner, tmp_path / "cache", default_speaker_id=3),
    )

    synthesized = warm_voice_cache(comp)

    # 最低でも定型応答の数だけ実合成される (キャラの声設定が違えばそれだけ増える)
    assert synthesized >= len(CANNED_REPLIES)
    assert len(inner.calls) == synthesized
    synthesized_texts = {call["text"] for call in inner.calls}
    assert set(CANNED_REPLIES) <= synthesized_texts

    # 2 周目はキャッシュHitで実合成ゼロ
    assert warm_voice_cache(comp) == 0
    assert len(inner.calls) == synthesized


def test_warm_survives_tts_failure(tmp_path):
    comp = replace(
        make_components(),
        tts=CachingTts(BoomTts(), tmp_path / "cache", default_speaker_id=3),
    )

    # 合成できなくても例外を外に出さず 0 件で終わる (起動を止めない)
    assert warm_voice_cache(comp) == 0


def test_warm_skipped_without_cache(tmp_path):
    comp = replace(make_components(), tts=FakeTts())

    assert warm_voice_cache(comp) == 0
    assert start_voice_warmup(comp) is None


def test_start_runs_in_background(tmp_path):
    inner = FakeTts()
    comp = replace(
        make_components(),
        tts=CachingTts(inner, tmp_path / "cache", default_speaker_id=3),
    )

    thread = start_voice_warmup(comp)

    assert thread is not None
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(inner.calls) >= len(CANNED_REPLIES)
