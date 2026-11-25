"""対話制御エンジン（02-features.md 4-2 / 4-3 / 6-4）。

責務:
- 対話状態の遷移と終了判定（LLM に状態遷移は決めさせない）
- 利用時間の上限管理と、ヒント・深掘り回数上限による対話の長期化防止
- スタンプ・Memory・思考記録の書き込み（付与条件の最終検証はアプリ側）
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from curioquest.db.store import DataStore
from curioquest.domain.models import (
    Character,
    Child,
    DialogueSession,
    DialogueState,
    MemoryType,
    MessageRole,
    Mission,
    MissionEndReason,
    MissionOutcome,
)
from curioquest.engine.rules import (
    FIRST_IDEA_CRITERION,
    SAFETY_REPLY,
    SHARED_IDEA_CONDITION,
    SIGNAL_TO_CRITERIA,
    ChildIntent,
    detect_intent,
    is_asking_for_help,
)
from curioquest.llm.base import ChatMessage, LLMProvider, ThinkingEvaluation

# 状態ごとの追加指示（キャラクターの人設プロンプトに付けて与える）
_STATE_INSTRUCTIONS: dict[DialogueState, str] = {
    DialogueState.INTRO: "ミッションが始まります。明るく短い導入をひと言でください。",
    DialogueState.QUESTION: "問いかけを続け、子どもが考え始めやすくしてください。",
    DialogueState.EXPLORATION: "子どもの考えの根拠を、今の疑問の範囲で一つだけ聞いてください。",
    DialogueState.HINT: "答えは教えず、考え方の入口になる小さなきっかけを一つください。",
    DialogueState.EXPLANATION: "今の疑問に関係する正しい知識を、短くやさしく補足してください。",
    DialogueState.REVIEW: "考えたことと発見を簡潔に振り返ってください。",
}

# 終了理由とミッション結果（02-features.md 6-4。ヒント出し切りもクリア扱い）
_OUTCOME_BY_END_REASON: dict[MissionEndReason, MissionOutcome] = {
    MissionEndReason.SHARED_IDEA: MissionOutcome.COMPLETED,
    MissionEndReason.HINTS_DONE: MissionOutcome.COMPLETED,
    MissionEndReason.CHILD_EXIT: MissionOutcome.CHILD_EXIT,
    MissionEndReason.TIME_UP: MissionOutcome.TIME_UP,
    MissionEndReason.IDLE: MissionOutcome.IDLE,
}

_FAREWELL_REPLY = "わかったよ。今日はここで終わりだね。またいっしょに考えよう。"
_TIME_UP_REPLY = "今日の利用時間にきたみたい。今日はここまで。またあそぼう。"
_IDLE_REPLY = "みんなかえりのじかんにいったみたい。またいっしょに考えような。"
_REPEAT_REQUEST = "ごめんね、よく きこえなかった。もういっかい おしえてくれる？"

# LLM を経由せず決まる応答文 (どこかのターンで必ず全文が音声化される)。
# TTS キャッシュのウォームアップ対象に使う (08-voice-cache 13-1、Issue #8)。
CANNED_REPLIES: tuple[str, ...] = (
    _REPEAT_REQUEST,
    SAFETY_REPLY,
    _FAREWELL_REPLY,
    _TIME_UP_REPLY,
    _IDLE_REPLY,
)
_REVIEW_LEAD = "いっしょに考えたことをふりかえろう。"
_NO_SUMMARY_REVIEW = "たくさんいっしょに考えられたね。よく考えたね。"


class DailyLimitExceeded(Exception):
    """本日の利用上限に達しているためセッションを開始できない。"""

    def __init__(self, child_id: str, daily_limit_minutes: int) -> None:
        super().__init__(f"child {child_id} daily limit {daily_limit_minutes}m reached")
        self.child_id = child_id
        self.daily_limit_minutes = daily_limit_minutes


class SessionClosed(Exception):
    """既に終了したセッションに対する操作。"""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"session {session_id} is already closed")
        self.session_id = session_id


@dataclass
class TurnResult:
    """1 ターンの処理結果。API 層は reply とセッション状態を UI へ反映する。"""

    session: DialogueSession
    reply: str
    intent: ChildIntent | None = None
    evaluation: ThinkingEvaluation | None = None
    stamps_awarded: list[str] = field(default_factory=list)
    reset_needed: bool = False


@dataclass
class DialogueEngine:
    """セッション対話を制御するエンジン。

    1 正常ターンで LLM を呼ぶのは応答生成 1 回と思考評価 1 回まで。
    危険話題・終了・沈黙では LLM を呼ばず固定応答でさばく。
    """

    store: DataStore
    llm: LLMProvider
    max_exploration_turns: int = 3
    max_hints: int = 3
    idle_timeout_seconds: float = 120.0
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _last_activity: dict[str, float] = field(default_factory=dict, repr=False)
    # 同一セッションの並行リクエスト（スレッドプール実行）による状態遷移の
    # 多重実行を防ぐ。セッショングランユリのロックで、別セッションは並行まま。
    _session_locks: dict[str, threading.Lock] = field(default_factory=dict, repr=False, init=False)
    _locks_guard: threading.Lock = field(default_factory=threading.Lock, repr=False, init=False)

    # ------------------------------------------------------------------ API

    def _gate(self, session_id: str) -> threading.Lock:
        """セッションごとの直列化ロックを取得（生成はガードロック下で行う）。"""
        with self._locks_guard:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._session_locks[session_id] = lock
            return lock

    def start_session(
        self,
        child_id: str,
        mission_id: str,
        character_id: str,
        *,
        day: date | str | None = None,
    ) -> TurnResult:
        """セッションを開始し、INTRO の導入応答を返す（02-features.md 7-2）。"""
        child = self.store.get_child(child_id)
        limit_sec = child.daily_limit_minutes * 60
        if self.store.get_used_seconds(child.id, day or date.today()) >= limit_sec:
            raise DailyLimitExceeded(child.id, child.daily_limit_minutes)

        character = self.store.get_character(character_id)
        mission = self.store.get_mission(mission_id)
        session = self.store.create_session(child.id, mission.id, character.id, DialogueState.INTRO)

        conversation = self._conversation_for(
            session, character, mission, child, DialogueState.INTRO
        )
        reply = str(self.llm.generate_response(conversation))
        session = self.store.update_session_state(session.id, DialogueState.QUESTION)
        self.store.append_message(
            session.id, child.id, MessageRole.ASSISTANT, reply, state=DialogueState.INTRO
        )
        self._last_activity[session.id] = self._clock()
        return TurnResult(session=session, reply=reply)

    def handle_turn(
        self,
        session_id: str,
        text: str,
        *,
        seconds_spent: int = 0,
        day: date | str | None = None,
    ) -> TurnResult:
        """子どもの発話 1 件を処理する。同一セッション内は直列化して実行する。"""
        with self._gate(session_id):
            return self._handle_turn_unlocked(
                session_id, text, seconds_spent=seconds_spent, day=day
            )

    def _handle_turn_unlocked(
        self,
        session_id: str,
        text: str,
        *,
        seconds_spent: int = 0,
        day: date | str | None = None,
    ) -> TurnResult:
        """状態遷移・記録・応答生成本体（セッション ゲート保持済みで呼ぶ）。"""
        session = self.store.get_session(session_id)
        if not session.is_active:
            raise SessionClosed(session.id)
        self._last_activity[session.id] = self._clock()

        # 音声認識が空のとき、子どもの意図として扱わない（05-safety-quality）
        # 保存も LLM も呼ばず、聞き直しだけを返す
        if not text.strip():
            return TurnResult(session=session, reply=_REPEAT_REQUEST)

        child = self.store.get_child(session.child_id)
        mission = self.store.get_mission(session.mission_id)
        character = self.store.get_character(session.character_id)
        self.store.append_message(session.id, child.id, MessageRole.CHILD, text)
        intent = detect_intent(text)

        if intent is ChildIntent.DANGEROUS:
            # 安全優先の固定応答へ切り替える（02-features.md 7-1）。LLM は呼ばない。
            self.store.append_message(
                session.id, child.id, MessageRole.ASSISTANT, SAFETY_REPLY, state=session.state
            )
            return TurnResult(session=session, reply=SAFETY_REPLY, intent=intent)

        if intent is ChildIntent.EXIT:
            result = self._finish(session, MissionEndReason.CHILD_EXIT, _FAREWELL_REPLY)
            return self._wrap(result, intent)

        if seconds_spent > 0:
            used = self.store.add_used_seconds(child.id, day or date.today(), seconds_spent)
            if used >= child.daily_limit_minutes * 60:
                result = self._wrap(
                    self._finish(session, MissionEndReason.TIME_UP, _TIME_UP_REPLY), intent
                )
                result.reset_needed = True
                return result

        if session.state is DialogueState.EXPLANATION:
            # 知識の補足まで完了。これ以上の深掘りはしない（4-2 ルール 9, 10）
            result = self._finish(
                session,
                MissionEndReason.HINTS_DONE,
                self._review_message(child.id, session.id),
            )
            self._remember_finished(child, mission, session.id, type=MemoryType.THINKING)
            return self._wrap(result, intent)

        if is_asking_for_help(intent):
            return self._hint_turn(session, child, mission, character, intent)
        return self._normal_turn(session, child, mission, character, intent)

    def maybe_idle(
        self,
        session_id: str,
        *,
        now: float | None = None,
        idle_seconds: float | None = None,
    ) -> TurnResult | None:
        """一定時間応答がないセッションを終了化する（02-features.md 6-4）。

        活動記録がないセッションは現在時刻で初期化するだけで終了しない。
        """
        with self._gate(session_id):
            return self._maybe_idle_unlocked(session_id, now=now, idle_seconds=idle_seconds)

    def _maybe_idle_unlocked(
        self,
        session_id: str,
        *,
        now: float | None = None,
        idle_seconds: float | None = None,
    ) -> TurnResult | None:
        """経過時間判定・終了化本体（セッション ゲート保持済みで呼ぶ）。"""
        session = self.store.get_session(session_id)
        if not session.is_active:
            return None
        now = self._clock() if now is None else now
        last = self._last_activity.get(session_id)
        if last is None:
            self._last_activity[session_id] = now
            return None
        limit = self.idle_timeout_seconds if idle_seconds is None else idle_seconds
        if (now - last) < limit:
            return None
        return self._finish(session, MissionEndReason.IDLE, _IDLE_REPLY)

    def complete(self, session_id: str) -> TurnResult:
        """「おしまい」ボタンなど、発話を介さない明示的なミッション終了（03 12）。

        終了理由は CHILD_EXIT として保存する。同一セッション内は直列化して実行する。
        """
        with self._gate(session_id):
            return self._complete_unlocked(session_id)

    def _complete_unlocked(self, session_id: str) -> TurnResult:
        """終了処理本体（セッション ゲート保持済みで呼ぶ）。"""
        session = self.store.get_session(session_id)
        if not session.is_active:
            raise SessionClosed(session.id)
        return self._finish(session, MissionEndReason.CHILD_EXIT, _FAREWELL_REPLY)

    # -------------------------------------------------------------- helpers

    def _hint_turn(
        self,
        session: DialogueSession,
        child: Child,
        mission: Mission,
        character: Character,
        intent: ChildIntent,
    ) -> TurnResult:
        """「教えて」要求へのヒントターン。出し切ったら終了（6-4）。"""
        hints_used = self._count_assistant_state(session.id, DialogueState.HINT)
        if hints_used >= self.max_hints:
            result = self._finish(
                session,
                MissionEndReason.HINTS_DONE,
                self._review_message(child.id, session.id),
            )
            self._remember_finished(child, mission, session.id, type=MemoryType.THINKING)
            return self._wrap(result, intent)

        conversation = self._conversation_for(
            session, character, mission, child, DialogueState.HINT
        )
        reply = str(self.llm.generate_response(conversation))
        updated = self.store.update_session_state(session.id, DialogueState.HINT)
        self.store.append_message(
            session.id, child.id, MessageRole.ASSISTANT, reply, state=DialogueState.HINT
        )
        return TurnResult(session=updated, reply=reply, intent=intent)

    def _normal_turn(
        self,
        session: DialogueSession,
        child: Child,
        mission: Mission,
        character: Character,
        intent: ChildIntent,
    ) -> TurnResult:
        """通常の深掘りターン。思考評価・思考記録・スタンプ判定を伴う。"""
        n_explorations = self._count_assistant_state(session.id, DialogueState.EXPLORATION)
        planned = (
            DialogueState.EXPLANATION
            if n_explorations >= self.max_exploration_turns
            else DialogueState.EXPLORATION
        )
        conversation = self._conversation_for(session, character, mission, child, planned)
        reply = str(self.llm.generate_response(conversation))

        history = self._history_messages(session.id)
        evaluation = self.llm.evaluate_thinking(history)
        # ChatMessage は OpenAI 形式の role を使うため、子ターン数は保存済みメッセージで数える
        turn_index = (
            sum(1 for m in self.store.list_messages(session.id) if m.role is MessageRole.CHILD) - 1
        )
        self.store.add_thinking_record(
            child.id, session.id, turn_index, list(evaluation.signals), summary=evaluation.summary
        )
        stamps = self._award_stamps(child, session, evaluation)
        self.store.append_message(session.id, child.id, MessageRole.ASSISTANT, reply, state=planned)

        shared = evaluation.shared_idea and SHARED_IDEA_CONDITION in mission.completion_conditions
        if shared:
            # 子どもが考えを共有できた = ミッションクリア（02-features.md 6-4）
            review = self._review_message(child.id, session.id)
            result = self._finish(session, MissionEndReason.SHARED_IDEA, review)
            self._remember_finished(
                child, mission, session.id, type=MemoryType.DISCOVERY, content=evaluation.summary
            )
            return self._wrap(result, intent, evaluation=evaluation, stamps=stamps)

        updated = self.store.update_session_state(session.id, planned)
        return TurnResult(
            session=updated,
            reply=reply,
            intent=intent,
            evaluation=evaluation,
            stamps_awarded=stamps,
        )

    def _finish(
        self, session: DialogueSession, end_reason: MissionEndReason, final_reply: str
    ) -> TurnResult:
        """セッションを終了し、ミッション結果と考えの痕跡を保存する。"""
        updated = self.store.update_session_state(
            session.id, DialogueState.COMPLETE, end_reason=end_reason
        )
        self.store.append_message(
            session.id,
            session.child_id,
            MessageRole.ASSISTANT,
            final_reply,
            state=DialogueState.COMPLETE,
        )
        child_turns = sum(
            1 for m in self.store.list_messages(session.id) if m.role is MessageRole.CHILD
        )
        self.store.save_mission_result(
            session.id,
            session.child_id,
            session.mission_id,
            _OUTCOME_BY_END_REASON[end_reason],
            thinking_summary=self._last_summary(session.child_id, session.id),
            signals={"end_reason": str(end_reason), "child_turns": child_turns},
        )
        self._last_activity.pop(session.id, None)
        return TurnResult(session=updated, reply=final_reply)

    def _wrap(
        self,
        result: TurnResult,
        intent: ChildIntent,
        *,
        evaluation: ThinkingEvaluation | None = None,
        stamps: list[str] | None = None,
    ) -> TurnResult:
        """終了系ターンへ意図・評価・スタンプを詰め直す。"""
        result.intent = intent
        result.evaluation = evaluation
        if stamps is not None:
            result.stamps_awarded = stamps
        return result

    def _award_stamps(
        self, child: Child, session: DialogueSession, evaluation: ThinkingEvaluation
    ) -> list[str]:
        """思考信号から獲得条件を満たしたスタンプを新規のみ付与する（7-1）。"""
        needed: set[str] = set()
        hint_count = self._count_assistant_state(session.id, DialogueState.HINT)
        for signal, criteria in SIGNAL_TO_CRITERIA.items():
            if not evaluation.has_signal(signal):
                continue
            # チャレンジャーは実際にヒントを使ったときだけ
            if criteria == "hint_retry" and hint_count == 0:
                continue
            needed.add(criteria)
        if evaluation.shared_idea:
            needed.add(FIRST_IDEA_CRITERION)
        if not needed:
            return []

        already = {s.id for s in self.store.list_child_stamps(child.id)}
        awarded: list[str] = []
        for stamp in self.store.list_stamps():
            if stamp.criteria not in needed or stamp.id in already:
                continue
            if self.store.award_stamp(child.id, stamp.id, session.id):
                awarded.append(stamp.id)
        return awarded

    def _conversation_for(
        self,
        session: DialogueSession,
        character: Character,
        mission: Mission,
        child: Child,
        state: DialogueState,
    ) -> list[ChatMessage]:
        """人設プロンプトと会話履歴から LLM への入力を作る。"""
        system = ChatMessage.system(self._system_prompt(character, mission, child, state))
        return [system, *self._history_messages(session.id)]

    def _system_prompt(
        self, character: Character, mission: Mission, child: Child, state: DialogueState
    ) -> str:
        """キャラクター人設へミッション情報と現在の状態の指示を添える。"""
        lines = [
            character.system_prompt,
            "",
            f"いま考えてもらうミッション：{mission.title}",
            f"子どもへの問いかけ：{mission.initial_question}",
            f"話している相手：{child.nickname}さん",
            "",
            f"現在の対話状態：{state}。{_STATE_INSTRUCTIONS[state]}",
            "前の発言で示した状態には戻らず、上の現在の状態の指示だけ守ってください。",
        ]
        return "\n".join(lines)

    def _history_messages(self, session_id: str) -> list[ChatMessage]:
        """保存済み会話履歴を LLM 用のメッセージ列へ変換する。"""
        messages: list[ChatMessage] = []
        for m in self.store.list_messages(session_id):
            if m.role is MessageRole.CHILD:
                messages.append(ChatMessage.user(m.content))
            elif m.role is MessageRole.ASSISTANT:
                messages.append(ChatMessage.assistant(m.content))
        return messages

    def _count_assistant_state(self, session_id: str, state: DialogueState) -> int:
        return sum(
            1
            for m in self.store.list_messages(session_id)
            if m.role is MessageRole.ASSISTANT and m.state is state
        )

    def _last_summary(self, child_id: str, session_id: str) -> str | None:
        """指定セッションの直近の思考記録の要約（無ければ None）。"""
        for record in reversed(self.store.list_thinking_records(child_id)):
            if record.session_id == session_id and record.summary:
                return record.summary
        return None

    def _review_message(self, child_id: str, session_id: str) -> str:
        """終了時の振り返り文末。思考記録の要約があれば添える。"""
        summary = self._last_summary(child_id, session_id)
        if summary is None:
            return _NO_SUMMARY_REVIEW
        return f"{_REVIEW_LEAD}{summary}"

    def _remember_finished(
        self,
        child: Child,
        mission: Mission,
        session_id: str,
        *,
        type: MemoryType,
        content: str | None = None,
    ) -> None:
        """ミッション終了時の発見・考え方を Memory へ残す（02-features.md 8-2）。"""
        if content is None:
            content = self._last_summary(child.id, session_id)
        if not content:
            return
        self.store.add_memory(
            child.id, type, content, topic=mission.category, source=f"mission:{mission.id}"
        )
