"""対話制御エンジン（02-features.md 4-2 / 4-3）。

状態遷移・終了判定・報酬付与はエンジンが決める。LLM は応答生成と
思考の痕跡の評価のみ担い、状態の所有権は持たない。
"""

from curioquest.engine.engine import (
    DailyLimitExceeded,
    DialogueEngine,
    SessionClosed,
    TurnResult,
)
from curioquest.engine.rules import ChildIntent, detect_intent, is_asking_for_help

__all__ = [
    "ChildIntent",
    "DailyLimitExceeded",
    "DialogueEngine",
    "SessionClosed",
    "TurnResult",
    "detect_intent",
    "is_asking_for_help",
]
