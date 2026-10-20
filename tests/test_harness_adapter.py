"""The Anthropic adapter: request shape, prompt caching, usage and errors (fake client)."""

from __future__ import annotations

import anthropic
import httpx2
import pytest

from fake_provider import FakeClient, message, text, tool_use
from zantine.harness.anthropic_adapter import AnthropicAdapter
from zantine.harness.pricing import model_spec
from zantine.harness.provider import HarnessRequest, ProviderError, ToolSpec, Usage

FABLE = model_spec("claude-fable-5-1")
TOOL = ToolSpec("wait", "Wait.", {"type": "object", "properties": {}, "required": []})
REQUEST = HarnessRequest(system="RULES", tools=(TOOL,), memory=("block 1", "block 2"), tail="NOW")


def test_request_uses_medium_effort_auto_tools_and_no_thinking_override() -> None:
    params = AnthropicAdapter(FABLE, FakeClient()).build_params(REQUEST)
    assert params["model"] == "claude-fable-5-1"
    assert params["output_config"] == {"effort": "medium"}
    assert params["max_tokens"] == FABLE.max_output_tokens
    # Thinking is always on for Fable 5.1; forced tool_choice is a 400; no fallback models.
    assert "thinking" not in params and "fallbacks" not in params and "betas" not in params
    assert params["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert params["tools"] == [
        {
            "name": "wait",
            "description": "Wait.",
            "input_schema": TOOL.parameters,
            "strict": True,
        }
    ]


def test_prompt_caching_marks_the_stable_prefix_only() -> None:
    params = AnthropicAdapter(FABLE, FakeClient()).build_params(REQUEST)
    assert params["system"] == [
        {"type": "text", "text": "RULES", "cache_control": {"type": "ephemeral"}}
    ]
    content = params["messages"][0]["content"]
    assert [b["text"] for b in content] == ["block 1", "block 2", "NOW"]
    assert [("cache_control" in b) for b in content] == [False, True, False]


def test_no_memory_means_only_the_system_breakpoint() -> None:
    request = HarnessRequest(system="RULES", tools=(TOOL,), memory=(), tail="NOW")
    params = AnthropicAdapter(FABLE, FakeClient()).build_params(request)
    assert params["messages"][0]["content"] == [{"type": "text", "text": "NOW"}]


def test_response_is_normalized_with_cached_and_thinking_tokens() -> None:
    client = FakeClient(
        lambda p: message(
            text("Waiting."),
            tool_use("wait"),
            input_tokens=12,
            cache_read=3000,
            cache_write=40,
            output_tokens=500,
            thinking_tokens=420,
        )
    )
    response = AnthropicAdapter(FABLE, client).send(REQUEST)
    assert [(c.name, c.arguments) for c in response.tool_calls] == [("wait", {})]
    assert response.text == "Waiting."
    assert response.stop_reason == "tool_use"
    assert response.usage == Usage(12, 3000, 40, 500, 420)
    assert len(client.messages.calls) == 1


def test_missing_thinking_details_are_unknown_not_zero() -> None:
    client = FakeClient(lambda p: message(tool_use("wait"), thinking_tokens=None))
    assert AnthropicAdapter(FABLE, client).send(REQUEST).usage.thinking_tokens is None


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> Exception:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    ("error", "fatal"),
    [
        (_status_error(anthropic.RateLimitError, 429), False),
        (_status_error(anthropic.InternalServerError, 500), False),
        (_status_error(anthropic.AuthenticationError, 401), True),
        (_status_error(anthropic.BadRequestError, 400), True),
        (_status_error(anthropic.NotFoundError, 404), True),
        (
            anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            ),
            False,
        ),
    ],
)
def test_api_errors_become_provider_errors(error: Exception, fatal: bool) -> None:
    adapter = AnthropicAdapter(FABLE, FakeClient(lambda p: error))
    with pytest.raises(ProviderError) as info:
        adapter.send(REQUEST)
    assert info.value.fatal is fatal


def test_real_client_needs_live_env_and_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZANTINE_LIVE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(ProviderError, match="ZANTINE_LIVE"):
        AnthropicAdapter.from_env(FABLE)
    monkeypatch.setenv("ZANTINE_LIVE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        AnthropicAdapter.from_env(FABLE)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    adapter = AnthropicAdapter.from_env(FABLE)  # builds a client; makes no request
    assert isinstance(adapter.client, anthropic.Anthropic)
