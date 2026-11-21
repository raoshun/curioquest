"""シードデータのテスト。"""

from __future__ import annotations

from curioquest.db.database import Database
from curioquest.db.seed import seed_default_data
from curioquest.db.store import DataStore


def _store() -> DataStore:
    db = Database.in_memory()
    db.init_schema()
    return DataStore(db)


def test_seed_creates_expected_defaults() -> None:
    store = _store()
    family_id = seed_default_data(store)
    assert family_id == "family_home"

    characters = store.list_characters()
    assert {c.id for c in characters} == {"professor", "detective", "artist"}
    assert all(c.system_prompt for c in characters)

    missions = store.list_missions()
    assert {m.category for m in missions} == {"curiosity", "reasoning", "imagination"}
    for mission in missions:
        assert mission.completion_conditions == ["child_has_shared_an_idea"]

    stamps = store.list_stamps()
    assert len(stamps) == 6
    assert {s.name for s in stamps} == {
        "はじめの一歩",
        "なぜなぜ博士",
        "ひらめき名人",
        "名探偵",
        "チャレンジャー",
        "おはなし名人",
    }


def test_seed_is_idempotent() -> None:
    store = _store()
    seed_default_data(store)
    seed_default_data(store)
    assert len(store.list_characters()) == 3
    assert len(store.list_missions()) == 3
    assert len(store.list_stamps()) == 6
    assert len(store.list_families()) == 1


def test_default_prompts_include_dialogue_rules() -> None:
    """対話の基本ルール（4-2）が全キャラクターのプロンプトに入っている。"""
    store = _store()
    seed_default_data(store)
    for character in store.list_characters():
        assert "すぐに答えを教えない" in character.system_prompt
        assert "秘密を" in character.system_prompt  # 秘密の約束を禁止 (05-safety)
