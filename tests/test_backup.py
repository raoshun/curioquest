"""SQLite バックアップツール（curioquest.backup）のテスト。"""

from __future__ import annotations

import gzip
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from curioquest import backup
from curioquest.backup import (
    backup_database,
    main,
    prune_old_backups,
    restore_backup,
)
from curioquest.config import ENV_DB_PATH
from curioquest.db.database import Database
from curioquest.db.seed import seed_default_data
from curioquest.db.store import DataStore


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """子ども1人を登録した状態の実ファイルデータベース。"""
    path = tmp_path / "curioquest.db"
    db = Database(path)
    db.init_schema()
    store = DataStore(db)
    family = store.create_family("テスト宅", family_id="family_backup")
    store.create_child(family.id, "はる", child_id="child_backup")
    db.close()
    return path


def _child_names(db_file: Path) -> list[str]:
    with sqlite3.connect(db_file) as conn:
        return [row[0] for row in conn.execute("SELECT nickname FROM children ORDER BY nickname")]


def test_backup_creates_readable_gzip_snapshot(db_path: Path) -> None:
    dest = db_path.parent / "backups"
    created = backup_database(db_path, dest)
    assert created.name.startswith("curioquest-")
    assert created.name.endswith(".sqlite.gz")
    assert created.is_file()

    restored = db_path.parent / "restored.db"
    restore_backup(created, restored)
    assert _child_names(restored) == ["はる"]
    with sqlite3.connect(restored) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_backup_missing_db_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        backup_database(tmp_path / "none.db", tmp_path / "backups")


def test_backup_same_second_names_do_not_collide(db_path: Path) -> None:
    dest = db_path.parent / "backups"
    now = datetime(2026, 2, 1, 9, 0, 0, tzinfo=UTC)
    first = backup_database(db_path, dest, now=now)
    second = backup_database(db_path, dest, now=now)
    assert first != second
    assert second.name == first.name.replace(".sqlite.gz", "-1.sqlite.gz")


def test_backup_prunes_archives_over_keep(db_path: Path) -> None:
    dest = db_path.parent / "backups"
    created = [
        backup_database(db_path, dest, keep=2, now=datetime(2026, 2, day, tzinfo=UTC))
        for day in range(1, 4)
    ]
    # keep=2 なので 1 日目は削除済み、2・3 日目が残る
    assert not created[0].exists()
    assert created[1].exists()
    assert created[2].exists()


def test_prune_keeps_newest_and_unlimited_when_keep_zero(db_path: Path) -> None:
    dest = db_path.parent / "backups"
    archives = [
        backup_database(db_path, dest, keep=0, now=datetime(2026, 2, day, tzinfo=UTC))
        for day in range(1, 5)
    ]
    removed = prune_old_backups(dest, keep=3)
    assert removed == [archives[0]]
    remaining = sorted(p.name for p in dest.glob("curioquest-*.sqlite.gz"))
    assert remaining == [a.name for a in archives[1:]]
    assert prune_old_backups(dest, keep=0) == []


def test_restore_round_trip(db_path: Path) -> None:
    dest = db_path.parent / "backups"
    archive = backup_database(db_path, dest, keep=0)

    # バックアップ後にデータを消す
    db = Database(db_path)
    row = db.query_one("SELECT id FROM children WHERE nickname = ?", ("はる",))
    assert row is not None
    db.execute("DELETE FROM children WHERE id = ?", (row["id"],))
    db.close()
    assert _child_names(db_path) == []

    restore_backup(archive, db_path)
    assert _child_names(db_path) == ["はる"]


def test_restore_broken_archive_leaves_db_untouched(db_path: Path) -> None:
    broken = db_path.parent / "broken.sqlite.gz"
    broken.write_bytes(b"this is not gzip data")
    before = db_path.read_bytes()
    with pytest.raises(gzip.BadGzipFile):
        restore_backup(broken, db_path)
    assert db_path.read_bytes() == before


def test_cli_backup_and_restore_with_env_defaults(tmp_path: Path, monkeypatch, capsys) -> None:
    db = tmp_path / "data" / "curioquest.db"
    db.parent.mkdir()
    handle = Database(db)
    handle.init_schema()
    store = DataStore(handle)
    family = store.create_family("テスト宅")
    store.create_child(family.id, "そら")
    handle.close()
    monkeypatch.setenv(ENV_DB_PATH, str(db))

    assert main([]) == 0
    backups_dir = tmp_path / "data" / "backups"
    archives = list(backups_dir.glob("curioquest-*.sqlite.gz"))
    assert len(archives) == 1
    assert "バックアップを作成しました" in capsys.readouterr().out

    # データを壊して CLI で復元
    handle = Database(db)
    handle.execute("DELETE FROM children")
    handle.close()
    assert _child_names(db) == []

    assert main(["--restore", str(archives[0])]) == 0
    assert _child_names(db) == ["そら"]
    assert "復元しました" in capsys.readouterr().out


def test_cli_missing_db_exits_with_code_1(tmp_path: Path) -> None:
    code = main(["--db", str(tmp_path / "none.db"), "--dest", str(tmp_path / "backups")])
    assert code == 1


def test_cli_restore_failure_exits_with_code_1(tmp_path: Path, capsys) -> None:
    code = main(["--db", str(tmp_path / "db.sqlite"), "--restore", str(tmp_path / "none.gz")])
    assert code == 1
    assert "復元に失敗しました" in capsys.readouterr().err


def test_seeded_database_can_be_backed_up(db_path: Path, tmp_path: Path) -> None:
    # 実運用同等にシードデータを流し込んでもスナップショットが取れること
    db = Database(db_path)
    seed_default_data(DataStore(db))
    db.close()
    created = backup_database(db_path, tmp_path / "backups", keep=backup.DEFAULT_KEEP)
    restored = tmp_path / "restored.db"
    restore_backup(created, restored)
    with sqlite3.connect(restored) as conn:
        assert conn.execute("SELECT COUNT(*) FROM stamps").fetchone()[0] > 0
