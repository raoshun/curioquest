"""決定論的な Fake LLM。テスト・デモ・技術検証で実モデルなしに検証できるようにする。"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from curioquest.llm.base import (
    ChatMessage,
    GeneratedMission,
    SchemaParser,
    ThinkingEvaluation,
)


@dataclass
class FakeLLM:
    """LLMProvider インターフェースを満たすスタブ。

    responses: generate_response の戻り値を呼び出し順に消費する。尽きたら default_response。
    evaluation / mission: evaluate_thinking / generate_mission の固定戻り値。
    呼び出し記録 (calls) を残すので、エンジン側テストで参照を検証できる。
    """

    responses: Sequence[str] = ()
    default_response: str = "なるほどね。どうしてだと思う？"
    evaluation: ThinkingEvaluation = field(default_factory=ThinkingEvaluation)
    mission: GeneratedMission | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    _responses: deque[str] = field(default_factory=deque, init=False, repr=False)

    def __post_init__(self) -> None:
        self._responses = deque(self.responses)
        self.calls = []

    def generate_response(
        self,
        messages: Sequence[ChatMessage],
        schema: SchemaParser | None = None,
        *,
        temperature: float | None = None,
    ) -> Any:
        self.calls.append({"kind": "generate_response", "messages": list(messages)})
        text = self._responses.popleft() if self._responses else self.default_response
        if schema is None:
            return text
        payload: dict[str, Any] = {"text": text}
        return schema(payload)

    def evaluate_thinking(self, conversation: Sequence[ChatMessage]) -> ThinkingEvaluation:
        self.calls.append({"kind": "evaluate_thinking", "conversation": list(conversation)})
        return self.evaluation

    def generate_mission(
        self,
        profile: Mapping[str, Any],
        history: Sequence[str] = (),
    ) -> GeneratedMission:
        self.calls.append(
            {"kind": "generate_mission", "profile": dict(profile), "history": list(history)}
        )
        if self.mission is None:
            raise AssertionError("FakeLLM.mission が設定されていません")
        return self.mission
