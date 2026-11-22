"""起動エントリーポイント（api.main）のテスト。

build_components は DB ファイルを作成するため、必ず tmp へ
CURIOQUEST_DB_PATH を向けて組み立てる。音声周りは遅延接続のまま
起動できること（起動だけで外部サービスへアクセスしないこと）も
ここで確認する。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from curioquest.api.main import create_standalone_app
from curioquest.config import ENV_DB_PATH

EXPECTED_ROUTES = {"/", "/healthz", "/api/children", "/api/sessions/{session_id}/voice"}


def test_standalone_app_is_assembled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_DB_PATH, str(tmp_path / "db.sqlite3"))
    app = create_standalone_app()
    routes = {route.path for route in app.routes}
    assert routes >= EXPECTED_ROUTES

    # 検証ページ同梱の確認: 実ファイルから GET / が読める（静的配置の証し）
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/healthz").json() == {"status": "ok"}


def test_standalone_app_seeds_new_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "nested" / "db.sqlite3"
    monkeypatch.setenv(ENV_DB_PATH, str(db))
    with TestClient(create_standalone_app()) as client:
        assert db.is_file()  # 親ディレクトリごと作られる
        chars = client.get("/api/characters").json()
        assert len(chars) == 3  # 初回起動でシード済み
