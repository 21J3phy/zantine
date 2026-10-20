"""xAI (Grok 4.7) and Meta (Muse Wonder): the OpenAI-compatible Responses adapters."""

from __future__ import annotations

import openai
import pytest

from fake_providers import OpenAIFake, openai_reply, xai_reply
from zantine.harness.ledger import Ledger
from zantine.harness.meta_adapter import MetaAdapter
from zantine.harness.pricing import PER_TOKEN, model_spec
from zantine.harness.provider import HarnessRequest, ProviderError, ToolSpec, Usage, make_adapter
from zantine.harness.xai_adapter import XAIAdapter

GROK = model_spec("grok-4.7")
MUSE = model_spec("muse-wonder-1.0")
TOOL = ToolSpec("wait", "Wait.", {"type": "object", "properties": {}, "required": []})
REQUEST = HarnessRequest(system="RULES", tools=(TOOL,), memory=("block 1",), tail="NOW")


@pytest.mark.parametrize(("cls", "spec"), [(XAIAdapter, GROK), (MetaAdapter, MUSE)])
def test_request_is_responses_api_at_the_labs_medium(cls, spec) -> None:
    params = cls(spec, OpenAIFake()).build_params(REQUEST)
    assert params["model"] == spec.id
    assert params["reasoning"] == {"effort": "medium"}
    assert params["tool_choice"] == "auto" and params["parallel_tool_calls"] is False
    assert params["tools"][0]["strict"] is True
    assert [p["text"] for p in params["input"][0]["content"]] == ["block 1", "NOW"]


def test_xai_reasoning_tokens_are_added_to_output() -> None:
    reply = xai_reply(("wait", {}), input_tokens=9000, cached=6000, output_tokens=300)
    response = XAIAdapter(GROK, OpenAIFake(lambda p: reply)).send(REQUEST)
    assert response.usage == Usage(3000, 6000, 0, 300 + 1500, 1500)
    # $2 in, $0.50 cached, $6 out; reasoning billed as output.
    assert GROK.cost(response.usage) == pytest.approx(
        PER_TOKEN * (3000 * 2 + 6000 * 0.5 + 1800 * 6)
    )
    assert response.raw["provider_cost_usd"] == pytest.approx(0.012345)


def test_xai_reported_cost_is_kept_on_the_call_record() -> None:
    ledger = Ledger(5.0)
    ledger.send(XAIAdapter(GROK, OpenAIFake(reply=xai_reply)), REQUEST, purpose="seat", phase="x")
    (rec,) = ledger.records
    assert rec.provider_cost_usd == pytest.approx(0.012345)
    assert rec.thinking_tokens == 1500 and rec.output_tokens == 2000


def test_xai_long_prompts_reprice_the_whole_call() -> None:
    usage = Usage(input_tokens=150_000, cached_input_tokens=50_000, output_tokens=1000)
    assert GROK.cost(usage) == pytest.approx(PER_TOKEN * (150_000 * 4 + 50_000 * 1 + 1000 * 12))


def test_meta_reasoning_is_inside_output_like_openai() -> None:
    reply = openai_reply(("wait", {}), input_tokens=7000, cached=5000, output_tokens=900)
    response = MetaAdapter(MUSE, OpenAIFake(lambda p: reply)).send(REQUEST)
    assert response.usage == Usage(2000, 5000, 0, 900, 1500)
    assert MUSE.cost(response.usage) == pytest.approx(
        PER_TOKEN * (2000 * 4 + 5000 * 0.2 + 900 * 20)
    )
    assert "provider_cost_usd" not in response.raw


@pytest.mark.parametrize(
    ("spec", "env", "url"),
    [
        (GROK, "XAI_API_KEY", "https://api.x.ai/v1"),
        (MUSE, "META_API_KEY", "https://api.meta.ai/v1"),
    ],
)
def test_real_client_needs_live_env_key_and_uses_the_labs_endpoint(
    monkeypatch: pytest.MonkeyPatch, spec, env, url
) -> None:
    monkeypatch.delenv("ZANTINE_LIVE", raising=False)
    monkeypatch.setenv(env, "key")
    with pytest.raises(ProviderError, match="ZANTINE_LIVE"):
        make_adapter(spec)
    monkeypatch.setenv("ZANTINE_LIVE", "1")
    monkeypatch.delenv(env)
    with pytest.raises(ProviderError, match=env):
        make_adapter(spec)
    monkeypatch.setenv(env, "key")
    adapter = make_adapter(spec)  # builds a client; makes no request
    assert isinstance(adapter.client, openai.OpenAI)
    assert str(adapter.client.base_url).rstrip("/") == url
