"""`zantine probe`: verdicts from raw usage, and each provider's checks on fake clients."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from fake_provider import FakeClient as AnthropicFakeClient
from fake_provider import message as anthropic_message
from fake_provider import tool_use as anthropic_tool_use
from fake_providers import GoogleFake, OpenAIFake, google_reply, openai_reply, xai_reply
from zantine.cli import main
from zantine.harness import probe
from zantine.harness.anthropic_adapter import AnthropicAdapter
from zantine.harness.google_adapter import GoogleAdapter
from zantine.harness.ledger import Ledger
from zantine.harness.meta_adapter import MetaAdapter
from zantine.harness.openai_adapter import OpenAIAdapter
from zantine.harness.pricing import PER_TOKEN, model_spec
from zantine.harness.provider import ProviderResponse, Usage
from zantine.harness.xai_adapter import XAIAdapter

OK, NO, SEEN, UNSURE = probe.CONFIRMED, probe.CONTRADICTED, probe.OBSERVED, probe.INCONCLUSIVE


def usage(inp: int, out: int, total: int, cached: int = 0, reasoning: int | None = 100) -> dict:
    return {
        "input_tokens": inp,
        "input_tokens_details": {"cached_tokens": cached},
        "output_tokens": out,
        "output_tokens_details": {"reasoning_tokens": reasoning},
        "total_tokens": total,
    }


def test_reasoning_accounting_reads_the_total() -> None:
    inside = usage(1000, 300, 1300)
    outside = usage(1000, 300, 1400)
    assert probe.reasoning_accounting(inside, reasoning_in_output=True)[0] == OK
    assert probe.reasoning_accounting(outside, reasoning_in_output=False)[0] == OK
    assert probe.reasoning_accounting(inside, reasoning_in_output=False)[0] == NO
    assert probe.reasoning_accounting(outside, reasoning_in_output=True)[0] == NO
    assert probe.reasoning_accounting(usage(1000, 300, 1999), True)[0] == UNSURE
    assert probe.reasoning_accounting(usage(1000, 300, 1300, reasoning=0), True)[0] == UNSURE


def test_cached_tokens_inside_or_outside_input() -> None:
    assert probe.cached_in_input(usage(1000, 300, 1300, cached=800))[0] == OK
    assert probe.cached_in_input(usage(200, 300, 1300, cached=800))[0] == NO
    assert probe.cached_in_input(usage(1000, 300, 1300, cached=0))[0] == UNSURE


def test_gemini_thoughts_and_cache_accounting() -> None:
    u = {
        "prompt_token_count": 1000,
        "cached_content_token_count": 600,
        "candidates_token_count": 50,
        "thoughts_token_count": 200,
        "total_token_count": 1250,
    }
    assert probe.gemini_thoughts_accounting(u)[0] == OK
    assert probe.gemini_thoughts_accounting(u | {"total_token_count": 1050})[0] == NO
    assert probe.gemini_cached_in_prompt(u)[0] == OK
    assert probe.gemini_cached_in_prompt(u | {"cached_content_token_count": 1200})[0] == NO


def response(output: int, thinking: int, stop: str) -> ProviderResponse:
    return ProviderResponse((), "", stop, Usage(output_tokens=output, thinking_tokens=thinking))


def test_output_cap_scope() -> None:
    # Spent past the cap: the cap did not count reasoning.
    assert probe.output_cap_scope(response(900, 850, "max_tokens"), 256, False)[0] == OK
    assert probe.output_cap_scope(response(900, 850, "max_tokens"), 256, True)[0] == NO
    # Stopped at the cap with reasoning inside it.
    assert probe.output_cap_scope(response(256, 256, "max_tokens"), 256, True)[0] == OK
    assert probe.output_cap_scope(response(120, 100, "tool_use"), 256, True)[0] == UNSURE


def test_xai_bill_against_our_prices() -> None:
    assert probe.provider_cost_match(0.0100, 0.0101, 0.008)[0] == OK
    assert probe.provider_cost_match(0.0100, 0.0080, 0.008)[0] == NO
    assert probe.provider_cost_match(0.0100, 0.0300, 0.008)[0] == UNSURE
    assert probe.provider_cost_match(0.0100, None, 0.008)[0] == UNSURE


def by_check(findings: list[probe.Finding]) -> dict[str, probe.Finding]:
    return {f.check: f for f in findings}


def test_openai_probe_runs_every_responses_api_check() -> None:
    def respond(params: dict[str, Any]) -> Any:
        if "Probe instruction" in params["input"][0]["content"][-1]["text"]:
            reply = openai_reply(("wait", {}), ("move", {"room": "Medbay"}))
        else:
            reply = openai_reply(("wait", {}))
        if "reasoning" not in params:
            reply.reasoning = SimpleNamespace(effort="medium")
        return reply

    fake = OpenAIFake(respond)
    ledger = Ledger(2)
    findings = by_check(probe.probe_model(OpenAIAdapter(model_spec("gpt-6.1-sol"), fake), ledger))
    assert findings["serves_request"].verdict == OK
    assert findings["reasoning_accounting"].verdict == OK
    assert findings["cache_read"].verdict == OK
    assert findings["cached_in_input"].verdict == OK
    assert findings["default_effort"].verdict == OK  # Sol's documented default is medium
    assert findings["parallel_tool_calls"].verdict == NO  # two calls despite the flag
    # Every call was metered, and each variant changed only what it tests.
    calls = fake.responses.calls
    assert len(ledger.records) == len(calls) == 5
    assert {r.purpose for r in ledger.records} == {"probe"}
    assert [c["max_output_tokens"] for c in calls].count(probe.CAP_PROBE_TOKENS) == 1
    assert sum("reasoning" not in c for c in calls) == 1
    assert all(c["parallel_tool_calls"] is False and c["store"] is False for c in calls)


def test_xai_probe_checks_reasoning_on_top_and_the_bill() -> None:
    grok = model_spec("grok-4.7")

    def respond(params: dict[str, Any]) -> Any:
        reply = xai_reply(("wait", {}), input_tokens=6000, cached=5000, output_tokens=40)
        u = reply.usage
        u.total_tokens = u.input_tokens + u.output_tokens + u.output_tokens_details.reasoning_tokens
        cost = PER_TOKEN * (1000 * grok.input + 5000 * grok.cached_input + 1540 * grok.output)
        u.cost_in_usd_ticks = round(cost * 1e10)
        return reply

    findings = by_check(probe.probe_model(XAIAdapter(grok, OpenAIFake(respond)), Ledger(2)))
    assert findings["reasoning_accounting"].verdict == OK
    assert findings["xai_billed_cost"].verdict == OK
    assert findings["cached_in_input"].verdict == OK
    assert findings["output_cap_scope"].assumption.endswith("excludes reasoning tokens")
    assert findings["default_effort"].verdict == UNSURE  # the fake echoes no effort


def test_meta_probe_records_what_meta_leaves_undocumented() -> None:
    def respond(params: dict[str, Any]) -> Any:
        reply = openai_reply(("wait", {}))
        if "reasoning" not in params:
            reply.reasoning = SimpleNamespace(effort="medium")
        return reply

    adapter = MetaAdapter(model_spec("muse-wonder-1.0"), OpenAIFake(respond))
    findings = by_check(probe.probe_model(adapter, Ledger(2)))
    assert findings["serves_request"].assumption.startswith("Muse Wonder is served")
    assert findings["default_effort"].verdict == SEEN
    assert findings["cache_write_field"].verdict == SEEN


def test_google_probe_tries_validated_mode() -> None:
    fake = GoogleFake(lambda p: google_reply(("wait", {})))
    findings = by_check(
        probe.probe_model(GoogleAdapter(model_spec("gemini-4-argon"), fake), Ledger(2))
    )
    assert findings["serves_request"].verdict == OK
    assert findings["thoughts_accounting"].verdict == OK
    assert findings["cached_in_prompt"].verdict == OK
    assert findings["validated_mode"].verdict == OK
    modes = [
        c["config"]["tool_config"]["function_calling_config"]["mode"] for c in fake.models.calls
    ]
    assert modes.count("VALIDATED") == 1 and modes.count("AUTO") == len(modes) - 1


def test_anthropic_probe_checks_serving_and_cache() -> None:
    def respond(params: dict[str, Any]) -> Any:
        return anthropic_message(anthropic_tool_use("wait"))

    adapter = AnthropicAdapter(model_spec("claude-opus-5-5"), AnthropicFakeClient(respond))
    findings = by_check(probe.probe_model(adapter, Ledger(2)))
    assert set(findings) == {"serves_request", "cache_read"}


def test_a_failed_first_call_stops_that_model() -> None:
    import openai

    def refuse(params: dict[str, Any]) -> Any:
        return openai.NotFoundError("no such model", response=_http(404), body=None)

    findings = probe.probe_model(
        MetaAdapter(model_spec("muse-wonder-1.0"), OpenAIFake(refuse)), Ledger(2)
    )
    assert [(f.check, f.verdict) for f in findings] == [("serves_request", probe.FAILED)]


def _http(status: int) -> Any:
    import httpx

    return httpx.Response(status, request=httpx.Request("POST", "https://example.test"))


def test_probe_needs_the_live_flag_and_env(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("ZANTINE_LIVE", raising=False)
    with pytest.raises(SystemExit):
        main(["probe", "--models", "grok-4.7"])
    assert "--live" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["probe", "--models", "grok-4.7", "--live"])
    assert "ZANTINE_LIVE=1" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["probe", "--models", "nope", "--live"])
