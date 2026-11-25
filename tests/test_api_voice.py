"""音声ターン API のテスト（03-architecture 12 の音声受信 API）。

FakeStt / FakeTts で 認識→ターン→合成 の連携と、各音声サービス障害時の
デグレード方針（認識失敗はターンを実行しない、合成失敗はテキストを返す）を
検証する。
"""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from curioquest.api import create_app
from curioquest.api import voice as voice_module
from curioquest.api.deps import AppComponents
from curioquest.audio import AudioServiceUnavailable
from curioquest.audio.fake import FakeStt, FakeTts
from curioquest.db.database import Database
from curioquest.db.seed import seed_default_data
from curioquest.db.store import DataStore
from curioquest.engine import DialogueEngine
from curioquest.llm.fake import FakeLLM

DAY = (date.today() - timedelta(days=1)).isoformat()


class BoomStt:
    """常に認識へ失敗する STT。"""

    def transcribe(self, audio: bytes) -> str:
        raise AudioServiceUnavailable("音声認識エンジンに接続できません")


class BoomTts:
    """常に合成へ失敗する TTS。"""

    def synthesize(self, text: str, voice: object) -> bytes:
        raise AudioServiceUnavailable("VOICEVOX が起動していません")


@pytest.fixture()
def voice_components() -> AppComponents:
    db = Database.in_memory()
    db.init_schema()
    store = DataStore(db)
    family_id = seed_default_data(store)
    engine = DialogueEngine(
        store=store, llm=FakeLLM(default_response="なるほど。どうしてだと思う？")
    )
    return AppComponents(store=store, engine=engine, family_id=family_id)


def make_client(voice_components: AppComponents, **audio) -> TestClient:
    comp = replace(voice_components, **audio)
    return TestClient(create_app(comp), raise_server_exceptions=False)


def child_message_count(store: DataStore, child_id: str) -> int:
    row = store.db.query_one(
        "SELECT COUNT(*) AS c FROM messages WHERE child_id = ? AND role = 'child'",
        (child_id,),
    )
    assert row is not None
    return int(row["c"])


