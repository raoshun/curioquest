"""REST API 層のテスト（03-architecture 12）。

インメモリ DB + FakeLLM の AppComponents を組み、HTTP のステータスコードと
レスポンスボディを中心に検証する。音声ターン API は test_api_voice.py に分ける。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from curioquest.api import app as app_module
from curioquest.api import create_app
from curioquest.api.deps import AppComponents, build_components
from curioquest.config import ENV_MISSION_GENERATION, Settings, settings_from_env
from curioquest.db.database import Database
from curioquest.db.seed import DEFAULT_MISSIONS, seed_default_data
from curioquest.db.store import DataStore
from curioquest.domain.models import MemoryType, ThinkingSignal
from curioquest.engine import DailyMissionPlanner, DialogueEngine
from curioquest.llm.base import LLMError
from curioquest.llm.fake import FakeLLM

# 実行中に日付をまたぐことを避けるため、テスト専用日は「昨日」を使う。
DAY = (date.today() - timedelta(days=1)).isoformat()
OTHER_DAY = (date.today() - timedelta(days=2)).isoformat()


class BoomLLM:
    """常に LLMError を送出する偽プロバイダ（502 応答の検証用）。"""

    def generate_response(self, messages, schema=None, **kwargs):
        raise LLMError("接続先の Ollama に到達できません")

    def evaluate_thinking(self, conversation):
        raise LLMError("接続先の Ollama に到達できません")

    def generate_mission(self, profile, history=()):
        raise LLMError("接続先の Ollama に到達できません")


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


@pytest.fixture()
def client(voice_components: AppComponents) -> TestClient:
    return TestClient(create_app(voice_components), raise_server_exceptions=False)


def make_child(client: TestClient, nickname: str = "たろう") -> dict:
    resp = client.post("/api/children", json={"nickname": nickname})
    assert resp.status_code == 201
    return resp.json()


def start_session(client: TestClient, child_id: str, **extra) -> dict:
    resp = client.post("/api/sessions", json={"child_id": child_id, "day": DAY, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_children_crud_round_trip(client: TestClient) -> None:
    assert client.get("/api/children").json() == []
    child = make_child(client)
    assert child["nickname"] == "たろう"
    assert child["daily_limit_minutes"] == 20

    got = client.get(f"/api/children/{child['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == child["id"]

    patched = client.patch(
        f"/api/children/{child['id']}",
        json={"nickname": "second", "daily_limit_minutes": 30},
    )
    assert patched.status_code == 200
    assert patched.json()["nickname"] == "second"
    assert patched.json()["daily_limit_minutes"] == 30

    assert client.delete(f"/api/children/{child['id']}/history").status_code == 204
    assert client.get("/api/children").json()[0]["id"] == child["id"]


def test_post_child_validation(client: TestClient) -> None:
    assert client.post("/api/children", json={"nickname": ""}).status_code == 422
    bad = {"nickname": "x", "daily_limit_minutes": 0}
    assert client.post("/api/children", json=bad).status_code == 422
    bad = {"nickname": "x", "daily_limit_minutes": 241}
    assert client.post("/api/children", json=bad).status_code == 422


def test_patch_child_range_is_422(client: TestClient) -> None:
    """境界値超過は pydantic が防御するため、store の ValueError よりは 422 が優先される。"""
    child = make_child(client)
    resp = client.patch(f"/api/children/{child['id']}", json={"daily_limit_minutes": 9999})
    assert resp.status_code == 422


def test_get_unknown_child_is_404(client: TestClient) -> None:
    resp = client.get("/api/children/nope")
    assert resp.status_code == 404
    assert "nope" in resp.json()["detail"]


def test_characters_hide_system_prompt(client: TestClient) -> None:
    body = client.get("/api/characters").json()
    assert [c["id"] for c in body] == ["artist", "detective", "professor"]
    assert [c["name"] for c in body] == ["アーティスト", "たんてい", "はかせ"]
    assert all("system_prompt" not in c for c in body)


def test_today_mission_is_deterministic_and_persisted(
    client: TestClient, voice_components: AppComponents
) -> None:
    child = make_child(client)
    first = client.get("/api/missions/today", params={"child_id": child["id"], "day": DAY})
    assert first.status_code == 200
    mission_id = first.json()["mission"]["id"]

    second = client.get("/api/missions/today", params={"child_id": child["id"], "day": DAY}).json()
    assert second["mission"]["id"] == mission_id
    today = voice_components.store.get_today_mission(child["id"], DAY)
    assert today is not None and today.id == mission_id

    other = client.get("/api/missions/today", params={"child_id": child["id"], "day": OTHER_DAY})
    assert other.status_code == 200
    assert client.get("/api/missions/today").status_code == 422


def test_today_mission_unknown_child_is_404(client: TestClient) -> None:
    assert client.get("/api/missions/today", params={"child_id": "ghost"}).status_code == 404


def test_session_full_flow_message_complete_and_reuse(
    client: TestClient, voice_components: AppComponents
) -> None:
    child = make_child(client)
    session = start_session(client, child["id"])
    # 日替わりターンは今日のミッションとキャラクターを自動採用する
    today = client.get("/api/missions/today", params={"child_id": child["id"], "day": DAY}).json()
    assert session["mission_id"] == today["mission"]["id"]
    assert session["character_id"] == today["mission"]["character_id"]
    assert session["state"] == "QUESTION"
    assert session["finished"] is False
    assert session["reply"]

    turn = client.post(
        f"/api/sessions/{session['session_id']}/message",
        json={"text": "ふしぎな発見があった気がする", "seconds_spent": 30, "day": DAY},
    )
    assert turn.status_code == 200
    body = turn.json()
    assert body["session_id"] == session["session_id"]
    assert body["finished"] is False
    assert voice_components.store.get_used_seconds(child["id"], DAY) == 30

    done = client.post(f"/api/sessions/{session['session_id']}/complete")
    assert done.status_code == 200
    assert done.json()["finished"] is True
    assert done.json()["end_reason"] == "child_exit"

    # 終了済みセッションの再利用は 409（05-safety-quality 9 節）
    assert client.post(f"/api/sessions/{session['session_id']}/complete").status_code == 409
    again = client.post(
        f"/api/sessions/{session['session_id']}/message", json={"text": "もういちど", "day": DAY}
    )
    assert again.status_code == 409


def test_session_defaults_day_to_today(client: TestClient) -> None:
    child = make_child(client)
    resp = client.post("/api/sessions", json={"child_id": child["id"]})
    assert resp.status_code == 201


def test_session_daily_limit_is_409(client: TestClient, voice_components: AppComponents) -> None:
    child_id = voice_components.store.create_child(
        voice_components.family_id, "まるこ", daily_limit_minutes=1
    ).id
    voice_components.store.add_used_seconds(child_id, DAY, 61)
    resp = client.post("/api/sessions", json={"child_id": child_id, "day": DAY})
    assert resp.status_code == 409
    body = resp.json()
    assert body["child_id"] == child_id
    assert body["daily_limit_minutes"] == 1


def test_session_unknown_mission_is_404(client: TestClient) -> None:
    child = make_child(client)
    resp = client.post("/api/sessions", json={"child_id": child["id"], "mission_id": "no_such"})
    assert resp.status_code == 404


def test_llm_failure_is_502(client: TestClient, voice_components: AppComponents) -> None:
    client.app.state.cq = replace(
        voice_components, engine=DialogueEngine(store=voice_components.store, llm=BoomLLM())
    )
    child = make_child(client)
    resp = client.post("/api/sessions", json={"child_id": child["id"], "day": DAY})
    assert resp.status_code == 502
    assert "Ollama" in resp.json()["detail"]


def test_stamps_available_and_earned(client: TestClient, voice_components: AppComponents) -> None:
    child = make_child(client)
    initial = client.get(f"/api/children/{child['id']}/stamps").json()
    assert initial["available"] == 6
    assert initial["earned"] == 0
    assert initial["stamps"] == []

    voice_components.store.award_stamp(child["id"], "stamp_first_step")
    earned = client.get(f"/api/children/{child['id']}/stamps").json()
    assert earned["earned"] == 1
    assert earned["stamps"][0]["id"] == "stamp_first_step"
    assert "criteria" not in earned["stamps"][0]


def test_stamps_unknown_child_is_404(client: TestClient) -> None:
    assert client.get("/api/children/ghost/stamps").status_code == 404


def test_memories_filter(client: TestClient, voice_components: AppComponents) -> None:
    child = make_child(client)
    assert client.get(f"/api/children/{child['id']}/memories").json() == []
    voice_components.store.add_memory(child["id"], type=MemoryType.INTEREST, content="ぞう")
    voice_components.store.add_memory(child["id"], type=MemoryType.KNOWLEDGE, content="かみなり")

    all_items = client.get(f"/api/children/{child['id']}/memories").json()
    assert {m["type"] for m in all_items} == {"interest", "knowledge"}

    filtered = client.get(f"/api/children/{child['id']}/memories", params={"type": "knowledge"})
    assert [m["content"] for m in filtered.json()] == ["かみなり"]

    assert (
        client.get(f"/api/children/{child['id']}/memories", params={"type": "BOGUS"}).status_code
        == 422
    )


def test_memories_unknown_child_is_404(client: TestClient) -> None:
    assert client.get("/api/children/ghost/memories").status_code == 404


def test_dashboard_aggregates_for_parent(
    client: TestClient, voice_components: AppComponents
) -> None:
    child = make_child(client)
    session = start_session(client, child["id"])
    client.post(
        f"/api/sessions/{session['session_id']}/message",
        json={"text": "音を発見したよ、仕組みが気になった", "seconds_spent": 42, "day": DAY},
    )
    client.post(f"/api/sessions/{session['session_id']}/complete")
    voice_components.store.award_stamp(child["id"], "stamp_first_step")

    body = client.get("/api/parent/dashboard", params={"day": DAY}).json()
    row = next(r for r in body["children"] if r["child_id"] == child["id"])
    assert body["day"] == DAY
    assert row["nickname"] == "たろう"
    assert row["daily_limit_minutes"] == 20
    assert row["used_seconds_today"] == 42
    assert row["stamps_earned"] == 1

    # 「やってみた」完了はセッション終了日の日付で計上される（finished_at は UTC）。
    # 実行時刻が UTC とローカル時刻で日付をまたぐ場合は今日の完了数がずれるので、
    # 「完了は当日か、UTC 日付差で翌日扱い（=当日 0）」のどちらかであることだけ確認する。
    today = client.get("/api/parent/dashboard").json()
    today_row = next(r for r in today["children"] if r["child_id"] == child["id"])
    assert today_row["missions_finished_today"] in {0, 1}

    mission = voice_components.store.get_today_mission(child["id"], DAY)
    assert row["today_mission_title"] == mission.title

    # 別日は完了を繰り越さない（usage_daily も mission_results も日付台帳）
    other = client.get("/api/parent/dashboard", params={"day": OTHER_DAY}).json()
    other_row = next(r for r in other["children"] if r["child_id"] == child["id"])
    assert other_row["missions_finished_today"] == 0
    assert other_row["used_seconds_today"] == 0
    # mission_results は finished_at、missions_today は server local day で台帳するため、
    # OTHER_DAY 行の今日のミッション表示は実行時刻依差別になりうる（None か本日のタイトル）。
    assert other_row["today_mission_title"] in (None, mission.title)


def test_dashboard_lists_children_newest_first(client: TestClient) -> None:
    """ダッシュボードは家庭の子を新着登録順に並べて返す。"""
    make_child(client, nickname="ひとりめ")
    make_child(client, nickname="ふたりめ")
    # 実行中の日付差分でミッション未割当の行が出ないよう、明確な past day で引く。
    body = client.get("/api/parent/dashboard", params={"day": DAY}).json()
    assert [r["nickname"] for r in body["children"]] == ["ふたりめ", "ひとりめ"]


def test_delete_history_removes_messages(
    client: TestClient, voice_components: AppComponents
) -> None:
    child = make_child(client)
    session = start_session(client, child["id"])
    client.post(
        f"/api/sessions/{session['session_id']}/message",
        json={"text": "テスト", "seconds_spent": 10, "day": DAY},
    )
    assert client.delete(f"/api/children/{child['id']}/history").status_code == 204
    messages = voice_components.store.db.query(
        "SELECT id FROM messages WHERE child_id = ?", (child["id"],)
    )
    assert messages == []
    assert client.delete("/api/children/ghost/history").status_code == 404


def test_store_value_error_is_400(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(self, child_id: str, patch: dict) -> None:
        raise ValueError("テスト用の更新失敗")

    monkeypatch.setattr(DataStore, "update_child", explode)
    child = make_child(client)
    resp = client.patch(f"/api/children/{child['id']}", json={"nickname": "しんいち"})
    assert resp.status_code == 400
    assert "テスト用の更新失敗" in resp.json()["detail"]


# ------------------------------------------------------------ 起動・組み立て


def test_pages_are_served_same_origin(client: TestClient) -> None:
    """GET / (こどもホーム)・/parent (おとなエリア)・/curio (最小検証) を同源で返す。

    音声ターンは同源必須のため、ページと API が同じオリジンにあることをここで担保する。
    """
    home = client.get("/")
    assert home.status_code == 200
    assert home.headers["content-type"].startswith("text/html")
    assert "CurioQuest こどもホーム" in home.text

    parent = client.get("/parent")
    assert parent.status_code == 200
    assert "おとなエリア" in parent.text

    curio = client.get("/curio")
    assert curio.status_code == 200
    assert "マイクにお話し" in curio.text
    assert "/api/" in curio.text


def test_page_not_found_if_static_missing(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """static ディレクトリが無い場合は導出物欠落として 404 で気づかせる。"""
    monkeypatch.setattr(app_module, "_STATIC_DIR", tmp_path / "missing")
    resp = client.get("/")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "ページが見つからなかったよ"


def test_build_components_creates_standalone_family(tmp_path: Path) -> None:
    """既定の組み立てはシード済みの家族環境を返す（起動経路のスモーク）。"""
    components = build_components(Settings(db_path=str(tmp_path / "db.sqlite3")))
    try:
        assert components.store.get_family(components.family_id).name
        assert len(components.store.list_characters()) == 3
        assert len(components.store.list_missions()) == len(DEFAULT_MISSIONS)
        assert components.stt is not None
        assert components.tts is not None
    finally:
        components.store.db.close()


def test_build_components_wires_daily_planner(tmp_path: Path) -> None:
    """既定の組み立ては LLM 付きの DailyMissionPlanner を接続する。"""
    components = build_components(Settings(db_path=str(tmp_path / "db.sqlite3")))
    try:
        planner = components.planner
        assert isinstance(planner, DailyMissionPlanner)
        assert planner.llm is components.engine.llm
        assert planner.generation_enabled is True
        assert planner.store is components.store
    finally:
        components.store.db.close()


def test_mission_generation_flag_can_be_disabled_with_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CURIOQUEST_MISSION_GENERATION での生成の ON/OFF を設定に反映する。"""
    monkeypatch.delenv(ENV_MISSION_GENERATION, raising=False)
    assert settings_from_env().mission_generation_enabled is True

    for raw, expected in (("0", False), ("false", False), ("NO", False), ("off", False)):
        monkeypatch.setenv(ENV_MISSION_GENERATION, raw)
        assert settings_from_env().mission_generation_enabled is expected

    for raw in ("1", "TRUE", "yes", "on"):
        monkeypatch.setenv(ENV_MISSION_GENERATION, raw)
        assert settings_from_env().mission_generation_enabled is True


