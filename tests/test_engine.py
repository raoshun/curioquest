"""対話制御エンジンの状態遷移・記録・安全振る舞いのテスト。"""

from __future__ import annotations

import threading
import time
from datetime import date

import pytest

from curioquest.db.database import Database
from curioquest.db.seed import seed_default_data
from curioquest.db.store import DataStore
from curioquest.domain.models import (
    DialogueState,
    MemoryType,
    MessageRole,
    MissionEndReason,
    MissionOutcome,
)
from curioquest.engine import DailyLimitExceeded, DialogueEngine, SessionClosed
from curioquest.engine.rules import SAFETY_REPLY
from curioquest.llm.base import ThinkingEvaluation, ThinkingSignal
from curioquest.llm.fake import FakeLLM


@pytest.fixture()
def store() -> DataStore:
    db = Database.in_memory()
    db.init_schema()
    return DataStore(db)


@pytest.fixture(autouse=True)
def seeded(store: DataStore) -> None:
    seed_default_data(store)


def make_engine(store: DataStore, llm: FakeLLM | None = None, **kwargs):
    engine = DialogueEngine(store=store, llm=llm or FakeLLM(responses=["ようこそ"]), **kwargs)
    return engine


def start(
    store: DataStore,
    llm: FakeLLM | None = None,
    *,
    child_kwargs: dict | None = None,
):
    kid = store.create_child("family_home", "testkid", **(child_kwargs or {}))
    engine = make_engine(store, llm)
    result = engine.start_session(kid.id, "why_001", "professor")
    return kid, engine, result


def test_start_session_generates_intro_and_moves_to_question(store: DataStore) -> None:
    llm = FakeLLM(responses=["ふしぎの探究を始めよう"])
    kid, engine, result = start(store, llm)
    assert result.session.state is DialogueState.QUESTION
    assert result.session.is_active
    assert result.reply == "ふしぎの探究を始めよう"
    kinds = [c["kind"] for c in llm.calls]
    assert kinds == ["generate_response"]
    messages = store.list_messages(result.session.id)
    assert messages[0].role is MessageRole.ASSISTANT
    assert messages[0].state is DialogueState.INTRO


def test_start_session_raises_when_daily_limit_reached(store: DataStore) -> None:
    kid = store.create_child("family_home", "over", daily_limit_minutes=20)
    store.add_used_seconds(kid.id, date.today(), 20 * 60)
    engine = make_engine(store)
    with pytest.raises(DailyLimitExceeded):
        engine.start_session(kid.id, "why_001", "professor")


def test_normal_turn_persists_thinking_and_exploration_state(store: DataStore) -> None:
    llm = FakeLLM(responses=["イントロ", "いいねえ"])
    kid, engine, result = start(store, llm)
    turn = engine.handle_turn(result.session.id, "そらを みて まぼろしだと おもった")
    assert turn.session.state is DialogueState.EXPLORATION
    assert turn.intent == "normal"
    records = store.list_thinking_records(kid.id)
    assert len(records) == 1
    assert records[0].turn_index == 0
    assert records[0].session_id == result.session.id
    # 子どもの発話は LLM への入力に含まれる
    generate_calls = [c for c in llm.calls if c["kind"] == "generate_response"]
    assert len(generate_calls) == 2
    assert any("まぼろし" in m.content for m in generate_calls[-1]["messages"])


def test_exploration_capped_then_explanation_then_finish(store: DataStore) -> None:
    llm = FakeLLM(responses=["i", "1", "2", "3", "4", "5"])
    kid, engine, result = start(store, llm)
    session_id = result.session.id
    for i in range(3):
        turn = engine.handle_turn(session_id, f"こえ{i}")
        assert turn.session.state is DialogueState.EXPLORATION
    turn = engine.handle_turn(session_id, "こえ3")
    assert turn.session.state is DialogueState.EXPLANATION
    turn = engine.handle_turn(session_id, "こえ4")
    assert turn.session.state is DialogueState.COMPLETE
    assert turn.session.end_reason is MissionEndReason.HINTS_DONE
    results = store.list_mission_results(kid.id)
    assert results[-1].outcome is MissionOutcome.COMPLETED


