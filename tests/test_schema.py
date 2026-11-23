"""スキーマ設計方針のテスト。"""

from __future__ import annotations

from curioquest.db import database

SCHEMA = (database._SCHEMA_PATH).read_text(encoding="utf-8")


def test_schema_never_stores_audio() -> None:
    """音声データは STT 後に破棄し、テキストのみ保存する（03-architecture 11）。"""
    lowered = SCHEMA.lower()
    assert "audio" not in lowered
    assert "voice_" not in lowered.replace("voice_engine", "").replace(
        "voice_speaker_id", ""
    ).replace("voice_speed_scale", "").replace("voice_pitch_scale", "")
    assert "blob" not in lowered


def test_all_child_scoped_tables_have_child_id() -> None:
    """子ども固有データには child_id を持たせる (03-architecture 11)。"""
    for table in ("sessions", "messages", "mission_results", "child_stamps", "memories"):
        block = SCHEMA.split(f"CREATE TABLE IF NOT EXISTS {table} (")[1].split(");")[0]
        assert "child_id" in block, table