def finish_session(client: TestClient, child_id: str, text: str) -> dict:
    """今日のミッションで1ターン会話して「共有できた」で終える。"""
    session = start_session(client, child_id)
    resp = client.post(
        f"/api/sessions/{session['session_id']}/message",
        json={"text": text, "seconds_spent": 30, "day": DAY},
    )
    assert resp.status_code == 200, resp.text
    done = client.post(f"/api/sessions/{session['session_id']}/complete")
    assert done.status_code == 200, done.text
    return session


def test_child_progress_shows_remaining_time(client: TestClient) -> None:
    child = make_child(client)
    fresh = client.get(f"/api/children/{child['id']}/progress", params={"day": DAY})
    assert fresh.status_code == 200, fresh.text
    body = fresh.json()
    assert body["day"] == DAY
    assert body["today_mission"] is None
    assert body["daily_limit_minutes"] == 20
    assert body["used_seconds_today"] == 0
    assert body["remaining_seconds"] == 20 * 60
    assert body["missions_finished_today"] == 0
    assert body["missions_finished_total"] == 0

    finish_session(client, child["id"], "音を発見したよ、仕組みが気になった")
    body = client.get(f"/api/children/{child['id']}/progress", params={"day": DAY}).json()
    assert body["today_mission"] is not None
    assert body["today_mission"]["title"]
    assert body["used_seconds_today"] == 30
    assert body["remaining_seconds"] == 20 * 60 - 30
    assert body["missions_finished_today"] in {0, 1}  # UTC とローカルの日付境界
    assert body["missions_finished_total"] == 1


