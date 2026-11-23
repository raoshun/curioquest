"""今日のミッション割り当て（02-features.md 6-2 / 6-3 のハイブリッド方式）。

定番問題DBからの決定的な割当を土台にし、その子の記憶や過去の対話がある場合に限って
LLM で個人最適ミッションを生成して上書きする。生成が使えなくても子どもには必ず
お題が出るよう、必ず定番DBへフォールバックする。

「いつ生成し、何を保存し、いつ諦めるか」の判断はアプリ側が持つ（LLM に委譲しない）。
状態を所有しないのは対話エンジンと同じ原則 (03-architecture 10-3) である。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from curioquest.db.store import DataStore, NotFound
from curioquest.domain.models import Child, Memory, MemoryType, Mission, MissionCategory
from curioquest.engine.rules import SHARED_IDEA_CONDITION
from curioquest.llm.base import GeneratedMission, LLMProvider
from curioquest.utils.dates import to_utc_day

logger = logging.getLogger("curioquest.engine.missions")

GENERATED_ID_PREFIX = "gen_"
"""生成ミッションの id 接頭辞。定番DB（why_ / mystery_ / if_）と区別するための目印。"""

MAX_PROFILE_ITEMS = 8
"""ミッション生成プロンプトに渡す記憶・履歴の上限（コンテキストを太らせないため）。"""

_PROFILE_MEMORY_TYPES = (
    MemoryType.INTEREST,
    MemoryType.DISCOVERY,
    MemoryType.THINKING,
    MemoryType.OPEN_QUESTION,
)

_SKILLS_BY_CATEGORY: dict[MissionCategory, list[str]] = {
    MissionCategory.CURIOSITY: ["observation", "hypothesis", "reasoning"],
    MissionCategory.REASONING: ["observation", "hypothesis", "reasoning"],
    MissionCategory.IMAGINATION: ["imagination", "expression", "storytelling"],
}
_KNOWN_SKILLS = frozenset(
    {
        "observation",
        "hypothesis",
        "reasoning",
        "communication",
        "imagination",
        "expression",
        "storytelling",
    }
)

LLMSource = LLMProvider | Callable[[], LLMProvider]
"""LLM は即使える実体でも、遅く作る工場でも受け付ける（本番の重い初期化を止めるため）。"""


def digest_index(seed: str, population: int) -> int:
    """同じ seed なら何度でも同じ添字になる決定的な選択 (02-features 6-1)。"""
    return int(hashlib.sha256(seed.encode()).hexdigest(), 16) % population


def classic_missions(missions: Iterable[Mission]) -> list[Mission]:
    """定番問題DBのミッションだけに絞る。生成ミッションは抽選に戻さない。"""
    return [m for m in missions if not m.id.startswith(GENERATED_ID_PREFIX)]


class MissionPlanner(Protocol):
    """今日のミッションを決める役割。受付け側はこの肩書きにだけ依存する。"""

    def plan(self, child: Child, day: str) -> Mission:
        """子どもと日付に対しお題を 1 件返し、割当を保存する。"""


@dataclass
class ClassicMissionPlanner:
    """定番DBから sha256 で 1 件を決定的に割り当てる（LLM を使わない安定版）。"""

    store: DataStore

    def assigned(self, child: Child, day: str) -> Mission | None:
        """割当済みならそのお題を返す。同じ子・同じ日は再生成も再抽選もしない。"""
        return self.store.get_today_mission(child.id, day)

    def pool(self) -> list[Mission]:
        return classic_missions(self.store.list_missions())

    def assign(self, child: Child, day: str, mission: Mission) -> None:
        self.store.set_today_mission(child.family_id, child.id, day, mission.id)

    def plan(self, child: Child, day: str) -> Mission:
        mission = self.assigned(child, day)
        if mission is not None:
            return mission
        pool = self.pool()
        if not pool:
            raise NotFound("missions")
        chosen = pool[digest_index(f"{child.id}:{day}", len(pool))]
        self.assign(child, day, chosen)
        return chosen


@dataclass
class DailyMissionPlanner:
    """日替わり・個人最適のお題 planner（6-3）。

    定番DBの決定論的な割当へ倒す条件:
    - generation_enabled が False（設定で生成を無効化している）
    - その子の記憶も favorite も無く、個人最適の材料が無い
    - LLM が失敗した、または返ってきたお題が使いものにならなかった
    """

    store: DataStore
    llm: Any = None
    generation_enabled: bool = True
    classic: ClassicMissionPlanner = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.classic = ClassicMissionPlanner(self.store)

    def plan(self, child: Child, day: str) -> Mission:
        mission = self.classic.assigned(child, day)
        if mission is not None:
            return mission
        if self.generation_enabled:
            generated = self._try_generate(child, day)
            if generated is not None:
                return generated
        return self.classic.plan(child, day)

    # ------------------------------------------------------------ 生成

    def _try_generate(self, child: Child, day: str) -> Mission | None:
        profile = self.build_profile(child, day)
        if profile is None:
            logger.debug("個人最適の材料がないため定番DBを使います: child=%s", child.id)
            return None
        provider = self._provider()
        if provider is None:
            return None
        try:
            candidate = provider.generate_mission(profile, self.build_history(child))
            mission = self.build_mission(child, day, candidate)
        except Exception as exc:  # noqa: BLE001 - 生成の失敗は常に定番フォールバック
            logger.warning("ミッション生成に使えないお題が返ってきたため定番DBへ倒します: %s", exc)
            return None
        if mission is None:
            logger.warning("ミッション生成の結果が不十分なので定番DBへ倒します: child=%s", child.id)
            return None
        self.store.upsert_mission(mission)
        self.classic.assign(child, day, mission)
        return mission

    def _provider(self) -> LLMProvider | None:
        llm = self.llm
        if llm is None:
            return None
        if hasattr(llm, "generate_mission"):
            return llm
        if callable(llm):
            return llm()
        return None

    def build_profile(self, child: Child, day: str) -> dict[str, Any] | None:
        """生成プロンプトへ渡すその子の人柄。個人最適の材料が無ければ None。"""
        memories = self.store.list_memories(child.id)
        profile: dict[str, Any] = {"nickname": child.nickname}
        if child.birthday:
            profile["age_days"] = _age_in_years(child.birthday)
        if child.favorite_topics:
            profile["favorite_topics"] = list(child.favorite_topics)[:MAX_PROFILE_ITEMS]
        if child.favorite_character:
            profile["favorite_character"] = self._character_name(child.favorite_character)
        typed = [m for m in memories if m.type in _PROFILE_MEMORY_TYPES][:MAX_PROFILE_ITEMS]
        if typed:
            profile["interests"] = [_memory_text(m) for m in typed]
        today = [
            _memory_text(m)
            for m in memories[:MAX_PROFILE_ITEMS]
            if m.type is MemoryType.DISCOVERY and to_utc_day(m.created_at) == day
        ]
        if today:
            profile["today_discoveries"] = today
        if len(profile) == 1:  # nickname しか無いなら生成しても定番と変わらない
            return None
        return profile

    def build_history(self, child: Child) -> list[str]:
        """ミッション結果の振り返りを中心に、直近の出来事だけを渡す。"""
        items: list[str] = []
        for result in self.store.list_mission_results(child.id)[:MAX_PROFILE_ITEMS]:
            title = self._mission_title(result.mission_id)
            detail = result.thinking_summary or ""
            items.append(f"{title}：{detail}" if detail else title)
        return items

    def build_mission(
        self, child: Child, day: str, generated: GeneratedMission | None
    ) -> Mission | None:
        """生成案を Mission へ変換する（title と initial_question が無ければ None）。"""
        if generated is None:
            return None
        title = str(generated.title or "").strip()[:60]
        question = str(generated.initial_question or "").strip()[:300]
        if not title or not question:
            return None
        category = _category(generated.category)
        return Mission(
            id=_generated_id(child, day, title),
            category=category or MissionCategory.CURIOSITY,
            title=title,
            difficulty=self._difficulty(child),
            target_skills=_skills(generated.target_skills, category),
            character_id=self._character_id(generated.character_id, category),
            estimated_minutes=_minutes(generated.estimated_minutes),
            initial_question=question,
            # エンジンの終了条件（考えを説明したらクリア）と必ず噛み合わせられるようにする。
            completion_conditions=[SHARED_IDEA_CONDITION],
        )

    # ------------------------------------------------------------ 補助

    def _difficulty(self, child: Child) -> int:
        """思考の記録が積み上がってきた子には、ひと捻りあるお題を出す。"""
        return 2 if len(self.store.list_thinking_records(child.id)) >= 6 else 1

    def _character_id(self, requested: Any, category: MissionCategory | None) -> str:
        """既知のキャラクターIDへ倒す。未知ならカテゴリの定番キャラクターに寄せる。"""
        known = {c.id for c in self.store.list_characters()}
        candidate = str(requested).strip() if requested else ""
        if candidate in known:
            return candidate
        pool = self.classic.pool()
        for mission in pool:
            if category is not None and mission.category is category:
                return mission.character_id
        if pool:
            return pool[0].character_id
        raise NotFound("characters")

    def _character_name(self, character_id: str) -> str:
        try:
            return self.store.get_character(character_id).name
        except NotFound:
            return character_id

    def _mission_title(self, mission_id: str) -> str:
        try:
            return self.store.get_mission(mission_id).title
        except NotFound:
            return mission_id


def _category(value: Any) -> MissionCategory | None:
    """LLM が返したカテゴリ文字列を検証して MissionCategory に寄せる（未知なら None）。"""
    try:
        return MissionCategory(str(value))
    except (ValueError, TypeError):
        return None


def _generated_id(child: Child, day: str, title: str) -> str:
    """同じ子・日・タイトルなら再生成しても同じ id（upsert なので重複登録しない）。"""
    digest = hashlib.sha256(f"{child.id}:{day}:{title}".encode()).hexdigest()
    return f"{GENERATED_ID_PREFIX}{digest[:16]}"


def _skills(requested: Sequence[str] | None, category: MissionCategory | None) -> list[str]:
    """未知の skill 名を落とし、何も残らなければカテゴリ既定を使う。"""
    known = list(dict.fromkeys(s for s in (requested or ()) if s in _KNOWN_SKILLS))
    if known:
        return known
    return list(_SKILLS_BY_CATEGORY.get(category or MissionCategory.CURIOSITY, ["reasoning"]))


def _minutes(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 3
    return min(max(value, 1), 15)


def _age_in_years(birthday: str) -> int:
    """誕生日 ('YYYY-MM-DD') から年齢（才）を返す。お題の難しさの参考程度に使う。"""
    try:
        born = date.fromisoformat(birthday[:10])
    except ValueError:
        return 0
    today = date.today()
    return max(0, today.year - born.year - ((today.month, today.day) < (born.month, born.day)))


def _memory_text(memory: Memory) -> str:
    topic = f"（{memory.topic}）" if memory.topic else ""
    return f"{memory.type}: {memory.content}{topic}"
