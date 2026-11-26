"""対話制御エンジン（02-features.md 4-2 / 4-3）。

状態遷移・終了判定・報酬付与はエンジンが決める。LLM は応答生成と
思考の痕跡の評価のみ担い、状態の所有権は持たない。
"""

from curioquest.engine.engine import (
    CANNED_REPLIES,
    DailyLimitExceeded,
    DialogueEngine,
    SessionClosed,
    TurnResult,
)
from curioquest.engine.missions import (
    GENERATED_ID_PREFIX,
    ClassicMissionPlanner,
    DailyMissionPlanner,
    MissionPlanner,
    classic_missions,
    digest_index,
)
from curioquest.engine.rules import ChildIntent, detect_intent, is_asking_for_help

__all__ = [
    "CANNED_REPLIES",
    "GENERATED_ID_PREFIX",
    "ChildIntent",
    "ClassicMissionPlanner",
    "DailyLimitExceeded",
    "DailyMissionPlanner",
    "DialogueEngine",
    "MissionPlanner",
    "SessionClosed",
    "TurnResult",
    "classic_missions",
    "detect_intent",
    "digest_index",
    "is_asking_for_help",
]