def test_help_intent_gets_hint_without_thinking_record(store: DataStore) -> None:
    llm = FakeLLM(responses=["i", "ここを見てみて"])
    kid, engine, result = start(store, llm)
    turn = engine.handle_turn(result.session.id, "わからない")
    assert turn.session.state is DialogueState.HINT
    assert turn.intent == "dont_know"
    assert store.list_thinking_records(kid.id) == []
    assert turn.reply == "ここを見てみて"


def test_hints_exhaust_then_mission_finishes_as_completed(store: DataStore) -> None:
    llm = FakeLLM(responses=["i", "h1", "h2", "h3", "おわり"])
    kid, engine, result = start(store, llm)
    session_id = result.session.id
    for _ in range(3):
        turn = engine.handle_turn(session_id, "むずかしい")
        assert turn.session.state is DialogueState.HINT
    turn = engine.handle_turn(session_id, "やっぱりわからない")
    assert turn.session.end_reason is MissionEndReason.HINTS_DONE
    assert turn.session.state is DialogueState.COMPLETE
    assert not turn.session.is_active
    results = store.list_mission_results(kid.id)
    assert results[-1].outcome is MissionOutcome.COMPLETED
    # 終了時の思考記録（hints_done）も保存される
    assert results[-1].signals["end_reason"] == "hints_done"
    assert results[-1].signals["child_turns"] == 4


def test_shared_idea_completes_mission_with_stamp_and_memory(store: DataStore) -> None:
    llm = FakeLLM(responses=["i", "いいね"])
    llm.evaluation = ThinkingEvaluation(
        signals=[ThinkingSignal.EXPLAINED_REASON],
        shared_idea=True,
        summary="ひかりの はなしを じぶんで 説明した",
    )
    kid, engine, result = start(store, llm)
    turn = engine.handle_turn(result.session.id, "そらは ひかりで できてる")
    assert turn.session.state is DialogueState.COMPLETE
    assert turn.session.end_reason is MissionEndReason.SHARED_IDEA
    assert sorted(turn.stamps_awarded) == sorted(["stamp_first_step", "stamp_explainer"])
    assert store.has_stamp(kid.id, "stamp_first_step")
    result_row = store.list_mission_results(kid.id)[-1]
    assert result_row.outcome is MissionOutcome.COMPLETED
    assert result_row.thinking_summary == "ひかりの はなしを じぶんで 説明した"
    memories = store.list_memories(kid.id)
    assert [m.type for m in memories] == [MemoryType.DISCOVERY]
    assert memories[0].source == "mission:why_001"
    assert "ひかりの" in turn.reply


def test_first_idea_stamp_awarded_only_once(store: DataStore) -> None:
    llm = FakeLLM(responses=["i", "a", "i2", "b"])
    llm.evaluation = ThinkingEvaluation(shared_idea=True, summary="ことばにできた")
    kid, engine, first = start(store, llm)
    turn1 = engine.handle_turn(first.session.id, "お空は鏡みたいな気がする")
    assert turn1.stamps_awarded == ["stamp_first_step"]
    second = engine.start_session(kid.id, "why_001", "professor")
    turn2 = engine.handle_turn(second.session.id, "雲の向こうも青そう")
    assert turn2.stamps_awarded == []
    assert turn2.session.end_reason is MissionEndReason.SHARED_IDEA


def test_child_exit_ends_session(store: DataStore) -> None:
    llm = FakeLLM(responses=["i", "またあした"])
    kid, engine, result = start(store, llm)
    turn = engine.handle_turn(result.session.id, "やめておしまい")
    assert turn.session.end_reason is MissionEndReason.CHILD_EXIT
    assert turn.session.state is DialogueState.COMPLETE
    assert store.list_mission_results(kid.id)[-1].outcome is MissionOutcome.CHILD_EXIT
    with pytest.raises(SessionClosed):
        engine.handle_turn(result.session.id, "ねえ")


