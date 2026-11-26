"""親エリアの音声キャッシュ削除 API のテスト (Issue #6)。

キャッシュ消去が WAV のみを削除し、会話履歴などの他データを触らないことを
検証する。設計根拠は docs/design/08-voice-cache.md 13-3 / 13-5。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from curioquest.api import create_app
from curioquest.api.deps import AppComponents
from curioquest.audio import CachingTts
from curioquest.audio.base import CharacterVoice, TextToSpeech
from curioquest.db.database import Database
from curioquest.db.seed import seed_default_data
from curioquest.db.store import DataStore
from curioquest.engine import DialogueEngine
from curioquest.llm.fake import FakeLLM


class RecordingTts(TextToSpeech):
    """最小実装の偽 TTS。"""

    def synthesize(self, text: str, voice: CharacterVoice) -> bytes:
        return f"dummy:{text}".encode()


@pytest.fixture()
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "tts_cache"


@pytest.fixture()
def components(cache_dir: Path) -> AppComponents:
    db = Database.in_memory()
    db.init_schema()
    store = DataStore(db)
    family_id = seed_default_data(store)
    engine = DialogueEngine(store=store, llm=FakeLLM(default_response="なるほど。"))
    tts = CachingTts(RecordingTts(), cache_dir, engine_tag="t", default_speaker_id=3)
    return AppComponents(store=store, engine=engine, family_id=family_id, tts=tts)


@pytest.fixture()
def client(components: AppComponents) -> TestClient:
    return TestClient(create_app(components), raise_server_exceptions=False)


def _fill_cache(tts: CachingTts, texts: list[str]) -> None:
    for text in texts:
        tts.synthesize(text, CharacterVoice())


def test_delete_removes_wavs_only(client: TestClient, cache_dir: Path) -> None:
    """削除後はメモリ・ディスクともに空。会話履歴などの他データはそのまま。"""
    tts = client.app.state.cq.tts
    assert isinstance(tts, CachingTts)
    _fill_cache(tts, ["すごいね！", "もういちど いって"])
    assert list(cache_dir.glob("*.wav"))

    child = client.post("/api/children", json={"nickname": "テスト"}).json()
    day = (date.today() - timedelta(days=1)).isoformat()
    conversation = client.post("/api/sessions", json={"child_id": child["id"], "day": day})
    assert conversation.status_code == 201
    session_id = conversation.json()["session_id"]
    assert client.post(f"/api/sessions/{session_id}/complete").status_code == 200

    res = client.delete("/api/parent/voice-cache")
    assert res.status_code == 200
    assert res.json() == {"deleted": 2}
    assert list(cache_dir.glob("*.wav")) == []

    conversations = client.get(f"/api/children/{child['id']}/conversations").json()
    assert len(conversations) == 1


def test_delete_without_caching_tts_reports_zero(client: TestClient) -> None:
    """キャッシュ無効構成 (ラップ外 TTS) でも 0 件で正常に応答する。"""
    plain = replace(client.app.state.cq, tts=RecordingTts())
    fresh = TestClient(create_app(plain), raise_server_exceptions=False)

    res = fresh.delete("/api/parent/voice-cache")
    assert res.status_code == 200
    assert res.json() == {"deleted": 0}


def test_delete_requires_pin(client: TestClient) -> None:
    """PIN が設定されていると、認証なしの削除は 401 になる。"""
    assert client.post("/api/parent/pin", json={"pin": "1234"}).status_code == 200

    anonymous = TestClient(client.app, raise_server_exceptions=False)
    assert anonymous.delete("/api/parent/voice-cache").status_code == 401
    assert client.delete("/api/parent/voice-cache").status_code == 200
