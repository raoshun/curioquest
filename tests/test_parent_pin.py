"""保護者エリアの PIN 保護のテスト（02-features 3-3）。

テストは専用に作っている。インメモリ DB + FakeLLM の AppComponents を組み、
HTTP のステータスコード・Cookie 通過後の挙動・失敗時のロック・PIN 変更時の
よその端末の失効を中心に検証する。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from curioquest.api import create_app
from curioquest.api.deps import AppComponents
from curioquest.db.database import Database
from curioquest.db.seed import seed_default_data
from curioquest.db.store import DataStore
from curioquest.engine import DialogueEngine
from curioquest.llm.fake import FakeLLM

PIN = "1234"
NEW_PIN = "9999"


@pytest.fixture()
def components() -> AppComponents:
    db = Database.in_memory()
    db.init_schema()
    store = DataStore(db)
    family_id = seed_default_data(store)
    engine = DialogueEngine(store=store, llm=FakeLLM(default_response="なるほど"))
    return AppComponents(store=store, engine=engine, family_id=family_id)


@pytest.fixture()
def client(components: AppComponents) -> TestClient:
    return TestClient(create_app(components), raise_server_exceptions=False)


def make_child(client: TestClient, nickname: str = "たろう") -> dict:
    resp = client.post("/api/children", json={"nickname": nickname})
    assert resp.status_code == 201, resp.text
    return resp.json()


def setup_pin(client: TestClient, pin: str = PIN) -> None:
    resp = client.post("/api/parent/pin", json={"pin": pin})
    assert resp.status_code == 200, resp.text


def verify(client: TestClient, pin: str = PIN) -> None:
    resp = client.post("/api/parent/pin/verify", json={"pin": pin})
    assert resp.status_code == 200, resp.text


# ------------------------------------------------------------ 未設定時は開いている


def test_status_reports_unconfigured_and_area_is_open(client: TestClient) -> None:
    resp = client.get("/api/parent/pin/status")
    assert resp.status_code == 200
    assert resp.json() == {"is_set": False, "authenticated": False}
    # まだ誰も PIN を置いていないご家庭を締め出さない方針
    assert client.get("/api/parent/dashboard").status_code == 200


def test_verify_without_pin_setup_is_409(client: TestClient) -> None:
    resp = client.post("/api/parent/pin/verify", json={"pin": PIN})
    assert resp.status_code == 409


# ------------------------------------------------------------ 設定・認証の往復


def test_setup_grants_cookie_and_dashboard_reads_it(client: TestClient) -> None:
    resp = client.post("/api/parent/pin", json={"pin": PIN})
    assert resp.status_code == 200
    assert resp.json() == {"is_set": True, "authenticated": True}
    assert "cq_parent_session" in client.cookies

    assert client.get("/api/parent/pin/status").json() == {"is_set": True, "authenticated": True}
    assert client.get("/api/parent/dashboard").status_code == 200


def test_logout_revokes_access(client: TestClient) -> None:
    setup_pin(client)
    resp = client.post("/api/parent/pin/logout")
    assert resp.status_code == 200
    assert resp.json() == {"is_set": True, "authenticated": False}
    assert client.get("/api/parent/dashboard").status_code == 401


def test_second_client_stays_locked_out(client: TestClient, components: AppComponents) -> None:
    setup_pin(client)
    other = TestClient(create_app(components), raise_server_exceptions=False)
    assert other.get("/api/parent/pin/status").json() == {"is_set": True, "authenticated": False}
    assert other.get("/api/parent/dashboard").status_code == 401

    verify(other)
    assert other.get("/api/parent/dashboard").status_code == 200


# ------------------------------------------------------------ 保護されるルート


def test_children_writing_routes_gated(client: TestClient) -> None:
    setup_pin(client)
    # 設定直後はこの端末は認証済み。ログアウトすると書き込みは締まる
    client.post("/api/parent/pin/logout")
    assert client.post("/api/children", json={"nickname": "はなこ"}).status_code == 401

    verify(client)
    resp = client.post("/api/children", json={"nickname": "はなこ"})
    assert resp.status_code == 201, resp.text
    child_id = resp.json()["id"]
    assert client.patch(f"/api/children/{child_id}", json={"nickname": "うめ"}).status_code == 200
    assert client.delete(f"/api/children/{child_id}/history").status_code == 204


def test_dashboard_and_conversations_gated(client: TestClient) -> None:
    setup_pin(client)
    verify(client)
    child = make_child(client)
    assert client.get("/api/parent/dashboard").status_code == 200
    assert client.get(f"/api/children/{child['id']}/conversations").status_code == 200

    client.post("/api/parent/pin/logout")
    assert client.get("/api/parent/dashboard").status_code == 401
    assert client.get(f"/api/children/{child['id']}/conversations").status_code == 401


def test_child_facing_routes_never_need_pin(client: TestClient) -> None:
    setup_pin(client)
    verify(client)
    child = make_child(client)
    client.post("/api/parent/pin/logout")
    for path in (
        "/api/children",
        f"/api/children/{child['id']}",
        f"/api/children/{child['id']}/progress",
        f"/api/children/{child['id']}/stamps",
        f"/api/children/{child['id']}/memories",
        "/api/missions/today?child_id=" + child["id"],
    ):
        assert client.get(path).status_code == 200, path


# ------------------------------------------------------------ 失敗とロック


def test_wrong_pin_is_401_and_fifth_attempt_locks(client: TestClient) -> None:
    setup_pin(client)
    for _ in range(5):
        resp = client.post("/api/parent/pin/verify", json={"pin": "0000"})
        assert resp.status_code == 401, resp.text
    # 5 回目でロックに入り、正しい PIN でもしばらく 429 であずかる
    resp = client.post("/api/parent/pin/verify", json={"pin": PIN})
    assert resp.status_code == 429


# ------------------------------------------------------------ 形・設定済み


@pytest.mark.parametrize("bad", ["123", "123456789", "12ab", "abcd", ""])
def test_setup_rejects_bad_format(client: TestClient, bad: str) -> None:
    resp = client.post("/api/parent/pin", json={"pin": bad})
    assert resp.status_code == 400, resp.text
    assert client.get("/api/parent/pin/status").json()["is_set"] is False


def test_setup_once_only(client: TestClient) -> None:
    setup_pin(client)
    resp = client.post("/api/parent/pin", json={"pin": "5678"})
    assert resp.status_code == 403


# ------------------------------------------------------------ PIN 変更


def test_change_requires_authentication(client: TestClient) -> None:
    setup_pin(client)
    client.post("/api/parent/pin/logout")
    resp = client.post("/api/parent/pin/change", json={"old_pin": PIN, "new_pin": NEW_PIN})
    assert resp.status_code == 401


def test_change_with_wrong_old_pin_is_400(client: TestClient) -> None:
    setup_pin(client)
    verify(client)
    resp = client.post("/api/parent/pin/change", json={"old_pin": "0000", "new_pin": NEW_PIN})
    assert resp.status_code == 400
    # 変更されていないので元の PIN で入れる
    other = TestClient(client.app)
    verify(other, PIN)
    assert other.get("/api/parent/dashboard").status_code == 200


def test_change_invalidates_sessions_on_other_devices(
    client: TestClient, components: AppComponents
) -> None:
    setup_pin(client)
    verify(client)
    other = TestClient(create_app(components), raise_server_exceptions=False)
    verify(other)

    resp = client.post("/api/parent/pin/change", json={"old_pin": PIN, "new_pin": NEW_PIN})
    assert resp.status_code == 200
    assert resp.json() == {"is_set": True, "authenticated": True}

    # 変更を言い出した端末は新しい PIN で認証済み。他の端末はやり直し。
    assert client.get("/api/parent/dashboard").status_code == 200
    assert other.get("/api/parent/dashboard").status_code == 401
    verify(other, NEW_PIN)
    assert other.get("/api/parent/dashboard").status_code == 200


def test_change_rejects_bad_new_format(client: TestClient) -> None:
    setup_pin(client)
    verify(client)
    resp = client.post("/api/parent/pin/change", json={"old_pin": PIN, "new_pin": "abc"})
    assert resp.status_code == 400