def test_time_up_mid_turn_resets_and_blocks_next_start(store: DataStore) -> None:
    llm = FakeLLM(responses=["i", "なるほど"])
    kid, engine, result = start(store, llm, child_kwargs={"daily_limit_minutes": 1})
    turn = engine.handle_turn(result.session.id, "うみは かるい", seconds_spent=61)
    assert turn.session.end_reason is MissionEndReason.TIME_UP
    assert turn.reset_needed
    assert store.list_mission_results(kid.id)[-1].outcome is MissionOutcome.TIME_UP
    with pytest.raises(DailyLimitExceeded):
        engine.start_session(kid.id, "why_001", "professor")


def test_dangerous_utterance_uses_safety_reply_without_llm(store: DataStore) -> None:
    llm = FakeLLM(responses=["i"])
    kid, engine, result = start(store, llm)
    calls_before = len(llm.calls)
    turn = engine.handle_turn(result.session.id, "マッチあそび したい")
    assert turn.reply == SAFETY_REPLY
    assert turn.intent == "dangerous"
    assert len(llm.calls) == calls_before
    assert turn.session.state is DialogueState.QUESTION
    assert store.list_thinking_records(kid.id) == []


def test_empty_utterance_is_not_treated_as_intent(store: DataStore) -> None:
    llm = FakeLLM(responses=["i"])
    _kid, engine, result = start(store, llm)
    calls_before = len(llm.calls)
    messages_before = len(store.list_messages(result.session.id))
    turn = engine.handle_turn(result.session.id, "   ")
    assert turn.reply != ""
    assert turn.intent is None
    assert len(llm.calls) == calls_before
    assert len(store.list_messages(result.session.id)) == messages_before
    assert turn.session.state is DialogueState.QUESTION


def test_complete_ends_session_as_child_exit(store: DataStore) -> None:
    kid, engine, result = start(store)
    done = engine.complete(result.session.id)
    assert done.session.end_reason is MissionEndReason.CHILD_EXIT
    assert not done.session.is_active
    assert done.reply  # 別れのあいさつが返る
    assert store.list_mission_results(kid.id)[0].outcome is MissionOutcome.CHILD_EXIT
    messages = store.list_messages(result.session.id)
    assert messages[-1].role is MessageRole.ASSISTANT
    assert messages[-1].state is DialogueState.COMPLETE


def test_complete_twice_raises_session_closed(store: DataStore) -> None:
    _kid, engine, result = start(store)
    engine.complete(result.session.id)
    with pytest.raises(SessionClosed):
        engine.complete(result.session.id)


def test_maybe_idle_closes_session_after_window(store: DataStore) -> None:
    clock = [100.0]
    llm = FakeLLM(responses=["i", "またね"])
    kid = store.create_child("family_home", "idle")
    engine = DialogueEngine(store=store, llm=llm, _clock=lambda: clock[0])
    result = engine.start_session(kid.id, "why_001", "professor")
    clock[0] += 60.0
    assert engine.maybe_idle(result.session.id) is None
    clock[0] += 180.0
    turn = engine.maybe_idle(result.session.id)
    assert turn is not None
    assert turn.session.end_reason is MissionEndReason.IDLE
    assert store.list_mission_results(kid.id)[-1].outcome is MissionOutcome.IDLE
    assert engine.maybe_idle(result.session.id) is None


class _SlowLLM(FakeLLM):
    """応答生成に意図的に待ち時間を入れ、並行ターンが干渉しやすくする（テスト専用）。"""

    def generate_response(self, messages, schema=None, *, temperature=None):  # type: ignore[no-untyped-def]
        time.sleep(0.05)
        return super().generate_response(messages, schema=schema, temperature=temperature)


