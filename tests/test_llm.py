"""OpenAI 互換 LLM プロバイダと FakeLLM のテスト。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from curioquest.config import Settings
from curioquest.domain.models import MissionCategory, ThinkingSignal
from curioquest.llm import (
    ChatMessage,
    FakeLLM,
    GeneratedMission,
    LLMError,
    LLMProvider,
    ThinkingEvaluation,
)
from curioquest.llm.factory import provider_from_settings
from curioquest.llm.jsonutil import extract_json_object
from curioquest.llm.openai_compat import OpenAICompatLLM
from curioquest.llm.prompts import evaluation_system_prompt, mission_system_prompt


def make_llm(handler) -> OpenAICompatLLM:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return OpenAICompatLLM(base_url="http://fake-llm/v1", model="test-model", client=client)


def completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


class TestExtractJsonObject:
    def test_plain_json(self) -> None:
        assert extract_json_object('{"a": 1}') == {"a": 1}

    def test_fenced_json(self) -> None:
        text = '結果はこれです:\n```json\n{"a": 1}\n```\n以上'
        assert extract_json_object(text) == {"a": 1}

    def test_leading_text_and_nested_braces(self) -> None:
        text = 'もちろん。 {"a": {"b": "}"}, "c": 2} 以上です'
        assert extract_json_object(text) == {"a": {"b": "}"}, "c": 2}

    def test_raises_when_absent(self) -> None:
        with pytest.raises(ValueError):
            extract_json_object("JSON はありません")


class TestGenerateResponse:
    def test_returns_assistant_content(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            assert request.url.path == "/v1/chat/completions"
            return completion("どうしてだと思う？")

        llm = make_llm(handler)
        reply = llm.generate_response([ChatMessage.system("s"), ChatMessage.user("そらってなぜ？")])
        assert reply == "どうしてだと思う？"
        assert seen["model"] == "test-model"
        assert seen["messages"][0] == {"role": "system", "content": "s"}
        assert "temperature" in seen

    def test_schema_path_parses_json(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["response_format"] == {"type": "json_object"}
            return completion('前置き {"end": true} 後置き')

        llm = make_llm(handler)
        result = llm.generate_response([ChatMessage.user("x")], schema=lambda p: p["end"])
        assert result is True

    def test_http_error_becomes_llm_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        llm = make_llm(handler)
        with pytest.raises(LLMError) as excinfo:
            llm.generate_response([ChatMessage.user("x")])
        assert excinfo.value.status_code == 500

    def test_connection_error_becomes_llm_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        llm = make_llm(handler)
        with pytest.raises(LLMError):
            llm.generate_response([ChatMessage.user("x")])


class TestEvaluateThinking:
    def test_parses_signals_and_flags(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            payload = {
                "signals": ["explained_reason", "未知のコード", "hypothesis", "explained_reason"],
                "shared_idea": True,
                "summary": "光の反射を自分で説明できた",
            }
            return completion("```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")

        llm = make_llm(handler)
        evaluation = llm.evaluate_thinking(
            [
                ChatMessage.assistant("なぜだと思う？"),
                ChatMessage.user("ひかりが まぶしいから だと おもう"),
            ]
        )
        assert evaluation.signals == [ThinkingSignal.EXPLAINED_REASON, ThinkingSignal.HYPOTHESIS]
        assert evaluation.shared_idea is True
        assert evaluation.summary is not None
        assert "観察者" in captured["messages"][0]["content"]
        assert captured["response_format"] == {"type": "json_object"}

    def test_invalid_json_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return completion("ごめん、よくわかりません")

        llm = make_llm(handler)
        with pytest.raises(LLMError):
            llm.evaluate_thinking([ChatMessage.user("えへへ")])

    def test_empty_signals_is_valid(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return completion('{"signals": [], "shared_idea": false}')

        llm = make_llm(handler)
        evaluation = llm.evaluate_thinking([ChatMessage.user("しらない")])
        assert evaluation.signals == []
        assert evaluation.shared_idea is False
        assert evaluation.summary is None


class TestGenerateMission:
    def test_parses_mission(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert "ゲームデザイナー" in body["messages"][0]["content"]
            payload = {
                "title": "かべの ひび の ひみつ",
                "category": "reasoning",
                "initial_question": "かべの ひび、なにも つかわずに ふさぐには？",
                "target_skills": ["observation", "hypothesis"],
                "estimated_minutes": 4,
            }
            return completion(json.dumps(payload, ensure_ascii=False))

        llm = make_llm(handler)
        mission = llm.generate_mission({"nickname": "あおい"}, ["そらは どうして あおい？"])
        assert mission.category is MissionCategory.REASONING
        assert mission.target_skills == ["observation", "hypothesis"]
        assert mission.estimated_minutes == 4

    def test_unknown_category_falls_back(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = {"title": "t", "category": "horror", "initial_question": "q"}
            return completion(json.dumps(payload, ensure_ascii=False))

        llm = make_llm(handler)
        mission = llm.generate_mission({})
        assert mission.category is MissionCategory.CURIOSITY
        assert mission.estimated_minutes == 3

    def test_missing_required_field_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return completion('{"title": "だけ"}')

        llm = make_llm(handler)
        with pytest.raises(LLMError):
            llm.generate_mission({})


class TestPrompts:
    def test_prompts_render_placeholders(self) -> None:
        eval_prompt = evaluation_system_prompt()
        assert "{signal_list}" not in eval_prompt
        assert "explained_reason" in eval_prompt
        assert "shared_idea" in eval_prompt
        mission_prompt = mission_system_prompt()
        assert "{{" not in mission_prompt
        assert "imagination" in mission_prompt


class TestFakeLLM:
    def test_consumes_scripted_responses(self) -> None:
        llm = FakeLLM(responses=["1つ目", "2つ目"], default_response="残り")
        msgs = [ChatMessage.user("x")]
        assert llm.generate_response(msgs) == "1つ目"
        assert llm.generate_response(msgs) == "2つ目"
        assert llm.generate_response(msgs) == "残り"
        assert len(llm.calls) == 3

    def test_protocol_conformance(self) -> None:
        assert isinstance(FakeLLM(), LLMProvider)

    def test_evaluation_and_mission(self) -> None:
        mission = GeneratedMission(
            title="t", category=MissionCategory.IMAGINATION, initial_question="q"
        )
        evaluation = ThinkingEvaluation(shared_idea=True)
        llm = FakeLLM(evaluation=evaluation, mission=mission)
        assert llm.evaluate_thinking([]) is evaluation
        assert llm.generate_mission({"nickname": "はると"}) is mission


class TestFactory:
    def test_fake_scheme_returns_fake(self) -> None:
        provider = provider_from_settings(Settings(llm_base_url="fake://localhost"))
        assert isinstance(provider, FakeLLM)

    def test_default_returns_compat(self) -> None:
        provider = provider_from_settings(
            Settings(llm_base_url="http://ollama:11434/v1", llm_model="qwen2.5")
        )
        assert isinstance(provider, OpenAICompatLLM)
