"""LLM プロバイダの抽象化（03-architecture 10-3）。

通常対話と思考評価を別経路に分け、状態遷移はアプリ側のエンジンが持つ。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from curioquest.domain.models import MissionCategory, ThinkingSignal


class LLMError(RuntimeError):
    """LLM の呼び出しまたは応答解析の失敗。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ChatMessage:
    """LLM に渡す 1 件のメッセージ。role は system/user/assistant。"""

    role: str
    content: str

    @classmethod
    def system(cls, content: str) -> ChatMessage:
        return cls("system", content)

    @classmethod
    def user(cls, content: str) -> ChatMessage:
        return cls("user", content)

    @classmethod
    def assistant(cls, content: str) -> ChatMessage:
        return cls("assistant", content)

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class ThinkingEvaluation:
    """思考評価の結果。

    signals は参考値であり、報酬の最終判定はアプリ側で行う（05-safety-quality）。
    """

    signals: list[ThinkingSignal] = field(default_factory=list)
    shared_idea: bool = False
    summary: str | None = None

    def has_signal(self, signal: ThinkingSignal) -> bool:
        return signal in self.signals


@dataclass
class GeneratedMission:
    """LLM が生成したミッション案（02-features 6-2 の日替わり問題）。"""

    title: str
    category: MissionCategory
    initial_question: str
    character_id: str | None = None
    target_skills: list[str] = field(default_factory=list)
    estimated_minutes: int = 3


SchemaParser = Callable[[dict[str, Any]], Any]
"""JSON オブジェクトをドメインオブジェクトへ変換するパーサ型。"""


@runtime_checkable
class LLMProvider(Protocol):
    """モデル非依存のプロバイダインターフェース。対話生成と評価処理を分離する。"""

    def generate_response(
        self,
        messages: Sequence[ChatMessage],
        schema: SchemaParser | None = None,
        *,
        temperature: float | None = None,
    ) -> Any:
        """通常対話を生成する。schema を渡した場合は JSON を変換して返す。"""

    def generate_response_stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
    ) -> Iterator[str]:
        """通常対話をテキスト差分のストリームとして生成する。

        連結すると generate_response と同じ応答になる。差分生成に非対応の
        プロバイダは未実装としてよい (エンジン側は generate_response へ退避する)。
        """

    def evaluate_thinking(self, conversation: Sequence[ChatMessage]) -> ThinkingEvaluation:
        """会話の思考の痕跡を評価する（対話生成とは独立の経路）。"""

    def generate_mission(
        self,
        profile: Mapping[str, Any],
        history: Sequence[str] = (),
    ) -> GeneratedMission:
        """子どものプロファイルと履歴をもとにミッション案を生成する。"""