def test_concurrent_same_session_turns_are_serialized(store: DataStore) -> None:
    """同一セッションへの同時リクエストでも状態上限・終了処理が多重実行されないこと。"""
    llm = _SlowLLM(default_response="いいねえ。どうしてだと思う？")
    kid, engine, started = start(store, llm)
    session_id = started.session.id
    errors: list[BaseException] = []
    barrier = threading.Barrier(5)

    def send(i: int) -> None:
        try:
            barrier.wait(timeout=5)
            engine.handle_turn(session_id, f"そらが あおいよ {i}", seconds_spent=1)
        except BaseException as exc:  # noqa: BLE001 - スレッド内の例外は主手で確認する
            errors.append(exc)

    threads = [threading.Thread(target=send, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert not any(t.is_alive() for t in threads), "ターン処理が完了しない"
    assert errors == []

    messages = store.list_messages(session_id)
    child_turns = [m for m in messages if m.role is MessageRole.CHILD]
    assert len(child_turns) == 5
    # 深掘りは高々 3 回、補足は 1 回、終了は 1 回（直列化で状態が進むことの証明）
    states = [m.state for m in messages if m.role is MessageRole.ASSISTANT]
    assert states.count(DialogueState.EXPLORATION) == 3
    assert states.count(DialogueState.EXPLANATION) == 1
    assert states.count(DialogueState.COMPLETE) == 1
    assert not store.get_session(session_id).is_active
    assert len(store.list_mission_results(kid.id)) == 1


class _BlockingOnlyLLM:
    """generate_response_stream を持たない LLM (後方互換の確認用)。"""

    def __init__(self, responses: list[str]) -> None:
        self._fake = FakeLLM(responses=responses)

    def generate_response(self, messages, schema=None, *, temperature=None):  # noqa: ANN001, ANN201
        return self._fake.generate_response(messages, schema, temperature=temperature)

    def evaluate_thinking(self, conversation):  # noqa: ANN001
        return self._fake.evaluate_thinking(conversation)


def test_handle_turn_streams_deltas_concatenated_to_reply(store: DataStore) -> None:
    reply = "そらがあおいのは、ひかりが ちって まぶしいからなんだ。なまえは なに？"
    llm = FakeLLM(responses=["i", reply], stream_chunk_size=5)
    _kid, engine, started = start(store, llm)
    deltas: list[str] = []
    turn = engine.handle_turn(
        started.session.id, "なんで そらは あおいの？", on_delta=deltas.append
    )
    assert "".join(deltas) == turn.reply == reply
    kinds = [c["kind"] for c in llm.calls]
    assert kinds == ["generate_response", "generate_response_stream", "evaluate_thinking"]


def test_handle_turn_hint_turn_streams_deltas(store: DataStore) -> None:
    reply = "たいようの ひかりを ぜんぶ みてみよう。"
    llm = FakeLLM(responses=["i", reply], stream_chunk_size=4)
    _kid, engine, started = start(store, llm)
    deltas: list[str] = []
    turn = engine.handle_turn(started.session.id, "わかんない、おしえて", on_delta=deltas.append)
    assert "".join(deltas) == turn.reply == reply


def test_handle_turn_canned_reply_emits_single_delta(store: DataStore) -> None:
    llm = FakeLLM(responses=["i"])
    _kid, engine, started = start(store, llm)
    deltas: list[str] = []
    turn = engine.handle_turn(started.session.id, "マッチあそび したい", on_delta=deltas.append)
    assert deltas == [SAFETY_REPLY]
    assert turn.reply == SAFETY_REPLY


def test_handle_turn_empty_utterance_emits_repeat_delta(store: DataStore) -> None:
    from curioquest.engine.engine import _REPEAT_REQUEST

    llm = FakeLLM(responses=["i"])
    _kid, engine, started = start(store, llm)
    deltas: list[str] = []
    turn = engine.handle_turn(started.session.id, "   ", on_delta=deltas.append)
    assert deltas == [_REPEAT_REQUEST]
    assert turn.reply == _REPEAT_REQUEST


def test_handle_turn_falls_back_for_providers_without_stream(store: DataStore) -> None:
    llm = _BlockingOnlyLLM(["i", "そうなんだ。どうしてだと思う？"])
    _kid, engine, started = start(store, llm)
    deltas: list[str] = []
    turn = engine.handle_turn(
        started.session.id, "ほしは なに？", on_delta=deltas.append
    )
    assert deltas == [turn.reply] == ["そうなんだ。どうしてだと思う？"]


def test_handle_turn_without_on_delta_uses_blocking_path(store: DataStore) -> None:
    llm = FakeLLM(responses=["i", "ふつうの こたえ"])
    _kid, engine, started = start(store, llm)
    turn = engine.handle_turn(started.session.id, "なんで？")
    assert turn.reply == "ふつうの こたえ"
    kinds = [c["kind"] for c in llm.calls]
    assert kinds == ["generate_response", "generate_response", "evaluate_thinking"]