def test_child_progress_unknown_child_is_404(client: TestClient) -> None:
    assert client.get("/api/children/nobody/progress").status_code == 404


def test_unknown_child_conversations_is_404(client: TestClient) -> None:
    assert client.get("/api/children/nobody/conversations").status_code == 404


def test_child_conversations_lists_finished_sessions_newest_first(client: TestClient) -> None:
    child = make_child(client)
    assert client.get(f"/api/children/{child['id']}/conversations").json() == []

    first = finish_session(client, child["id"], "最初は火山の話")
    second = finish_session(client, child["id"], "次は海の生き物の話")
    rows = client.get(f"/api/children/{child['id']}/conversations").json()
    assert [row["id"] for row in rows] == [second["session_id"], first["session_id"]]
    for row in rows:
        assert row["mission_title"]
        assert row["ended_at"] is not None
        assert row["end_reason"] in {"shared_idea", "child_exit"}
        assert row["turn_count"] >= 1

    # 途中の会話は一覧に出さない（「つづき」側が担当する）
    start_session(client, child["id"])
    assert len(client.get(f"/api/children/{child['id']}/conversations").json()) == 2

    limited = client.get(f"/api/children/{child['id']}/conversations", params={"limit": 1}).json()
    assert [row["id"] for row in limited] == [second["session_id"]]
    assert (
        client.get(f"/api/children/{child['id']}/conversations", params={"limit": 0}).status_code
        == 422
    )


