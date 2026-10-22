"""The OpenAI Responses adapter: request shape, usage in OpenAI's billing units, errors."""

from __future__ import annotations

import httpx2
import openai
import pytest

from fake_providers import OpenAIFake, openai_reply
from zantine.harness.openai_adapter import OpenAIAdapter
from zantine.harness.pricing import model_spec
from zantine.harness.provider import HarnessRequest, ProviderError, ToolSpec, Usage

ASTRA = model_spec("gpt-6-astra")
SOL = model_spec("gpt-6.1-sol")
TOOL = ToolSpec("wait", "Wait.", {"type": "object", "properties": {}, "required": []})
REQUEST = HarnessRequest(system="RULES", tools=(TOOL,), memory=("block 1", "block 2"), tail="NOW")


@pytest.mark.parametrize("spec", [ASTRA, SOL])
def test_request_uses_responses_api_at_medium_effort(spec) -> None:
    params = OpenAIAdapter(spec, OpenAIFake()).build_params(REQUEST)
    assert params["model"] == spec.id
    assert params["reasoning"] == {"effort": "medium"}
    assert params["max_output_tokens"] == spec.max_output_tokens
    assert params["instructions"] == "RULES"
    assert params["tool_choice"] == "auto" and params["parallel_tool_calls"] is False
    assert params["store"] is False
    assert params["tools"] == [
        {
            "type": "function",
            "name": "wait",
            "description": "Wait.",
            "parameters": TOOL.parameters,
            "strict": True,
        }
    ]
    # Stable prefix first, volatile tail last.
    (msg,) = params["input"]
    assert msg["role"] == "user"
    assert [p["text"] for p in msg["content"]] == ["block 1", "block 2", "NOW"]
    # No sampling knobs alongside reasoning.
    assert "temperature" not in params and "top_p" not in params


def test_effort_level_maps_to_the_labs_setting() -> None:
    params = OpenAIAdapter(SOL.at_effort("high"), OpenAIFake()).build_params(REQUEST)
    assert params["reasoning"] == {"effort": SOL.efforts["high"]}


def test_usage_splits_cache_reads_and_writes_out_of_input_tokens() -> None:
    reply = openai_reply(
        ("wait", {}),
        text="Waiting.",
        input_tokens=9000,
        cached=6000,
        output_tokens=700,
        reasoning_tokens=600,
    )
    reply.usage.input_tokens_details.cache_write_tokens = 2000
    client = OpenAIFake(lambda p: reply)
    response = OpenAIAdapter(ASTRA, client).send(REQUEST)
    assert [(c.name, c.arguments) for c in response.tool_calls] == [("wait", {})]
    assert response.text == "Waiting." and response.stop_reason == "tool_use"
    assert response.usage == Usage(1000, 6000, 2000, 700, 600)
    # $10 in, $1 cached, $12.50 cache write, $50 out (reasoning included).
    expected = (1000 * 10 + 6000 * 1 + 2000 * 12.5 + 700 * 50) / 1e6
    assert ASTRA.cost(response.usage) == pytest.approx(expected)
    assert len(client.responses.calls) == 1


def test_long_prompts_are_billed_at_the_long_context_rate() -> None:
    usage = Usage(input_tokens=200_000, cached_input_tokens=100_000, output_tokens=1000)
    assert ASTRA.cost(usage) == pytest.approx((200_000 * 20 + 100_000 * 2 + 1000 * 75) / 1e6)


def test_arguments_are_parsed_from_json() -> None:
    client = OpenAIFake(lambda p: openai_reply(("move", {"to": "Cafeteria"})))
    (call,) = OpenAIAdapter(SOL, client).send(REQUEST).tool_calls
    assert call.arguments == {"to": "Cafeteria"}


@pytest.mark.parametrize(
    ("reply", "stop"),
    [
        (openai_reply(status="incomplete", incomplete="max_output_tokens"), "max_tokens"),
        (openai_reply(status="incomplete", incomplete="content_filter"), "refusal"),
        (openai_reply(text="Done."), "end_turn"),
    ],
)
def test_stop_reasons_are_normalized(reply, stop) -> None:
    assert OpenAIAdapter(SOL, OpenAIFake(lambda p: reply)).send(REQUEST).stop_reason == stop


def test_refusal_content_is_a_refusal() -> None:
    reply = openai_reply()
    part = type(reply.output[0])(type="refusal", refusal="I can't help with that.")
    reply.output.append(type(reply.output[0])(type="message", content=[part]))
    response = OpenAIAdapter(SOL, OpenAIFake(lambda p: reply)).send(REQUEST)
    assert response.stop_reason == "refusal"


def test_missing_reasoning_details_are_unknown_not_zero() -> None:
    reply = openai_reply(("wait", {}), reasoning_tokens=None)
    assert (
        OpenAIAdapter(SOL, OpenAIFake(lambda p: reply)).send(REQUEST).usage.thinking_tokens is None
    )


def _status_error(cls: type[openai.APIStatusError], status: int) -> Exception:
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    ("error", "fatal"),
    [
        (_status_error(openai.RateLimitError, 429), False),
        (_status_error(openai.InternalServerError, 500), False),
        (_status_error(openai.AuthenticationError, 401), True),
        (_status_error(openai.BadRequestError, 400), True),
        (_status_error(openai.NotFoundError, 404), True),
        (
            openai.APIConnectionError(
                request=httpx2.Request("POST", "https://api.openai.com/v1/responses")
            ),
            False,
        ),
    ],
)
def test_api_errors_become_provider_errors(error: Exception, fatal: bool) -> None:
    adapter = OpenAIAdapter(SOL, OpenAIFake(lambda p: error))
    with pytest.raises(ProviderError) as info:
        adapter.send(REQUEST)
    assert info.value.fatal is fatal


def test_real_client_needs_live_env_and_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZANTINE_LIVE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(ProviderError, match="ZANTINE_LIVE"):
        OpenAIAdapter.from_env(ASTRA)
    monkeypatch.setenv("ZANTINE_LIVE", "1")
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        OpenAIAdapter.from_env(ASTRA)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = OpenAIAdapter.from_env(ASTRA)  # builds a client; makes no request
    assert isinstance(adapter.client, openai.OpenAI)


def test_a_failed_response_is_a_provider_error_not_a_free_call() -> None:
    """A reply with status "failed" carries no trustworthy usage; it must not be
    recorded as a successful $0 call."""
    reply = openai_reply(("wait", {}))
    reply.status = "failed"
    reply.error = openai.types.responses.ResponseError(code="server_error", message="boom")
    reply.usage = None
    with pytest.raises(ProviderError, match="server_error: boom") as info:
        OpenAIAdapter(SOL, OpenAIFake(lambda p: reply)).send(REQUEST)
    assert info.value.status is None and not info.value.fatal
