"""シードデータのテスト。"""

from __future__ import annotations

from curioquest.db.database import Database
from curioquest.db.seed import DEFAULT_MISSIONS, seed_default_data
from curioquest.db.store import DataStore
from curioquest.domain.models import MissionCategory


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


def test_seed_missions_offer_three_per_category() -> None:
    """定番問題DBは 3 カテゴリ × 3 問で、日割り当ての繰り返しを防ぐ (6-1 / 6-3)。"""
    store = _store()
    seed_default_data(store)

    missions = store.list_missions()
    assert len(missions) == 9
    for category in MissionCategory:
        same_category = [m for m in missions if m.category is category]
        assert len(same_category) == 3
        assert {m.character_id for m in same_category} == {same_category[0].character_id}

    for mission in missions:
        # ミッションは核心の問いを1つだけ含む (6-4)。二重質問の予防線。
        assert mission.initial_question.count("？") == 1
        assert mission.initial_question.count("?") == 0
        # 定番は生成ミッション（gen_ 接頭辞）と区別できる id にしておく
        assert mission.id.startswith(("why_", "mystery_", "if_"))
        assert 1 <= mission.difficulty <= 3
        assert mission.target_skills

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
    assert len(store.list_missions()) == len(DEFAULT_MISSIONS)
    assert len(store.list_stamps()) == 6
    assert len(store.list_families()) == 1


def test_default_prompts_include_dialogue_rules() -> None:
    """対話の基本ルール（4-2）が全キャラクターのプロンプトに入っている。"""
    store = _store()
    seed_default_data(store)
    for character in store.list_characters():
        assert "すぐに答えを教えない" in character.system_prompt
        assert "秘密を" in character.system_prompt  # 秘密の約束を禁止 (05-safety)