def test_dashboard_shows_what_parent_wants_to_know(
    client: TestClient, voice_components: AppComponents
) -> None:
    child = make_child(client)
    session = finish_session(client, child["id"], "安土城をつくりたかった")
    store = voice_components.store
    store.add_memory(child["id"], MemoryType.INTEREST, "また行きたい", topic="お城", source="t")
    store.add_memory(child["id"], MemoryType.INTEREST, "天守閣がすごい", topic="お城", source="t")
    store.add_memory(child["id"], MemoryType.PREFERENCE, "雪", source="t")
    store.add_thinking_record(
        child["id"],
        session["session_id"],
        turn_index=1,
        signals=[ThinkingSignal.HYPOTHESIS],
        summary="城が溶ける理由",
    )

    row = client.get("/api/parent/dashboard", params={"day": DAY}).json()["children"][0]
    assert {"topic": "お城", "count": 2} in row["interests"]
    assert {"topic": "雪", "count": 1} in row["interests"]
    thinking = row["recent_thinking"][0]
    assert thinking["summary"] == "城が溶ける理由"
    assert thinking["signals"] == ["hypothesis"]
    mission = store.get_today_mission(child["id"], DAY)
    assert row["recent_missions"][0]["mission_title"] == mission.title
    assert row["recent_missions"][0]["outcome"] in {"completed", "child_exit"}
    assert row["recent_conversations"][0]["id"] == session["session_id"]
