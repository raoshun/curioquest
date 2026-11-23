"""SQLite 接続のラッパー。

MVP は SQLite（03-architecture.md 10-1）。音声は保存しないため、
この層を流れるのはテキストとメタデータのみである。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Database:
    """1 つの SQLite ファイル（またはメモリ内 DB）へのスレッドセーフな接続。"""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def in_memory(cls) -> Database:
        return cls(":memory:")

    def init_schema(self) -> None:
        """schema.sql を流し込む（冪等）。"""
        script = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self._lock, self._conn:
            self._conn.executescript(script)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
