"""DataStore の永続化テスト（インメモリ SQLite）。"""

from __future__ import annotations

import pytest

from curioquest.db.database import Database
from curioquest.db.seed import seed_default_data
from curioquest.db.store import DataStore, NotFound
from curioquest.domain.models import (
    Character,
    DialogueState,
    MemoryType,
    MessageRole,
    Mission,
    MissionCategory,
    MissionEndReason,
    MissionOutcome,
    ThinkingSignal,
)


@pytest.fixture()
def store() -> DataStore:
    db = Database.in_memory()
    db.init_schema()
    return DataStore(db)


@pytest.fixture()
def family_id(store: DataStore) -> str:
    return store.create_family("テスト宅", family_id="family_t").id


def make_character(store: DataStore) -> None:
    store.upsert_character(
        Character(
            id="professor",
            name="はかせ",
            description="テスト用キャラクター",
            personality=[],
            thinking_style=[],
            system_prompt="テスト用プロンプト",
        )
    )


def make_mission(store: DataStore) -> Mission:
    make_character(store)
    return store.upsert_mission(
        Mission(
            id="why_001",
            category=MissionCategory.CURIOSITY,
            title="そらは どうして あおい？",
            difficulty=1,
            target_skills=["hypothesis"],
            character_id="professor",
            estimated_minutes=3,
            initial_question="そらは なんで あおいと おもう？",
            completion_conditions=["child_has_shared_an_idea"],
        )
    )


class TestFamilyChild:
    def test_roundtrip(self, store: DataStore, family_id: str) -> None:
        store.create_child(
            family_id,
            "あおい",
            child_id="child_a",
            birthday="2017-04-01",
            favorite_topics=["宇宙", "生き物"],
            daily_limit_minutes=15,
        )
        loaded = store.get_child("child_a")
        assert loaded.nickname == "あおい"
        assert loaded.favorite_topics == ["宇宙", "生き物"]
        assert loaded.daily_limit_minutes == 15
        assert loaded in store.list_children(family_id)

    def test_update_and_delete(self, store: DataStore, family_id: str) -> None:
        store.create_child(family_id, "はると", child_id="child_b")
        updated = store.update_child("child_b", {"nickname": "はる", "daily_limit_minutes": 10})
        assert updated.nickname == "はる"
        assert updated.daily_limit_minutes == 10
        with pytest.raises(ValueError):
            store.update_child("child_b", {"family_id": "other"})
        store.delete_child("child_b")
        with pytest.raises(NotFound):
            store.get_child("child_b")

    def test_unknown_child_raises(self, store: DataStore) -> None:
        with pytest.raises(NotFound):
            store.get_child("child_missing")


