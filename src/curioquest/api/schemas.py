"""REST API のリクエスト・レスポンスモデル（03-architecture 12）。

ドメインモデルをそのまま晒さず、API として安定した形（enum は文字列、
内部専用料理除外）へ変換する境界にする。
"""

from __future__ import annotations

import base64

from pydantic import BaseModel, Field

from curioquest.domain.models import Character, Child, Memory, Mission, Stamp
from curioquest.engine import TurnResult


class ChildCreate(BaseModel):
    """子どもの登録（保護者画面から）。「声でスタート」は 1 画面 1 タップで済むことを優先し、
    ニックネーム以外すべて省略可能とする。"""

    nickname: str = Field(min_length=1, max_length=40)
    birthday: str | None = None
    favorite_topics: list[str] = Field(default_factory=list)
    favorite_character: str | None = None
    daily_limit_minutes: int = Field(default=20, ge=1, le=240)


class ChildPatch(BaseModel):
    """子どもの更新。変更する項目だけを渡す（PATCH 意味論）。"""

    nickname: str | None = Field(default=None, min_length=1, max_length=40)
    birthday: str | None = None
    favorite_topics: list[str] | None = None
    favorite_character: str | None = None
    daily_limit_minutes: int | None = Field(default=None, ge=1, le=240)


class ChildOut(BaseModel):
    id: str
    family_id: str
    nickname: str
    birthday: str | None = None
    favorite_topics: list[str] = Field(default_factory=list)
    favorite_character: str | None = None
    daily_limit_minutes: int

    @classmethod
    def from_domain(cls, child: Child) -> ChildOut:
        return cls(
            id=child.id,
            family_id=child.family_id,
            nickname=child.nickname,
            birthday=child.birthday,
            favorite_topics=list(child.favorite_topics),
            favorite_character=child.favorite_character,
            daily_limit_minutes=child.daily_limit_minutes,
        )


class MissionOut(BaseModel):
    id: str
    category: str
    title: str
    difficulty: int
    target_skills: list[str] = Field(default_factory=list)
    character_id: str
    estimated_minutes: int
    initial_question: str

    @classmethod
    def from_domain(cls, mission: Mission) -> MissionOut:
        return cls(
            id=mission.id,
            category=str(mission.category),
            title=mission.title,
            difficulty=mission.difficulty,
            target_skills=list(mission.target_skills),
            character_id=mission.character_id,
            estimated_minutes=mission.estimated_minutes,
            initial_question=mission.initial_question,
        )


class CharacterOut(BaseModel):
    """キャラクター定義。system_prompt はプロンプトインジェッションの足下に
    なりうるので API では公開しない。"""

    id: str
    name: str
    description: str
    personality: list[str] = Field(default_factory=list)
    thinking_style: list[str] = Field(default_factory=list)
    voice_engine: str
    voice_speed_scale: float


class TodayOut(BaseModel):
    day: str
    mission: MissionOut


class SessionStart(BaseModel):
    """セッション開始。ミッション・キャラクター未指定なら今日の割当から補完する。"""

    child_id: str
    mission_id: str | None = None
    character_id: str | None = None
    day: str | None = None  # YYYY-MM-DD。Tests で日を固定するため指定可能にする


class MessageIn(BaseModel):
    """子どもの発話 1 件。text が空ならエンジンは聞き直しだけを返す。"""

    text: str = ""
    seconds_spent: int = Field(default=0, ge=0, le=3600)
    day: str | None = None


class TurnOut(BaseModel):
    """1 ターンの処理結果。UI は reply を表示し、finished で画面を切り替える。"""

    session_id: str
    child_id: str
    mission_id: str
    character_id: str
    state: str
    reply: str
    intent: str | None = None
    thinking_summary: str | None = None
    end_reason: str | None = None
    finished: bool
    stamps_awarded: list[str] = Field(default_factory=list)
    reset_needed: bool = False

    @classmethod
    def from_result(cls, result: TurnResult) -> TurnOut:
        session = result.session
        return cls(
            session_id=session.id,
            child_id=session.child_id,
            mission_id=session.mission_id,
            character_id=session.character_id,
            state=str(session.state),
            reply=result.reply,
            intent=str(result.intent) if result.intent is not None else None,
            thinking_summary=result.evaluation.summary if result.evaluation else None,
            end_reason=str(session.end_reason) if session.end_reason else None,
            finished=not session.is_active,
            stamps_awarded=list(result.stamps_awarded),
            reset_needed=result.reset_needed,
        )


class VoiceOut(TurnOut):
    """音声ターンの結果。合成できたときだけ audio_wav_b64 に WAV を載せる。"""

    audio_wav_b64: str | None = None
    voice_available: bool = False
    audio_error: str | None = None

    @classmethod
    def from_turn(
        cls, turn: TurnOut, wav: bytes | None, audio_error: str | None = None
    ) -> VoiceOut:
        """テキストターン結果に合成音（WAV バイト列）を添えて組み立てる。

        wav が None のときは audio_error があってもテキストの応答は有効なので、
        finished や reply などの対話結果はそのまま返す。
        """
        return cls(
            **turn.model_dump(),
            audio_wav_b64=base64.b64encode(wav).decode("ascii") if wav is not None else None,
            voice_available=wav is not None,
            audio_error=audio_error,
        )


class StampOut(BaseModel):
    id: str
    name: str
    description: str

    @classmethod
    def from_domain(cls, stamp: Stamp) -> StampOut:
        return cls(id=stamp.id, name=stamp.name, description=stamp.description)


class StampsOut(BaseModel):
    available: int
    earned: int
    stamps: list[StampOut] = Field(default_factory=list)


class MemoryOut(BaseModel):
    id: str
    type: str
    content: str
    topic: str | None = None
    source: str | None = None
    created_at: str | None = None

    @classmethod
    def from_domain(cls, memory: Memory) -> MemoryOut:
        return cls(
            id=memory.id,
            type=str(memory.type),
            content=memory.content,
            topic=memory.topic,
            source=memory.source,
            created_at=memory.created_at,
        )


class DashboardChildOut(BaseModel):
    child_id: str
    nickname: str
    today_mission_title: str | None = None
    used_seconds_today: int = 0
    daily_limit_minutes: int = 0
    missions_finished_today: int = 0
    stamps_earned: int = 0


class DashboardOut(BaseModel):
    day: str
    children: list[DashboardChildOut] = Field(default_factory=list)


def character_out(character: Character) -> CharacterOut:
    """CharacterOut はフィールドが多いためマッパーを関数で切り出す。"""
    return CharacterOut(
        id=character.id,
        name=character.name,
        description=character.description,
        personality=list(character.personality),
        thinking_style=list(character.thinking_style),
        voice_engine=character.voice_engine,
        voice_speed_scale=character.voice_speed_scale,
    )