def test_voice_round_trip_returns_synthesized_wav(voice_components: AppComponents) -> None:
    stt, tts = FakeStt(texts=["ぞうがすき"]), FakeTts()
    client = make_client(voice_components, stt=stt, tts=tts)
    child = client.post("/api/children", json={"nickname": "はな"}).json()
    sess = client.post("/api/sessions", json={"child_id": child["id"], "day": DAY}).json()

    resp = client.post(
        f"/api/sessions/{sess['session_id']}/voice?seconds_spent=15&day={DAY}",
        content=b"RIFFfake-wav",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "なるほど。どうしてだと思う？"
    assert body["voice_available"] is True
    assert body["audio_error"] is None
    assert base64.b64decode(body["audio_wav_b64"]) == b"RIFF0000WAVEfake"
    # 認識へ渡した音声バイト長と、合成テキストが応答と一致する
    assert stt.audio_sizes == [len(b"RIFFfake-wav")]
    assert tts.calls[-1]["text"] == body["reply"]
    # 利用時間はテキストターンと同じ台帳に積もる
    assert voice_components.store.get_used_seconds(child["id"], DAY) == 15


def test_voice_recognition_failure_does_not_run_turn(voice_components: AppComponents) -> None:
    client = make_client(voice_components, stt=BoomStt(), tts=FakeTts())
    child = client.post("/api/children", json={"nickname": "はな"}).json()
    sess = client.post("/api/sessions", json={"child_id": child["id"], "day": DAY}).json()

    resp = client.post(
        f"/api/sessions/{sess['session_id']}/voice?seconds_spent=60&day={DAY}",
        content=b"x",
    )
    assert resp.status_code == 503
    assert "音声認識" in resp.json()["detail"]
    # 認識できなかった音声でターンも利用時間も消費しない
    assert child_message_count(voice_components.store, child["id"]) == 0
    assert voice_components.store.get_used_seconds(child["id"], DAY) == 0


def test_voice_synthesis_failure_falls_back_to_text(voice_components: AppComponents) -> None:
    client = make_client(voice_components, stt=FakeStt(texts=["くじらについて"]), tts=BoomTts())
    child = client.post("/api/children", json={"nickname": "はな"}).json()
    sess = client.post("/api/sessions", json={"child_id": child["id"], "day": DAY}).json()

    resp = client.post(
        f"/api/sessions/{sess['session_id']}/voice?seconds_spent=10&day={DAY}",
        content=b"x",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "なるほど。どうしてだと思う？"
    assert body["voice_available"] is False
    assert body["audio_wav_b64"] is None
    assert "VOICEVOX" in (body["audio_error"] or "")
    # 合成に失敗しても対話ターンは成立している
    assert child_message_count(voice_components.store, child["id"]) == 1


def test_voice_without_audio_services(voice_components: AppComponents) -> None:
    # 認識が未登録なら入力不能で 503、合成だけ未登録ならテキストを返す
    no_stt = make_client(voice_components, tts=FakeTts())
    child = no_stt.post("/api/children", json={"nickname": "はな"}).json()
    sess = no_stt.post("/api/sessions", json={"child_id": child["id"], "day": DAY}).json()
    url = f"/api/sessions/{sess['session_id']}/voice"

    resp = no_stt.post(url, content=b"x")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "この設定では音声を入力できません"

    only_tts_missing = make_client(voice_components, stt=FakeStt(texts=["Test"]), tts=None)
    child = only_tts_missing.post("/api/children", json={"nickname": "はな"}).json()
    sess = only_tts_missing.post("/api/sessions", json={"child_id": child["id"], "day": DAY}).json()
    resp = only_tts_missing.post(
        f"/api/sessions/{sess['session_id']}/voice?day={DAY}", content=b"x"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["voice_available"] is False
    assert body["audio_wav_b64"] is None
    assert "音声出力" in (body["audio_error"] or "")


def test_voice_session_errors_and_validation(voice_components: AppComponents) -> None:
    stt = FakeStt(default_text="Test")
    client = make_client(voice_components, stt=stt, tts=FakeTts())
    child = client.post("/api/children", json={"nickname": "はな"}).json()
    sess = client.post("/api/sessions", json={"child_id": child["id"], "day": DAY}).json()
    url = f"/api/sessions/{sess['session_id']}/voice"

    assert client.post("/api/sessions/ghost/voice", content=b"x").status_code == 404
    assert client.post(url, params={"seconds_spent": -1}, content=b"x").status_code == 422
    assert client.post(url, params={"seconds_spent": 99999}, content=b"x").status_code == 422

    done = client.post(f"/api/sessions/{sess['session_id']}/complete")
    assert done.status_code == 200
    before = list(stt.audio_sizes)  # 別オブジェクトとして確保（append で共有されない）
    resp = client.post(url, content=b"x")
    assert resp.status_code == 409
    # 閉じたセッションは認識だけを消費し、ターンへは進まない
    assert stt.audio_sizes == before + [1]


def test_voice_empty_transcript_asks_again(voice_components: AppComponents) -> None:
    """認識結果が空ならエンジンの聞き直し経路へ乗り、テキストで応答する。"""
    client = make_client(voice_components, stt=FakeStt(default_text=""), tts=FakeTts())
    child = client.post("/api/children", json={"nickname": "はな"}).json()
    sess = client.post("/api/sessions", json={"child_id": child["id"], "day": DAY}).json()
    resp = client.post(f"/api/sessions/{sess['session_id']}/voice?day={DAY}", content=b"x")
    assert resp.status_code == 200
    assert resp.json()["reply"]


def _start_session(client) -> tuple[str, str]:
    child = client.post("/api/children", json={"nickname": "はな"}).json()
    sess = client.post("/api/sessions", json={"child_id": child["id"], "day": DAY}).json()
    return child["id"], sess["session_id"]


def test_voice_body_over_declared_limit_is_rejected(
    voice_components: AppComponents, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Content-Length が上限を超えるアップロードは 413 で拒否し、ターンを進めない。"""
    monkeypatch.setattr(voice_module, "MAX_VOICE_BODY_BYTES", 8)
    client = make_client(voice_components, stt=FakeStt(texts=["ぞうがすき"]), tts=FakeTts())
    child_id, session_id = _start_session(client)

    resp = client.post(
        f"/api/sessions/{session_id}/voice?seconds_spent=10&day={DAY}",
        content=b"x" * 9,
    )
    assert resp.status_code == 413
    assert "もういっかい" in resp.json()["detail"]
    assert child_message_count(voice_components.store, child_id) == 0
    assert voice_components.store.get_used_seconds(child_id, DAY) == 0


def test_voice_body_streamed_over_limit_is_rejected(
    voice_components: AppComponents, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Content-Length なし（chunked）でも実測バイト数で上限を超えたら 413 にする。"""
    monkeypatch.setattr(voice_module, "MAX_VOICE_BODY_BYTES", 8)
    client = make_client(voice_components, stt=FakeStt(texts=["ぞうがすき"]), tts=FakeTts())
    child_id, session_id = _start_session(client)

    def chunks():
        yield b"x" * 5
        yield b"x" * 5

    resp = client.post(
        f"/api/sessions/{session_id}/voice?seconds_spent=10&day={DAY}",
        content=chunks(),
    )
    assert resp.status_code == 413
    assert child_message_count(voice_components.store, child_id) == 0


def test_voice_body_exactly_at_limit_is_accepted(
    voice_components: AppComponents, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上限ちょうど（超えない）は従来どおり受理する。"""
    monkeypatch.setattr(voice_module, "MAX_VOICE_BODY_BYTES", 8)
    client = make_client(voice_components, stt=FakeStt(texts=["ぞうがすき"]), tts=FakeTts())
    child_id, session_id = _start_session(client)

    resp = client.post(
        f"/api/sessions/{session_id}/voice?seconds_spent=10&day={DAY}",
        content=b"x" * 8,
    )
    assert resp.status_code == 200
    assert voice_components.store.get_used_seconds(child_id, DAY) == 10


def test_voice_turn_logs_stage_latency(
    voice_components: AppComponents, caplog: pytest.LogCaptureFixture
) -> None:
    # 段階A計測: 認識→ターン→合成 の各区間 ms と音声合成可否を 1 行で残す
    client = make_client(voice_components, stt=FakeStt(texts=["ぞうがすき"]), tts=FakeTts())
    child = client.post("/api/children", json={"nickname": "はな"}).json()
    sess = client.post("/api/sessions", json={"child_id": child["id"], "day": DAY}).json()

    with caplog.at_level("INFO", logger="curioquest.api.voice"):
        resp = client.post(f"/api/sessions/{sess['session_id']}/voice?day={DAY}", content=b"x")
    assert resp.status_code == 200
    lines = [r.getMessage() for r in caplog.records if "voice_turn" in r.getMessage()]
    assert len(lines) == 1
    message = lines[0]
    for field in ("stt_ms=", "engine_ms=", "tts_ms=", "voice_available=True"):
        assert field in message


def test_voice_recognition_failure_logs_stage(
    voice_components: AppComponents, caplog: pytest.LogCaptureFixture
) -> None:
    client = make_client(voice_components, stt=BoomStt(), tts=FakeTts())
    child = client.post("/api/children", json={"nickname": "はな"}).json()
    sess = client.post("/api/sessions", json={"child_id": child["id"], "day": DAY}).json()

    with caplog.at_level("INFO", logger="curioquest.api.voice"):
        resp = client.post(f"/api/sessions/{sess['session_id']}/voice?day={DAY}", content=b"x")
    assert resp.status_code == 503
    lines = [r.getMessage() for r in caplog.records if "voice_turn" in r.getMessage()]
    assert len(lines) == 1
    assert "failed=stt" in lines[0]
