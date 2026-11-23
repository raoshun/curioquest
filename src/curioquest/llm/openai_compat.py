"""OpenAI 互換 API アダプター（Ollama など、03-architecture 10-2）。

httpx を直接使用し、依存を最小にする。評価・生成の両経路を同一クライアントで持つが、
モデルやパラメータは経路ごとに切り替えられる。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from curioquest.domain.models import ThinkingSignal
from curioquest.llm.base import (
    ChatMessage,
    GeneratedMission,
    LLMError,
    SchemaParser,
    ThinkingEvaluation,
)
from curioquest.llm.jsonutil import extract_json_object
from curioquest.llm.prompts import (
    conversation_to_text,
    evaluation_system_prompt,
    mission_system_prompt,
    mission_user_prompt,
    parse_mission_category,
)

DEFAULT_TIMEOUT_SECONDS = 120.0


def parse_signals(payload: Mapping[str, Any]) -> list[ThinkingSignal]:
    """評価 JSON の signals 配列を検証付きで列挙型へ変換する。未知の値は捨てる。"""
    raw = payload.get("signals")
    if not isinstance(raw, list):
        return []
    signals: list[ThinkingSignal] = []
    for item in raw:
        try:
            signal = ThinkingSignal(str(item))
        except ValueError:
            continue
        if signal not in signals:
            signals.append(signal)
    return signals


class OpenAICompatClient:
    """OpenAI 互換 /chat/completions を呼ぶ最低限のクライアント。"""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        if client is not None:
            if api_key:
                msg = "client を渡す場合は client 側で認証ヘッダーを設定してください"
                raise ValueError(msg)
            self._client = client
        else:
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            self._client = httpx.Client(timeout=timeout, headers=headers)

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        """1 回のチャット補完を実行し、アシスタント発話を返す。"""
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [m.as_dict() for m in messages],
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            response = self._client.post(self._endpoint, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"LLM API がエラーを返しました: {exc.response.status_code}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM API に接続できません: {exc}") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"想定外の応答形式です: {data!r}") from exc
        return str(content or "")


class OpenAICompatLLM:
    """LLMProvider インターフェースを満たす OpenAI 互換プロバイダ。"""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
        evaluation_model: str | None = None,
        response_temperature: float = 0.7,
    ) -> None:
        self._chat_client = OpenAICompatClient(
            base_url=base_url, model=model, api_key=api_key, timeout=timeout, client=client
        )
        self._evaluation_model = evaluation_model
        self._response_temperature = response_temperature

    def generate_response(
        self,
        messages: Sequence[ChatMessage],
        schema: SchemaParser | None = None,
        *,
        temperature: float | None = None,
    ) -> Any:
        if schema is None:
            temp = temperature if temperature is not None else self._response_temperature
            return self._chat_client.chat(messages, temperature=temp)
        content = self._chat_client.chat(
            messages,
            temperature=0.0 if temperature is None else temperature,
            json_mode=True,
        )
        try:
            payload = extract_json_object(content)
        except ValueError as exc:
            raise LLMError(str(exc)) from exc
        return schema(payload)

    def evaluate_thinking(self, conversation: Sequence[ChatMessage]) -> ThinkingEvaluation:
        messages = [
            ChatMessage.system(evaluation_system_prompt()),
            ChatMessage.user(conversation_to_text([c.as_dict() for c in conversation])),
        ]
        content = self._chat_client.chat(
            messages, model=self._evaluation_model, temperature=0.0, json_mode=True
        )
        try:
            payload = extract_json_object(content)
        except ValueError as exc:
            raise LLMError(f"評価応答を解析できません: {content!r}") from exc
        summary = payload.get("summary")
        return ThinkingEvaluation(
            signals=parse_signals(payload),
            shared_idea=bool(payload.get("shared_idea", False)),
            summary=str(summary) if summary else None,
        )

    def generate_mission(
        self,
        profile: Mapping[str, Any],
        history: Sequence[str] = (),
    ) -> GeneratedMission:
        messages = [
            ChatMessage.system(mission_system_prompt()),
            ChatMessage.user(mission_user_prompt(profile, history)),
        ]
        content = self._chat_client.chat(messages, temperature=0.9, json_mode=True)
        try:
            payload = extract_json_object(content)
        except ValueError as exc:
            raise LLMError(f"ミッション応答を解析できません: {content!r}") from exc
        return parse_generated_mission(payload)


def parse_generated_mission(payload: Mapping[str, Any]) -> GeneratedMission:
    """生成 JSON を GeneratedMission に変換する。必須項目が欠けていれば LLMError。"""
    try:
        title = str(payload["title"])
        initial_question = str(payload["initial_question"])
    except KeyError as exc:
        raise LLMError(f"ミッション応答に必須項目がありません: {exc}") from exc
    skills = payload.get("target_skills")
    estimated = payload.get("estimated_minutes", 3)
    return GeneratedMission(
        title=title,
        category=parse_mission_category(payload.get("category")),
        initial_question=initial_question,
        character_id=payload.get("character_id"),
        target_skills=[str(s) for s in skills] if isinstance(skills, list) else [],
        estimated_minutes=int(estimated),
    )
