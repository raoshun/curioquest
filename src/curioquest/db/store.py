"""データアクセス層。ドメインオブジェクトと SQLite 行の変換を担う。"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from curioquest.db.database import Database
from curioquest.domain.models import (
    Character,
    Child,
    DialogueSession,
    DialogueState,
    Family,
    Memory,
    MemoryType,
    Message,
    MessageRole,
    Mission,
    MissionCategory,
    MissionEndReason,
    MissionOutcome,
    MissionResult,
    Stamp,
    ThinkingRecord,
    ThinkingSignal,
    new_id,
)


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load_json(raw: str | None, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    return json.loads(raw)


class NotFound(LookupError):
    """指定 ID のレコードが存在しない。"""


class DataStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ family

    def create_family(self, name: str, family_id: str | None = None) -> Family:
        family = Family(id=family_id or new_id("family"), name=name)
        self.db.execute("INSERT INTO families (id, name) VALUES (?, ?)", (family.id, family.name))
        return family

    def get_family(self, family_id: str) -> Family:
        row = self.db.query_one("SELECT * FROM families WHERE id = ?", (family_id,))
        if row is None:
            raise NotFound(family_id)
        return Family(id=row["id"], name=row["name"])

    def list_families(self) -> list[Family]:
        return [
            Family(id=r["id"], name=r["name"])
            for r in self.db.query("SELECT * FROM families ORDER BY created_at")
        ]

    # ------------------------------------------------------------------ child

    def create_child(
        self,
        family_id: str,
        nickname: str,
        *,
        child_id: str | None = None,
        birthday: str | None = None,
        favorite_topics: list[str] | None = None,
        favorite_character: str | None = None,
        difficulty_profile: dict[str, int] | None = None,
        daily_limit_minutes: int = 20,
    ) -> Child:
        child = Child(
            id=child_id or new_id("child"),
            family_id=family_id,
            nickname=nickname,
            birthday=birthday,
            favorite_topics=favorite_topics or [],
            favorite_character=favorite_character,
            difficulty_profile=difficulty_profile or {},
            daily_limit_minutes=daily_limit_minutes,
        )
        self.db.execute(
            """
            INSERT INTO children (
                id, family_id, nickname, birthday, favorite_topics,
                favorite_character, difficulty_profile, daily_limit_minutes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                child.id,
                child.family_id,
                child.nickname,
                child.birthday,
                _j(child.favorite_topics),
                child.favorite_character,
                _j(child.difficulty_profile),
                child.daily_limit_minutes,
            ),
        )
        return child

    def get_child(self, child_id: str) -> Child:
        row = self.db.query_one("SELECT * FROM children WHERE id = ?", (child_id,))
        if row is None:
            raise NotFound(child_id)
        return self._to_child(row)

    def list_children(self, family_id: str) -> list[Child]:
        # created_at は秒精度・id はランダム uuid のため、同一秒に作られた子の並びは
        # rowid（挿入順に単調増加）で確定させる。保護者向け画面は新しい子を先に表示する。
        rows = self.db.query(
            "SELECT * FROM children WHERE family_id = ? ORDER BY created_at DESC, rowid DESC",
            (family_id,),
        )
        return [self._to_child(r) for r in rows]

    def update_child(self, child_id: str, patch: dict[str, Any]) -> Child:
        allowed = {
            "nickname",
            "birthday",
            "favorite_topics",
            "favorite_character",
            "difficulty_profile",
            "daily_limit_minutes",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(f"更新できない項目: {sorted(unknown)}")
        if not patch:
            return self.get_child(child_id)
        columns, values = [], []
        for key, value in patch.items():
            columns.append(f"{key} = ?")
            values.append(_j(value) if key in {"favorite_topics", "difficulty_profile"} else value)
        values.append(child_id)
        self.db.execute(f"UPDATE children SET {', '.join(columns)} WHERE id = ?", tuple(values))
        return self.get_child(child_id)

    def delete_child(self, child_id: str) -> None:
        """子ども単位のデータ削除（AC-12）。ON DELETE CASCADE で従属データも消える。"""
        self.db.execute("DELETE FROM children WHERE id = ?", (child_id,))

    def _to_child(self, row: Any) -> Child:
        return Child(
            id=row["id"],
            family_id=row["family_id"],
            nickname=row["nickname"],
            birthday=row["birthday"],
            favorite_topics=_load_json(row["favorite_topics"], []),
            favorite_character=row["favorite_character"],
            difficulty_profile=_load_json(row["difficulty_profile"], {}),
            daily_limit_minutes=row["daily_limit_minutes"],
            created_at=row["created_at"],
        )

    # -------------------------------------------------------------- character

    def upsert_character(self, character: Character) -> Character:
        self.db.execute(
            """
            INSERT INTO characters (
                id, name, description, personality, thinking_style, system_prompt,
                voice_engine, voice_speaker_id, voice_speed_scale, voice_pitch_scale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                personality = excluded.personality,
                thinking_style = excluded.thinking_style,
                system_prompt = excluded.system_prompt,
                voice_engine = excluded.voice_engine,
                voice_speaker_id = excluded.voice_speaker_id,
                voice_speed_scale = excluded.voice_speed_scale,
                voice_pitch_scale = excluded.voice_pitch_scale
            """,
            (
                character.id,
                character.name,
                character.description,
                _j(character.personality),
                _j(character.thinking_style),
                character.system_prompt,
                character.voice_engine,
                character.voice_speaker_id,
                character.voice_speed_scale,
                character.voice_pitch_scale,
            ),
        )
        return character

    def get_character(self, character_id: str) -> Character:
        row = self.db.query_one("SELECT * FROM characters WHERE id = ?", (character_id,))
        if row is None:
            raise NotFound(character_id)
        return self._to_character(row)

    def list_characters(self) -> list[Character]:
        rows = self.db.query("SELECT * FROM characters ORDER BY id")
        return [self._to_character(r) for r in rows]

    def _to_character(self, row: Any) -> Character:
        return Character(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            personality=_load_json(row["personality"], []),
            thinking_style=_load_json(row["thinking_style"], []),
            system_prompt=row["system_prompt"],
            voice_engine=row["voice_engine"],
            voice_speaker_id=row["voice_speaker_id"],
            voice_speed_scale=row["voice_speed_scale"],
            voice_pitch_scale=row["voice_pitch_scale"],
        )

    # ---------------------------------------------------------------- mission

    def upsert_mission(self, mission: Mission) -> Mission:
        self.db.execute(
            """
            INSERT INTO missions (
                id, category, title, difficulty, target_skills, character_id,
                estimated_minutes, initial_question, completion_conditions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                category = excluded.category,
                title = excluded.title,
                difficulty = excluded.difficulty,
                target_skills = excluded.target_skills,
                character_id = excluded.character_id,
                estimated_minutes = excluded.estimated_minutes,
                initial_question = excluded.initial_question,
                completion_conditions = excluded.completion_conditions
            """,
            (
                mission.id,
                str(mission.category),
                mission.title,
                mission.difficulty,
                _j(mission.target_skills),
                mission.character_id,
                mission.estimated_minutes,
                mission.initial_question,
                _j(mission.completion_conditions),
            ),
        )
        return mission

    def get_mission(self, mission_id: str) -> Mission:
        row = self.db.query_one("SELECT * FROM missions WHERE id = ?", (mission_id,))
        if row is None:
            raise NotFound(mission_id)
        return self._to_mission(row)

    def list_missions(self, category: MissionCategory | None = None) -> list[Mission]:
        if category is None:
            rows = self.db.query("SELECT * FROM missions ORDER BY id")
        else:
            rows = self.db.query(
                "SELECT * FROM missions WHERE category = ? ORDER BY id", (str(category),)
            )
        return [self._to_mission(r) for r in rows]

    def _to_mission(self, row: Any) -> Mission:
        return Mission(
            id=row["id"],
            category=MissionCategory(row["category"]),
            title=row["title"],
            difficulty=row["difficulty"],
            target_skills=_load_json(row["target_skills"], []),
            character_id=row["character_id"],
            estimated_minutes=row["estimated_minutes"],
            initial_question=row["initial_question"],
            completion_conditions=_load_json(row["completion_conditions"], []),
        )

    # ------------------------------------------------------- missions_today ---

    def set_today_mission(self, family_id: str, child_id: str, day: str, mission_id: str) -> None:
        self.db.execute(
            """
            INSERT INTO missions_today (family_id, child_id, day, mission_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (child_id, day) DO UPDATE SET mission_id = excluded.mission_id
            """,
            (family_id, child_id, day, mission_id),
        )

    def get_today_mission(self, child_id: str, day: str) -> Mission | None:
        row = self.db.query_one(
            "SELECT mission_id FROM missions_today WHERE child_id = ? AND day = ?",
            (child_id, day),
        )
        return self.get_mission(row["mission_id"]) if row else None

    # ---------------------------------------------------------------- session

    def create_session(
        self, child_id: str, mission_id: str, character_id: str, state: DialogueState
    ) -> DialogueSession:
        session = DialogueSession(
            id=new_id("sess"),
            child_id=child_id,
            mission_id=mission_id,
            character_id=character_id,
            state=state,
        )
        self.db.execute(
            """
            INSERT INTO sessions (id, child_id, mission_id, character_id, state)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session.id, child_id, mission_id, character_id, str(state)),
        )
        return session

    def get_session(self, session_id: str) -> DialogueSession:
        row = self.db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if row is None:
            raise NotFound(session_id)
        return DialogueSession(
            id=row["id"],
            child_id=row["child_id"],
            mission_id=row["mission_id"],
            character_id=row["character_id"],
            state=DialogueState(row["state"]),
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            end_reason=MissionEndReason(row["end_reason"]) if row["end_reason"] else None,
        )

    def update_session_state(
        self,
        session_id: str,
        state: DialogueState,
        end_reason: MissionEndReason | None = None,
    ) -> DialogueSession:
        reason = str(end_reason) if end_reason else None
        self.db.execute(
            """
            UPDATE sessions
            SET state = ?,
                end_reason = COALESCE(?, end_reason),
                ended_at = CASE WHEN ? IS NOT NULL THEN datetime('now') ELSE ended_at END
            WHERE id = ?
            """,
            (str(state), reason, reason, session_id),
        )
        return self.get_session(session_id)

    def list_active_sessions(self, child_id: str) -> list[DialogueSession]:
        rows = self.db.query(
            "SELECT * FROM sessions WHERE child_id = ? AND end_reason IS NULL ORDER BY started_at",
            (child_id,),
        )
        return [
            DialogueSession(
                id=r["id"],
                child_id=r["child_id"],
                mission_id=r["mission_id"],
                character_id=r["character_id"],
                state=DialogueState(r["state"]),
                started_at=r["started_at"],
            )
            for r in rows
        ]

    # ---------------------------------------------------------------- message

    def append_message(
        self,
        session_id: str,
        child_id: str,
        role: MessageRole,
        content: str,
        state: DialogueState | None = None,
    ) -> Message:
        cur = self.db.execute(
            """
            INSERT INTO messages (session_id, child_id, role, content, state)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, child_id, str(role), content, str(state) if state else None),
        )
        return Message(
            id=str(cur.lastrowid),
            session_id=session_id,
            child_id=child_id,
            role=role,
            content=content,
            state=state,
        )

    def list_messages(self, session_id: str) -> list[Message]:
        rows = self.db.query(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id", (session_id,)
        )
        return [self._to_message(r) for r in rows]

    def list_messages_for_child(self, child_id: str, limit: int = 200) -> list[Message]:
        """子ども単位の会話履歴（家庭・子ども単位で分離: 05-safety-quality）。"""
        rows = self.db.query(
            "SELECT * FROM messages WHERE child_id = ? ORDER BY id DESC LIMIT ?",
            (child_id, limit),
        )
        return [self._to_message(r) for r in reversed(rows)]

    def _to_message(self, row: Any) -> Message:
        return Message(
            id=str(row["id"]),
            session_id=row["session_id"],
            child_id=row["child_id"],
            role=MessageRole(row["role"]),
            content=row["content"],
            state=DialogueState(row["state"]) if row["state"] else None,
            created_at=row["created_at"],
        )

    # --------------------------------------------------------- mission result

    def save_mission_result(
        self,
        session_id: str,
        child_id: str,
        mission_id: str,
        outcome: MissionOutcome,
        thinking_summary: str | None = None,
        signals: dict[str, Any] | None = None,
        finished_at: str | None = None,
    ) -> MissionResult:
        # finished_at 未指定時は DB デフォルト (UTC の datetime('now')) を使う
        result = MissionResult(
            id=new_id("mres"),
            session_id=session_id,
            child_id=child_id,
            mission_id=mission_id,
            outcome=outcome,
            thinking_summary=thinking_summary,
            signals=signals or {},
            finished_at=finished_at,
        )
        if finished_at is None:
            self.db.execute(
                """
                INSERT INTO mission_results
                    (id, session_id, child_id, mission_id, outcome, thinking_summary, signals)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.id,
                    session_id,
                    child_id,
                    mission_id,
                    str(outcome),
                    thinking_summary,
                    _j(result.signals),
                ),
            )
        else:
            self.db.execute(
                """
                INSERT INTO mission_results
                    (id, session_id, child_id, mission_id, outcome, thinking_summary, signals,
                     finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.id,
                    session_id,
                    child_id,
                    mission_id,
                    str(outcome),
                    thinking_summary,
                    _j(result.signals),
                    finished_at,
                ),
            )
        return result

    def list_mission_results(self, child_id: str) -> list[MissionResult]:
        rows = self.db.query(
            "SELECT * FROM mission_results WHERE child_id = ? ORDER BY finished_at DESC, id",
            (child_id,),
        )
        return [
            MissionResult(
                id=r["id"],
                session_id=r["session_id"],
                child_id=r["child_id"],
                mission_id=r["mission_id"],
                outcome=MissionOutcome(r["outcome"]),
                thinking_summary=r["thinking_summary"],
                signals=_load_json(r["signals"], {}),
                finished_at=r["finished_at"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ stamp

    def upsert_stamp(self, stamp: Stamp) -> Stamp:
        self.db.execute(
            """
            INSERT INTO stamps (id, name, description, criteria) VALUES (?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                criteria = excluded.criteria
            """,
            (stamp.id, stamp.name, stamp.description, stamp.criteria),
        )
        return stamp

    def list_stamps(self) -> list[Stamp]:
        return [
            Stamp(id=r["id"], name=r["name"], description=r["description"], criteria=r["criteria"])
            for r in self.db.query("SELECT * FROM stamps ORDER BY id")
        ]

    def award_stamp(self, child_id: str, stamp_id: str, session_id: str | None = None) -> bool:
        """スタンプを付与する。既に同じスタンプを持っている場合は False（繰り返せない）。"""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO child_stamps (child_id, stamp_id, session_id) VALUES (?, ?, ?)",
            (child_id, stamp_id, session_id),
        )
        return cur.rowcount > 0

    def has_stamp(self, child_id: str, stamp_id: str) -> bool:
        row = self.db.query_one(
            "SELECT 1 FROM child_stamps WHERE child_id = ? AND stamp_id = ?",
            (child_id, stamp_id),
        )
        return row is not None

    def list_child_stamps(self, child_id: str) -> list[Stamp]:
        rows = self.db.query(
            """
            SELECT s.* FROM stamps s
            JOIN child_stamps cs ON cs.stamp_id = s.id
            WHERE cs.child_id = ?
            ORDER BY cs.awarded_at, s.id
            """,
            (child_id,),
        )
        return [
            Stamp(id=r["id"], name=r["name"], description=r["description"], criteria=r["criteria"])
            for r in rows
        ]

    # ---------------------------------------------------------------- memory

    def add_memory(
        self,
        child_id: str,
        type: MemoryType,
        content: str,
        topic: str | None = None,
        source: str | None = None,
    ) -> Memory:
        memory = Memory(
            id=new_id("mem"),
            child_id=child_id,
            type=type,
            content=content,
            topic=topic,
            source=source,
        )
        self.db.execute(
            """
            INSERT INTO memories (id, child_id, type, topic, content, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (memory.id, child_id, str(type), topic, content, source),
        )
        return memory

    def list_memories(
        self, child_id: str, type: MemoryType | None = None, limit: int = 100
    ) -> list[Memory]:
        if type is None:
            rows = self.db.query(
                "SELECT * FROM memories WHERE child_id = ? ORDER BY created_at DESC, id LIMIT ?",
                (child_id, limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM memories WHERE child_id = ? AND type = ?"
                " ORDER BY created_at DESC, id LIMIT ?",
                (child_id, str(type), limit),
            )
        return [self._to_memory(r) for r in rows]

    def delete_memories_for_child(self, child_id: str) -> None:
        self.db.execute("DELETE FROM memories WHERE child_id = ?", (child_id,))

    def _to_memory(self, row: Any) -> Memory:
        return Memory(
            id=row["id"],
            child_id=row["child_id"],
            type=MemoryType(row["type"]),
            content=row["content"],
            topic=row["topic"],
            source=row["source"],
            created_at=row["created_at"],
        )

    # -------------------------------------------------------- thinking record

    def add_thinking_record(
        self,
        child_id: str,
        session_id: str,
        turn_index: int,
        signals: list[ThinkingSignal],
        summary: str | None = None,
    ) -> ThinkingRecord:
        record = ThinkingRecord(
            id=new_id("think"),
            child_id=child_id,
            session_id=session_id,
            turn_index=turn_index,
            signals=signals,
            summary=summary,
        )
        self.db.execute(
            """
            INSERT INTO thinking_records (id, child_id, session_id, turn_index, signals, summary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (record.id, child_id, session_id, turn_index, _j([str(s) for s in signals]), summary),
        )
        return record

    def list_thinking_records(self, child_id: str) -> list[ThinkingRecord]:
        rows = self.db.query(
            "SELECT * FROM thinking_records WHERE child_id = ? ORDER BY created_at, turn_index",
            (child_id,),
        )
        return [
            ThinkingRecord(
                id=r["id"],
                child_id=r["child_id"],
                session_id=r["session_id"],
                turn_index=r["turn_index"],
                signals=[ThinkingSignal(s) for s in _load_json(r["signals"], [])],
                summary=r["summary"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ----------------------------------------------------------- app settings

    def get_setting(self, family_id: str, key: str, default: Any = None) -> Any:
        row = self.db.query_one(
            "SELECT value FROM app_settings WHERE family_id = ? AND key = ?",
            (family_id, key),
        )
        if row is None:
            return default
        return json.loads(row["value"])

    def set_setting(self, family_id: str, key: str, value: Any) -> None:
        self.db.execute(
            """
            INSERT INTO app_settings (family_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT (family_id, key) DO UPDATE SET value = excluded.value
            """,
            (family_id, key, _j(value)),
        )

    # ------------------------------------------------------------ usage daily

    def get_used_seconds(self, child_id: str, day: date | str) -> int:
        row = self.db.query_one(
            "SELECT used_seconds FROM usage_daily WHERE child_id = ? AND day = ?",
            (child_id, str(day)),
        )
        return row["used_seconds"] if row else 0

    def add_used_seconds(self, child_id: str, day: date | str, seconds: int) -> int:
        self.db.execute(
            """
            INSERT INTO usage_daily (child_id, day, used_seconds) VALUES (?, ?, ?)
            ON CONFLICT (child_id, day) DO UPDATE SET
                used_seconds = used_seconds + excluded.used_seconds
            """,
            (child_id, str(day), max(seconds, 0)),
        )
        return self.get_used_seconds(child_id, day)

    # ----------------------------------------------------------------- delete

    def delete_history_for_child(self, child_id: str) -> None:
        """保護者が会話履歴を削除できる (03-architecture 11 重要事項・AC-12 とは別枠)。"""
        self.db.execute("DELETE FROM messages WHERE child_id = ?", (child_id,))