class TestMessages:
    def test_child_history_is_isolated(self, store: DataStore, family_id: str) -> None:
        """会話履歴は子ども単位で分離される (AC-02)。"""
        store.create_child(family_id, "あおい", child_id="child_a")
        store.create_child(family_id, "はると", child_id="child_b")
        mission = make_mission(store)
        s1 = store.create_session("child_a", mission.id, "professor", DialogueState.INTRO)
        s2 = store.create_session("child_b", mission.id, "professor", DialogueState.INTRO)
        store.append_message(s1.id, "child_a", MessageRole.CHILD, "そらは 空気(くうき)でできてる")
        store.append_message(s2.id, "child_b", MessageRole.CHILD, "みずは ひえると こおる")

        hist_a = store.list_messages_for_child("child_a")
        hist_b = store.list_messages_for_child("child_b")
        assert [m.content for m in hist_a] == ["そらは 空気(くうき)でできてる"]
        assert [m.content for m in hist_b] == ["みずは ひえると こおる"]

    def test_session_ordering(self, store: DataStore, family_id: str) -> None:
        child = store.create_child(family_id, "あおい", child_id="child_a")
        mission = make_mission(store)
        session = store.create_session(child.id, mission.id, "professor", DialogueState.QUESTION)
        store.append_message(
            session.id,
            child.id,
            MessageRole.ASSISTANT,
            "どうしてだと思う？",
            state=DialogueState.QUESTION,
        )
        store.append_message(
            session.id,
            child.id,
            MessageRole.CHILD,
            "光(ひかり)が 飛(と)んでるから",
            state=DialogueState.THINKING,
        )
        msgs = store.list_messages(session.id)
        assert [m.role for m in msgs] == [MessageRole.ASSISTANT, MessageRole.CHILD]
        assert msgs[1].state is DialogueState.THINKING
        assert msgs[0].created_at is not None

    def test_delete_child_removes_all_child_data(self, store: DataStore, family_id: str) -> None:
        """AC-12: 子ども単位でデータを削除できる（CASCADE）。"""
        child = store.create_child(family_id, "あおい", child_id="child_a")
        mission = make_mission(store)
        session = store.create_session(child.id, mission.id, "professor", DialogueState.INTRO)
        store.append_message(session.id, child.id, MessageRole.CHILD, "テスト")
        store.add_memory(child.id, MemoryType.DISCOVERY, "発見", source=session.id)
        store.add_thinking_record(
            child.id, session.id, 0, [ThinkingSignal.HYPOTHESIS], "仮説(かせつ)"
        )
        seed_default_data(store, family_id=family_id)
        store.award_stamp(child.id, "stamp_first_step", session.id)
        store.add_used_seconds(child.id, "2026-11-21", 60)

        store.delete_child(child.id)
        assert store.list_messages_for_child(child.id) == []
        assert store.list_memories(child.id) == []
        assert store.list_thinking_records(child.id) == []
        assert store.list_child_stamps(child.id) == []
        assert store.get_used_seconds(child.id, "2026-11-21") == 0

    def test_delete_history_only(self, store: DataStore, family_id: str) -> None:
        """保護者が会話履歴だけを削除できる（スタンプ・Memory は残す）。"""
        child = store.create_child(family_id, "あおい", child_id="child_a")
        mission = make_mission(store)
        session = store.create_session(child.id, mission.id, "professor", DialogueState.INTRO)
        store.append_message(session.id, child.id, MessageRole.CHILD, "会話(かいは)の履歴")
        store.add_memory(child.id, MemoryType.DISCOVERY, "発見を残す")

        store.delete_history_for_child(child.id)
        assert store.list_messages_for_child(child.id) == []
        assert len(store.list_memories(child.id)) == 1


class TestSessions:
    def test_state_update_and_end(self, store: DataStore, family_id: str) -> None:
        child = store.create_child(family_id, "あおい", child_id="child_a")
        mission = make_mission(store)
        session = store.create_session(child.id, mission.id, "professor", DialogueState.INTRO)
        assert session.is_active
        assert store.list_active_sessions(child.id)  # 1 active

        store.update_session_state(session.id, DialogueState.QUESTION)
        mid = store.get_session(session.id)
        assert mid.state is DialogueState.QUESTION
        assert mid.ended_at is None and mid.is_active

        ended = store.update_session_state(
            session.id, DialogueState.COMPLETE, end_reason=MissionEndReason.SHARED_IDEA
        )
        assert ended.state is DialogueState.COMPLETE
        assert ended.end_reason is MissionEndReason.SHARED_IDEA
        assert ended.ended_at is not None
        assert not ended.is_active
        assert store.list_active_sessions(child.id) == []

    def test_mission_result_roundtrip(self, store: DataStore, family_id: str) -> None:
        child = store.create_child(family_id, "あおい", child_id="child_a")
        mission = make_mission(store)
        session = store.create_session(child.id, mission.id, "professor", DialogueState.INTRO)
        store.save_mission_result(
            session.id,
            child.id,
            mission.id,
            MissionOutcome.COMPLETED,
            thinking_summary="光(ひかり)のことで仮説(かせつ)を出した",
            signals={"hypothesis": 1},
        )
        results = store.list_mission_results(child.id)
        assert len(results) == 1
        assert results[0].outcome is MissionOutcome.COMPLETED
        assert results[0].signals == {"hypothesis": 1}


class TestStamps:
    def test_award_once(self, store: DataStore, family_id: str) -> None:
        from curioquest.db.seed import DEFAULT_STAMPS

        seed_default_data(store, family_id=family_id)
        child = store.create_child(family_id, "あおい", child_id="child_a")

        assert store.award_stamp(child.id, "stamp_first_step") is True
        # 同一条件で何度も繰り返せない (7-1)
        assert store.award_stamp(child.id, "stamp_first_step") is False
        assert store.has_stamp(child.id, "stamp_first_step")
        assert [s.id for s in store.list_child_stamps(child.id)] == ["stamp_first_step"]
        assert len(DEFAULT_STAMPS) == 6


