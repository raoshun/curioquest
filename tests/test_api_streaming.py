"""SSE ストリーミング ターン API のテスト (Issue #10)。

テキスト差分と音声セグメントが並行で流れ、最後に turn が届くことを検証する。
STT 失敗は SSE ヘッダー送信前 (HTTP ステータス) で返し、TTS 失敗は音声だけ
欠落させて-text ターンは成立させる、というデグレード方針もここで確認する。
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from curioquest.api import create_app
from curioquest.api.deps import AppComponents
from curioquest.audio import AudioServiceUnavailable
from curioquest.audio.fake import FakeStt, FakeTts
from curioquest.db.database import Database
from curioquest.db.seed import seed_default_data
from curioquest.db.store import DataStore
from curioquest.engine import DialogueEngine
from curioquest.llm.base import LLMError
from curioquest.llm.fake import FakeLLM

DAY = (date.today() - timedelta(days=1)).isoformat()
REPLY = "ぞうは 大きいね。どうして だと おもう？"


class BoomStt:
    def transcribe(self, audio: bytes) -> str:
        raise AudioServiceUnavailable("音声認識エンジンに接続できません")


class BoomTts:
    def synthesize(self, text: str, voice: object) -> bytes:
        raise AudioServiceUnavailable("VOICEVOX が起動していません")


class StreamFailingLLM(FakeLLM):
    """最初のチャン後に LLMError を送出するストリーム。"""

    def generate_response_stream(
        self, messages, *, temperature: float | None = None
    ) -> Iterator[str]:
        yield "ぞうは"
        raise LLMError("上流の LLM が応答を途絶させた", status_code=502)


@pytest.fixture()
def stream_components() -> AppComponents:
    db = Database.in_memory()
    db.init_schema()
    store = DataStore(db)
    family_id = seed_default_data(store)
    engine = DialogueEngine(store=store, llm=FakeLLM(default_response=REPLY))
    return AppComponents(store=store, engine=engine, family_id=family_id)


def make_client(comp: AppComponents, **audio) -> TestClient:
    return TestClient(create_app(replace(comp, **audio)), raise_server_exceptions=False)


def open_session(client: TestClient) -> str:
    child = client.post("/api/children", json={"nickname": "はな"}).json()
    sess = client.post("/api/sessions", json={"child_id": child["id"], "day": DAY}).json()
    return sess["session_id"]


def events(body: str) -> list[dict]:
    out = []
    for chunk in body.split("\n\n"):
        for line in chunk.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: ") :]))
    return out


def test_message_stream_deltas_then_turn(stream_components: AppComponents) -> None:
    client = make_client(stream_components)
    session_id = open_session(client)

    resp = client.post(
        f"/api/sessions/{session_id}/message/stream",
        json={"text": "ぞうについて おしえて", "day": DAY},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    evs = events(resp.text)
    deltas = [e["text"] for e in evs if e["type"] == "delta"]
    turn = next(e for e in evs if e["type"] == "turn")
    assert "".join(deltas) == turn["turn"]["reply"] == REPLY
    assert len(deltas) > 1  # 実際に分割されて流れている
    assert evs[-1]["type"] == "turn"
    assert not any(e["type"] == "audio" for e in evs)


def test_voice_stream_emits_sequential_audio(stream_components: AppComponents) -> None:
    stt, tts = FakeStt(texts=["ぞうがすき"]), FakeTts()
    client = make_client(stream_components, stt=stt, tts=tts)
    session_id = open_session(client)

    resp = client.post(
        f"/api/sessions/{session_id}/voice/stream?day={DAY}",
        content=b"RIFFfake-wav",
    )
    assert resp.status_code == 200
    evs = events(resp.text)
    audios = [e for e in evs if e["type"] == "audio"]
    assert audios, "音声セグメントが 1 つ以上流れるべき"
    assert [e["index"] for e in audios] == list(range(len(audios)))
    for ev in audios:
        assert base64.b64decode(ev["data_b64"]).startswith(b"RIFF")
    turn = next(e for e in evs if e["type"] == "turn")
    assert turn["transcript"] == "ぞうがすき"
    assert turn["turn"]["reply"] == REPLY
    assert evs[-1]["type"] == "turn"


def test_voice_stream_tts_failure_keeps_text_turn(stream_components: AppComponents) -> None:
    client = make_client(stream_components, stt=FakeStt(texts=["test"]), tts=BoomTts())
    session_id = open_session(client)

    resp = client.post(f"/api/sessions/{session_id}/voice/stream?day={DAY}", content=b"x")
    assert resp.status_code == 200
    evs = events(resp.text)
    errors = [e for e in evs if e["type"] == "audio_error"]
    assert errors
    assert all(e["index"] >= 0 for e in errors)
    turn = next(e for e in evs if e["type"] == "turn")
    assert turn["turn"]["reply"] == REPLY
    assert not any(e["type"] == "audio" for e in evs)


def test_voice_stream_stt_failure_is_http_503(stream_components: AppComponents) -> None:
    client = make_client(stream_components, stt=BoomStt(), tts=FakeTts())
    session_id = open_session(client)

    resp = client.post(f"/api/sessions/{session_id}/voice/stream", content=b"x")
    assert resp.status_code == 503
    assert "data:" not in resp.text


def test_voice_stream_without_tts_is_text_only(stream_components: AppComponents) -> None:
    client = make_client(stream_components, stt=FakeStt(texts=["hello"]))
    session_id = open_session(client)

    resp = client.post(f"/api/sessions/{session_id}/voice/stream?day={DAY}", content=b"x")
    assert resp.status_code == 200
    evs = events(resp.text)
    assert not any(e["type"] in ("audio", "audio_error") for e in evs)
    turn = next(e for e in evs if e["type"] == "turn")
    assert turn["transcript"] == "hello"


def test_stream_llm_failure_emits_error_event(stream_components: AppComponents) -> None:
    failing = StreamFailingLLM()
    engine = DialogueEngine(store=stream_components.store, llm=failing)
    comp = replace(stream_components, engine=engine)
    client = make_client(comp)
    session_id = open_session(client)

    resp = client.post(
        f"/api/sessions/{session_id}/message/stream",
        json={"text": "hello", "day": DAY},
    )
    assert resp.status_code == 200
    evs = events(resp.text)
    err = next(e for e in evs if e["type"] == "error")
    assert err["status"] == 502
    assert not any(e["type"] == "turn" for e in evs)


def test_stream_unknown_session_is_http_404(stream_components: AppComponents) -> None:
    client = make_client(stream_components)
    resp = client.post(
        "/api/sessions/does-not-exist/message/stream",
        json={"text": "hello", "day": DAY},
    )
    assert resp.status_code == 404


def test_stream_events_never_leak_internal_markers(stream_components: AppComponents) -> None:
    stt, tts = FakeStt(texts=["hello"]), FakeTts()
    client = make_client(stream_components, stt=stt, tts=tts)
    session_id = open_session(client)

    resp = client.post(f"/api/sessions/{session_id}/voice/stream?day={DAY}", content=b"x")
    allowed = {"delta", "audio", "audio_error", "turn", "error"}
    for ev in events(resp.text):
        assert ev["type"] in allowed
