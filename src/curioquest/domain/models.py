"""CurioQuest のドメインモデル。

設計仕様 02-features.md / 03-architecture.md に基づく共通のデータ構造。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum


def new_id(prefix: str) -> str:
    """接頭辞付きの一意識別子を生成する（例: child_a1b2c3d4e5f6）。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class DialogueState(StrEnum):
    """対話状態（02-features.md 4-3）。遷移は対話制御エンジンが管理し、LLM に委譲しない。"""

    INTRO = "INTRO"
    QUESTION = "QUESTION"
    THINKING = "THINKING"
    HINT = "HINT"
    EXPLORATION = "EXPLORATION"
    EXPLANATION = "EXPLANATION"
    REVIEW = "REVIEW"
    COMPLETE = "COMPLETE"


COMPLETE_STATES = (DialogueState.COMPLETE,)


class MissionCategory(StrEnum):
    """ミッション分類（02-features.md 6-1）。"""

    CURIOSITY = "curiosity"  # なぜなぜ探検
    REASONING = "reasoning"  # なぞとき探偵
    IMAGINATION = "imagination"  # もしもワールド


class MissionEndReason(StrEnum):
    """ミッション終了条件（02-features.md 6-4）。正解しなくても終了・クリア可能。"""

    SHARED_IDEA = "shared_idea"  # 子どもが考えを説明した
    HINTS_DONE = "hints_done"  # 必要なヒントを提示した
    CHILD_EXIT = "child_exit"  # 子どもが終了を希望した
    TIME_UP = "time_up"  # 保護者設定の利用時間に達した
    IDLE = "idle"  # 一定時間応答がない


class MessageRole(StrEnum):
    CHILD = "child"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MissionOutcome(StrEnum):
    """ミッション結果。正解・不正解では判定しない。"""

    COMPLETED = "completed"  # 考えを共有して完了
    CHILD_EXIT = "child_exit"  # 子どもの終了希望
    TIME_UP = "time_up"  # 利用時間上限に到達
    IDLE = "idle"  # 一定時間応答なし


class MemoryType(StrEnum):
    """Memory の分類（02-features.md 8-1）。"""

    INTEREST = "interest"
    KNOWLEDGE = "knowledge"
    DISCOVERY = "discovery"
    THINKING = "thinking"
    PREFERENCE = "preference"
    OPEN_QUESTION = "open_question"


class ThinkingSignal(StrEnum):
    """思考の痕跡。LLM の評価は参考値とし、付与判定はアプリ側で行う。"""

    EXPLAINED_REASON = "explained_reason"  # 理由を説明した
    HYPOTHESIS = "hypothesis"  # 仮説を立てた
    MULTIPLE_POSSIBILITIES = "multiple_possibilities"  # 複数の可能性を考えた
    CREATIVE_IDEA = "creative_idea"  # 独創的なアイデアを出した
    MADE_STORY = "made_story"  # 自分の物語を作った
    USED_HINT_AND_RETRY = "used_hint_and_retry"  # ヒントを使って再挑戦した
    ASKED_QUESTION = "asked_question"  # 自分から疑問を持った


@dataclass
class Family:
    id: str
    name: str


@dataclass
class Child:
    id: str
    family_id: str
    nickname: str
    birthday: str | None = None
    favorite_topics: list[str] = field(default_factory=list)
    favorite_character: str | None = None
    difficulty_profile: dict[str, int] = field(default_factory=dict)
    daily_limit_minutes: int = 20
    created_at: str | None = None


@dataclass
class Character:
    id: str
    name: str
    description: str
    personality: list[str]
    thinking_style: list[str]
    system_prompt: str
    voice_engine: str = "voicevox"
    voice_speaker_id: int | None = None
    voice_speed_scale: float = 1.0
    voice_pitch_scale: float = 0.0


@dataclass
class Mission:
    id: str
    category: MissionCategory
    title: str
    difficulty: int
    target_skills: list[str]
    character_id: str
    estimated_minutes: int
    initial_question: str
    completion_conditions: list[str] = field(default_factory=list)


@dataclass
class DialogueSession:
    id: str
    child_id: str
    mission_id: str
    character_id: str
    state: DialogueState
    started_at: str | None = None
    ended_at: str | None = None
    end_reason: MissionEndReason | None = None

    @property
    def is_active(self) -> bool:
        return self.end_reason is None and self.state is not DialogueState.COMPLETE


@dataclass
class Message:
    id: str | None
    session_id: str
    child_id: str
    role: MessageRole
    content: str
    state: DialogueState | None = None
    created_at: str | None = None


@dataclass
class Stamp:
    id: str
    name: str
    description: str
    criteria: str  # 獲得条件の判定コード（アプリ側で検証）


@dataclass
class MissionResult:
    id: str
    session_id: str
    child_id: str
    mission_id: str
    outcome: MissionOutcome
    thinking_summary: str | None = None
    signals: dict[str, object] = field(default_factory=dict)
    finished_at: str | None = None


@dataclass
class Memory:
    id: str
    child_id: str
    type: MemoryType
    content: str
    topic: str | None = None
    source: str | None = None
    created_at: str | None = None


@dataclass
class ThinkingRecord:
    id: str
    child_id: str
    session_id: str
    turn_index: int
    signals: list[ThinkingSignal] = field(default_factory=list)
    summary: str | None = None
    created_at: str | None = None
