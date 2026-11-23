"""日替わり・個人最適ミッションの割当 (02-features.md 6-2 / 6-3) のテスト。"""

from __future__ import annotations

import pytest

from curioquest.db.database import Database
from curioquest.db.seed import DEFAULT_MISSIONS, seed_default_data
from curioquest.db.store import DataStore, NotFound
from curioquest.domain.models import MemoryType, Mission, MissionCategory
from curioquest.engine import GENERATED_ID_PREFIX, ClassicMissionPlanner, DailyMissionPlanner
from curioquest.llm.base import GeneratedMission, LLMError
from curioquest.llm.fake import FakeLLM

DAY = "2026-05-01"
OTHER_DAY = "2026-05-02"

GENERATED = GeneratedMission(
    title="かみなりはどこから来るの？",
    category=MissionCategory.CURIOSITY,
    initial_question="空が光る理由を、あなたはどうやって調べる？",
    character_id="professor",
    target_skills=["hypothesis", "made-up-skill"],
    estimated_minutes=99,
)


@pytest.fixture()
def store() -> DataStore:
    db = Database.in_memory()
    db.init_schema()
    data = DataStore(db)
    seed_default_data(data)
    return data


def curious_child(store: DataStore, child_id: str = "child_a") -> str:
    """個人最適の材料（興味）を持った子を作る。"""
    kid = store.create_child("family_home", "あおい", child_id=child_id, birthday="2019-04-02")
    store.add_memory(kid.id, type=MemoryType.INTEREST, content="かみなり", topic="天気")
    return kid.id


def _generated_mission(mission_id: str) -> Mission:
    """missions テーブルへ直接入れるような生成ミッション見本。"""
    return Mission(
        id=mission_id,
        category=MissionCategory.CURIOSITY,
        title="生成されたお題",
        difficulty=1,
        target_skills=["reasoning"],
        character_id="professor",
        estimated_minutes=3,
        initial_question="どうして？",
        completion_conditions=["child_has_shared_an_idea"],
    )


def generated_missions(store: DataStore) -> list[str]:
    return [m.id for m in store.list_missions() if m.id.startswith(GENERATED_ID_PREFIX)]


def test_classic_planner_is_stable_per_child_and_day(store: DataStore) -> None:
    """定番 planner は同じ子・同じ日で常に同じお題。別の日なら別のお題もあり得る (6-1)。"""
    planner = ClassicMissionPlanner(store)
    child = store.get_child(curious_child(store))

    first = planner.plan(child, DAY)
    again = planner.plan(child, DAY)
    assert first.id == again.id
    assert store.get_today_mission(child.id, DAY) is not None

    other = planner.plan(child, OTHER_DAY)
    assert other.id in {m.id for m in DEFAULT_MISSIONS}


def test_classic_planner_ignores_generated_missions(store: DataStore) -> None:
    """生成ミッションを定番の抽選プールに戻さない（定番DBは定番だけ） (6-3)。"""
    store.upsert_mission(_generated_mission(f"{GENERATED_ID_PREFIX}only"))
    pool = ClassicMissionPlanner(store).pool()
    assert len(pool) == len(DEFAULT_MISSIONS)
    assert all(not m.id.startswith(GENERATED_ID_PREFIX) for m in pool)


def test_generated_mission_is_saved_and_assigned(store: DataStore) -> None:
    """興味のある子は生成ミッションが missions と missions_today に残る (6-2, 12-2)。"""
    child = store.get_child(curious_child(store))
    llm = FakeLLM(mission=GENERATED)
    planner = DailyMissionPlanner(store=store, llm=llm)

    mission = planner.plan(child, DAY)

    assert mission.id.startswith(GENERATED_ID_PREFIX)
    assert mission.title == GENERATED.title
    assert store.get_mission(mission.id).initial_question == GENERATED.initial_question
    assert store.get_today_mission(child.id, DAY).id == mission.id
    # 未知の skill は落とし、所要時間は上限へクリップする
    assert mission.target_skills == ["hypothesis"]
    assert mission.estimated_minutes == 15
    # 終了条件はエンジンと同じなので、考えを説明したらクリアになる (6-4)
    assert mission.completion_conditions == ["child_has_shared_an_idea"]

    call = next(c for c in llm.calls if c["kind"] == "generate_mission")
    assert call["profile"]["nickname"] == "あおい"
    assert call["profile"]["interests"][0].startswith("interest: かみなり")
    assert call["profile"]["age_days"] >= 0


def test_same_day_is_not_regenerated(store: DataStore) -> None:
    """一度決めた今日は再生成しない（LLM 呼び出しも増えない） (6-1)。"""
    child = store.get_child(curious_child(store))
    llm = FakeLLM(mission=GENERATED)
    planner = DailyMissionPlanner(store=store, llm=llm)

    first = planner.plan(child, DAY)
    second = planner.plan(store.get_child(child.id), DAY)

    assert second.id == first.id
    assert sum(1 for c in llm.calls if c["kind"] == "generate_mission") == 1
    assert len(generated_missions(store)) == 1