class TestMemoriesAndSettings:
    def test_memory_types_filter(self, store: DataStore, family_id: str) -> None:
        child = store.create_child(family_id, "あおい", child_id="child_a")
        store.add_memory(child.id, MemoryType.INTEREST, "宇宙(うちゅう)が好き", topic="宇宙")
        store.add_memory(
            child.id, MemoryType.DISCOVERY, "光(ひかり)の反射(はんしゃ)を発見", topic="宇宙"
        )
        assert len(store.list_memories(child.id)) == 2
        only_discovery = store.list_memories(child.id, type=MemoryType.DISCOVERY)
        assert len(only_discovery) == 1
        assert only_discovery[0].type is MemoryType.DISCOVERY

    def test_settings_roundtrip(self, store: DataStore, family_id: str) -> None:
        assert store.get_setting(family_id, "x", default={"a": 1}) == {"a": 1}
        store.set_setting(family_id, "x", {"yy": [1, 2]})
        assert store.get_setting(family_id, "x") == {"yy": [1, 2]}

    def test_usage_daily_accumulates(self, store: DataStore, family_id: str) -> None:
        child = store.create_child(family_id, "あおい", child_id="child_a")
        assert store.get_used_seconds(child.id, "2026-11-21") == 0
        assert store.add_used_seconds(child.id, "2026-11-21", 30) == 30
        assert store.add_used_seconds(child.id, "2026-11-21", 45) == 75
        assert store.get_used_seconds(child.id, "2026-11-22") == 0  # 別日は別レコード


class TestTodayMission:
    def test_set_get(self, store: DataStore, family_id: str) -> None:
        child = store.create_child(family_id, "あおい", child_id="child_a")
        mission = make_mission(store)
        assert store.get_today_mission(child.id, "2026-11-21") is None
        store.set_today_mission(family_id, child.id, "2026-11-21", mission.id)
        got = store.get_today_mission(child.id, "2026-11-21")
        assert got is not None and got.id == mission.id
        assert store.get_today_mission(child.id, "2026-11-22") is None


def test_list_session_summaries_shows_finished_conversations(
    store: DataStore, family_id: str
) -> None:
    """会話一覧は終了したものだけ・新しい順。Turns は子どもの発話数 (02-features.md 9-2)。"""
    child = store.create_child(family_id, nickname="あさえ")
    other_child = store.create_child(family_id, nickname="まる")
    first = make_mission(store)
    second = store.upsert_mission(
        Mission(
            id="mystery_001",
            category=MissionCategory.REASONING,
            title="かぎは どこに かくれた？",
            difficulty=1,
            target_skills=["observation"],
            character_id="professor",
            estimated_minutes=3,
            initial_question="どこだと おもう？",
            completion_conditions=["shared_idea"],
        )
    )
    older = store.create_session(child.id, first.id, "professor", DialogueState.COMPLETE)
    store.update_session_state(older.id, DialogueState.COMPLETE, MissionEndReason.SHARED_IDEA)
    store.append_message(older.id, child.id, MessageRole.CHILD, "はじめまして")
    store.append_message(older.id, child.id, MessageRole.CHILD, "しった!")
    store.append_message(older.id, child.id, MessageRole.ASSISTANT, "よかった!")
    newer = store.create_session(child.id, second.id, "professor", DialogueState.COMPLETE)
    store.update_session_state(newer.id, DialogueState.COMPLETE, MissionEndReason.CHILD_EXIT)
    store.append_message(newer.id, child.id, MessageRole.CHILD, "みつかった!")
    active = store.create_session(other_child.id, first.id, "professor", DialogueState.QUESTION)

    rows = store.list_session_summaries(child.id)

    assert [row.mission_id for row in rows] == [second.id, first.id]
    assert [row.turn_count for row in rows] == [1, 2]
    row = rows[0]
    assert (row.id, row.turn_count, row.end_reason) == (newer.id, 1, MissionEndReason.CHILD_EXIT)
    assert row.mission_title == "かぎは どこに かくれた？"
    assert row.started_at and row.ended_at
    assert row.child_id == child.id
    assert active.id not in [r.id for r in rows]


def test_list_session_summaries_honours_limit(store: DataStore, family_id: str) -> None:
    child = store.create_child(family_id, nickname="あさえ")
    mission = make_mission(store)
    for _ in range(3):
        session = store.create_session(child.id, mission.id, "professor", DialogueState.COMPLETE)
        store.update_session_state(session.id, DialogueState.COMPLETE, MissionEndReason.SHARED_IDEA)

    rows = store.list_session_summaries(child.id, limit=2)

    assert len(rows) == 2
    assert all(row.turn_count == 0 for row in rows)
