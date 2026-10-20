"""The Gemini adapter: request shape, usage in Google's billing units, errors."""

from __future__ import annotations

import httpx
import pytest
from google import genai
from google.genai import errors, types

from fake_providers import GoogleFake, google_reply
from zantine.harness.google_adapter import GoogleAdapter
from zantine.harness.pricing import model_spec
from zantine.harness.provider import HarnessRequest, ProviderError, ToolSpec, Usage

ARGON = model_spec("gemini-4-argon")
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"anyOf": [{"type": "string"}, {"type": "number"}]}},
    "required": ["answer"],
    "additionalProperties": False,
}
TOOL = ToolSpec("tasks__work", "Work.", SCHEMA)
REQUEST = HarnessRequest(system="RULES", tools=(TOOL,), memory=("block 1", "block 2"), tail="NOW")


def test_request_uses_thinking_level_medium_and_auto_function_calling() -> None:
    params = GoogleAdapter(ARGON, GoogleFake()).build_params(REQUEST)
    assert params["model"] == "gemini-4-argon"
    config = params["config"]
    assert config["thinking_config"] == {"thinking_level": "medium"}
    assert "thinking_budget" not in config["thinking_config"]  # with a level it's a 400
    assert config["max_output_tokens"] == ARGON.max_output_tokens
    assert config["system_instruction"] == "RULES"
    assert config["tool_config"] == {"function_calling_config": {"mode": "AUTO"}}
    (tools,) = config["tools"]
    assert tools["function_declarations"] == [
        {"name": "tasks__work", "description": "Work.", "parameters_json_schema": SCHEMA}
    ]
    (turn,) = params["contents"]
    assert turn["role"] == "user"
    assert [p["text"] for p in turn["parts"]] == ["block 1", "block 2", "NOW"]


def test_config_is_valid_for_the_sdk() -> None:
    """The dict config parses into the SDK's own types (catches renamed fields)."""
    config = types.GenerateContentConfig(
        **GoogleAdapter(ARGON, GoogleFake()).build_params(REQUEST)["config"]
    )
    assert config.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM
    assert config.tools[0].function_declarations[0].parameters_json_schema == SCHEMA


def test_usage_adds_thoughts_to_output_and_splits_cached_input() -> None:
    reply = google_reply(
        ("tasks__work", {"answer": 7}),
        text="Working.",
        prompt=9000,
        cached=6000,
        candidates=100,
        thoughts=900,
    )
    client = GoogleFake(lambda p: reply)
    response = GoogleAdapter(ARGON, client).send(REQUEST)
    assert [(c.name, c.arguments) for c in response.tool_calls] == [("tasks__work", {"answer": 7})]
    assert response.text == "Working." and response.stop_reason == "tool_use"
    assert response.usage == Usage(3000, 6000, 0, 1000, 900)
    # $2 in, $0.10 cached, $10 out (thinking included).
    assert ARGON.cost(response.usage) == pytest.approx((3000 * 2 + 6000 * 0.1 + 1000 * 10) / 1e6)
    assert len(client.models.calls) == 1


def test_thought_parts_are_not_text() -> None:
    reply = google_reply(("tasks__work", {"answer": "x"}))
    thought = type(reply.candidates[0].content.parts[0])(text="hmm", thought=True)
    reply.candidates[0].content.parts.insert(0, thought)
    assert GoogleAdapter(ARGON, GoogleFake(lambda p: reply)).send(REQUEST).text == ""


@pytest.mark.parametrize(
    ("reply", "stop"),
    [
        (google_reply(finish="MAX_TOKENS"), "max_tokens"),
        (google_reply(finish="SAFETY"), "refusal"),
        (google_reply(block="PROHIBITED_CONTENT"), "refusal"),
        (google_reply(finish="MALFORMED_FUNCTION_CALL"), "malformed_function_call"),
        (google_reply(text="Done."), "end_turn"),
    ],
)
def test_stop_reasons_are_normalized(reply, stop) -> None:
    assert GoogleAdapter(ARGON, GoogleFake(lambda p: reply)).send(REQUEST).stop_reason == stop


def test_missing_counts_are_zero_and_thinking_unknown() -> None:
    reply = google_reply(("tasks__work", {"answer": 1}), cached=None, thoughts=None)
    usage = GoogleAdapter(ARGON, GoogleFake(lambda p: reply)).send(REQUEST).usage
    assert usage.cached_input_tokens == 0 and usage.thinking_tokens is None


@pytest.mark.parametrize(
    ("error", "fatal"),
    [
        (errors.ClientError(429, {"error": {"message": "slow down"}}), False),
        (errors.ServerError(503, {"error": {"message": "overloaded"}}), False),
        (errors.ClientError(400, {"error": {"message": "bad"}}), True),
        (errors.ClientError(403, {"error": {"message": "key"}}), True),
        (httpx.ConnectError("down"), False),
    ],
)
def test_api_errors_become_provider_errors(error: Exception, fatal: bool) -> None:
    adapter = GoogleAdapter(ARGON, GoogleFake(lambda p: error))
    with pytest.raises(ProviderError) as info:
        adapter.send(REQUEST)
    assert info.value.fatal is fatal


def test_real_client_needs_live_env_and_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZANTINE_LIVE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    with pytest.raises(ProviderError, match="ZANTINE_LIVE"):
        GoogleAdapter.from_env(ARGON)
    monkeypatch.setenv("ZANTINE_LIVE", "1")
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(ProviderError, match="GEMINI_API_KEY"):
        GoogleAdapter.from_env(ARGON)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    adapter = GoogleAdapter.from_env(ARGON)  # builds a client; makes no request
    assert isinstance(adapter.client, genai.Client)