def test_generated_id_repeats_for_same_title(store: DataStore) -> None:
    """同じ子・日・タイトルなら id が一致し、ミッションが重複登録されない (12-2)。"""
    child = store.get_child(curious_child(store))
    planner = DailyMissionPlanner(store=store, llm=FakeLLM(mission=GENERATED))
    other_day = planner.plan(child, OTHER_DAY)

    rebuilt = DailyMissionPlanner(store=store, llm=FakeLLM(mission=GENERATED))
    again = rebuilt.plan(child, OTHER_DAY)

    assert again.id == other_day.id
    assert generated_missions(store) == [other_day.id]


def test_child_without_material_uses_classic_without_llm(store: DataStore) -> None:
    """記憶も favorite も無い子は LLM を呼ばずに定番DBから割り当てる (6-3)。"""
    child = store.create_child("family_home", "はると")
    llm = FakeLLM(mission=GENERATED)
    planner = DailyMissionPlanner(store=store, llm=llm)

    mission = planner.plan(child, DAY)

    assert not mission.id.startswith(GENERATED_ID_PREFIX)
    assert llm.calls == []


def test_llm_failure_falls_back_to_classic(store: DataStore) -> None:
    """LLM が落ちても子どもには必ずお題が出る (6-3, 05-safety-quality)。"""
    child = store.get_child(curious_child(store, "child_fail"))
    planner = DailyMissionPlanner(store=store, llm=FakeLLM(mission=None))

    mission = planner.plan(child, DAY)

    assert mission.id in {m.id for m in DEFAULT_MISSIONS}
    assert store.get_today_mission(child.id, DAY).id == mission.id


def test_unusable_generation_falls_back(store: DataStore) -> None:
    """生成結果が使える Mission でない場合も定番DBへ倒す。"""
    child = store.get_child(curious_child(store, "child_blank"))
    blank = GeneratedMission(title="   ", category=MissionCategory.REASONING, initial_question="")
    planner = DailyMissionPlanner(store=store, llm=FakeLLM(mission=blank))
    assert not planner.plan(child, DAY).id.startswith(GENERATED_ID_PREFIX)

    broken = DailyMissionPlanner(store=store, llm=_BrokenLLM())
    assert not broken.plan(child, OTHER_DAY).id.startswith(GENERATED_ID_PREFIX)


def test_generation_can_be_disabled(store: DataStore) -> None:
    """設定で生成を切ることもできる（デモ・低スペック機向け） (6-3)。"""
    child = store.get_child(curious_child(store, "child_off"))
    llm = FakeLLM(mission=GENERATED)
    planner = DailyMissionPlanner(store=store, llm=llm, generation_enabled=False)

    mission = planner.plan(child, DAY)

    assert not mission.id.startswith(GENERATED_ID_PREFIX)
    assert llm.calls == []


def test_llm_can_be_given_as_lazy_factory(store: DataStore) -> None:
    """LLM は遅延工場でも受け付ける（重い初期化を起動時に走らせない） (10-3)。"""
    child = store.get_child(curious_child(store, "child_lazy"))
    made: list[FakeLLM] = []

    def make_llm() -> FakeLLM:
        made.append(FakeLLM(mission=GENERATED))
        return made[-1]

    planner = DailyMissionPlanner(store=store, llm=make_llm)
    mission = planner.plan(child, DAY)

    assert mission.id.startswith(GENERATED_ID_PREFIX)
    assert len(made) == 1


def test_unknown_category_and_character_are_normalized(store: DataStore) -> None:
    """未知のカテゴリ・キャラクターは定番の値へ寄せる (05-safety-quality)。"""
    child = store.get_child(curious_child(store, "child_odd"))
    odd = GeneratedMission(
        title="ふしぎな なつやすみ",
        category="curiosity-typo",  # type: ignore[arg-type]
        initial_question="いちばん おどろいた できごとは なに？",
        character_id="no-such-character",
    )
    planner = DailyMissionPlanner(store=store, llm=FakeLLM(mission=odd))

    mission = planner.plan(child, DAY)

    assert mission.category is MissionCategory.CURIOSITY
    assert mission.character_id in {c.id for c in store.list_characters()}
    assert mission.difficulty in (1, 2)


def test_planner_without_llm_uses_classic(store: DataStore) -> None:
    """LLM 未設定の planner でも定番DBの割当は機能する。"""
    child = store.get_child(curious_child(store, "child_plain"))
    planner = DailyMissionPlanner(store=store)

    assert not planner.plan(child, DAY).id.startswith(GENERATED_ID_PREFIX)


def test_empty_mission_db_raises_not_found(store: DataStore) -> None:
    """定番DBまで空なら 404 相当の NotFound（アプリ側で 404 になる）。"""
    for mission in store.list_missions():
        store.db.execute("DELETE FROM missions WHERE id = ?", (mission.id,))
    child = store.get_child(curious_child(store, "child_empty"))

    with pytest.raises(NotFound):
        ClassicMissionPlanner(store).plan(child, DAY)


class _BrokenLLM:
    """常に失敗する LLM（realなLLMErrorを装う）。"""

    def generate_mission(self, profile: object, history: object = ()) -> GeneratedMission:
        raise LLMError("応答が JSON ではありません")
