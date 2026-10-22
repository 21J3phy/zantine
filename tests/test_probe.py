"""`zantine probe`: verdicts from raw usage, and each provider's checks on fake clients."""

from __future__ import annotations

from dataclasses import replace
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


def answers(acc: probe.Accounting) -> tuple[bool | None, bool | None]:
    return acc.hidden_inside, acc.cache_inside


def test_reasoning_accounting_reads_the_total() -> None:
    acc = probe.responses_accounting
    assert answers(acc(usage(1000, 300, 1300))) == (True, None)  # nothing cached: no answer
    assert answers(acc(usage(1000, 300, 1400))) == (False, None)
    assert answers(acc(usage(1000, 300, 1999))) == (None, None)  # no reading fits
    assert answers(acc(usage(1000, 300, 1300, reasoning=0))) == (None, None)
    assert acc(usage(1000, 300, 1400)).generated == 400
    assert acc(usage(1000, 300, 1300)).generated == 300


def test_cache_and_reasoning_are_decided_together() -> None:
    acc = probe.responses_accounting
    assert answers(acc(usage(1000, 300, 1300, cached=800))) == (True, True)
    assert answers(acc(usage(200, 300, 1300, cached=800))) == (True, False)
    # 1000 + 300 + 100 fits cache-inside/reasoning-on-top and cache-outside/reasoning-inside.
    ambiguous = usage(1000, 300, 1400, cached=100, reasoning=100)
    assert answers(acc(ambiguous)) == (None, None)
    assert acc(ambiguous).generated is None
    # Knowing one answer (from another call) settles the other.
    assert answers(acc(ambiguous, hidden_inside=False)) == (False, True)
    assert answers(acc(ambiguous, cache_inside=False)) == (True, False)


def test_gemini_thoughts_and_cache_accounting() -> None:
    u = {
        "prompt_token_count": 1000,
        "cached_content_token_count": 600,
        "candidates_token_count": 50,
        "thoughts_token_count": 200,
        "total_token_count": 1250,
    }
    acc = probe.gemini_accounting
    assert answers(acc(u)) == (False, True)
    assert answers(acc(u | {"total_token_count": 1050})) == (True, True)
    # Cached tokens on top of the prompt count: 1000 + 600 + 50 + 200.
    assert answers(acc(u | {"total_token_count": 1850})) == (False, False)
    assert answers(acc(u | {"total_token_count": 1251})) == (None, None)


def test_judge() -> None:
    assert probe.judge(True, True) == OK
    assert probe.judge(False, True) == NO
    assert probe.judge(None, True) == UNSURE


def test_output_cap_scope_counts_from_resolved_raw_usage() -> None:
    cap = 256
    on_top = probe.responses_accounting(usage(5000, 256, 5456, reasoning=200))
    # Visible output hit the cap and reasoning came on top: the cap excludes reasoning.
    assert probe.output_cap_scope(on_top, "max_tokens", cap, False)[0] == OK
    assert probe.output_cap_scope(on_top, "max_tokens", cap, True)[0] == NO
    # Reasoning inside output (total = input + output): 256 generated, stopped at the cap.
    inside = probe.responses_accounting(usage(5000, 256, 5256, reasoning=200))
    assert probe.output_cap_scope(inside, "max_tokens", cap, True)[0] == OK
    assert probe.output_cap_scope(inside, "max_tokens", cap, False)[0] == NO
    # The reviewer's case: xAI actually counting reasoning inside must not confirm "excludes".
    assert probe.output_cap_scope(inside, "max_tokens", cap, False)[0] != OK
    unresolved = probe.responses_accounting(usage(5000, 256, 9999, reasoning=200))
    assert probe.output_cap_scope(unresolved, "max_tokens", cap, False)[0] == UNSURE
    under = probe.responses_accounting(usage(5000, 120, 5120, reasoning=100))
    assert probe.output_cap_scope(under, "tool_use", cap, True)[0] == UNSURE


def test_xai_bill_against_our_prices() -> None:
    assert probe.provider_cost_match(0.0100, 0.0101, 0.008)[0] == OK
    assert probe.provider_cost_match(0.0100, 0.0080, 0.008)[0] == NO
    # A trustworthy bill neither reading reproduces disproves the assumption.
    assert probe.provider_cost_match(0.0100, 0.0300, 0.008)[0] == NO
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


def test_parallel_flag_needs_a_positive_control() -> None:
    """One call with the flag off proves nothing unless the model makes two when allowed."""

    def obeys_flag(params: dict[str, Any]) -> Any:
        two = params["parallel_tool_calls"] and "Probe instruction" in str(params["input"])
        return (
            openai_reply(("wait", {}), ("move", {"room": "Medbay"}))
            if two
            else openai_reply(("wait", {}))
        )

    def always_one(params: dict[str, Any]) -> Any:
        return openai_reply(("wait", {}))

    sol = model_spec("gpt-6.1-sol")
    held = by_check(probe.probe_model(OpenAIAdapter(sol, OpenAIFake(obeys_flag)), Ledger(2)))
    assert held["parallel_tool_calls"].verdict == OK
    one = by_check(probe.probe_model(OpenAIAdapter(sol, OpenAIFake(always_one)), Ledger(2)))
    assert one["parallel_tool_calls"].verdict == UNSURE


def test_a_call_lost_in_transit_keeps_its_worst_case_against_the_cap() -> None:
    import httpx
    import openai

    def first_then_timeouts(params: dict[str, Any]) -> Any:
        if fake.responses.calls[:-1]:
            return openai.APITimeoutError(request=httpx.Request("POST", "https://example.test"))
        return openai_reply(("wait", {}))

    fake = OpenAIFake(first_then_timeouts)
    ledger = Ledger(5)
    findings = probe.probe_model(OpenAIAdapter(model_spec("gpt-6.1-sol"), fake), ledger)
    failed = [f for f in findings if f.verdict == probe.FAILED]
    assert len(failed) == len(fake.responses.calls) - 1 >= 3
    billed = sum(r.cost_usd for r in ledger.records)
    # Each lost call holds its own worst case; the smallest is the 256-token cap probe's.
    size = probe.probe_request().text_size()
    least = replace(model_spec("gpt-6.1-sol"), max_output_tokens=probe.CAP_PROBE_TOKENS)
    assert ledger.spent_usd - billed >= len(failed) * least.worst_case_cost(size)


def test_reaching_the_spend_cap_keeps_what_was_found() -> None:
    sol = model_spec("gpt-6.1-sol")
    needed = sol.worst_case_cost(probe.probe_request().text_size())
    ledger = Ledger(needed * 1.01)  # room for the first call only
    findings = probe.probe_model(OpenAIAdapter(sol, OpenAIFake()), ledger)
    assert [(f.check, f.verdict) for f in findings] == [
        ("serves_request", OK),
        ("spend_cap", probe.FAILED),
    ]
