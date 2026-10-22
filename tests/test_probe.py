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


def readings(u: dict, gemini: bool = False) -> tuple[bool | None, bool | None]:
    counts = probe.gemini_counts(u) if gemini else probe.responses_counts(u)
    return probe.decide(counts.fits())


def test_reasoning_accounting_reads_the_total() -> None:
    assert readings(usage(1000, 300, 1300)) == (True, None)  # nothing cached: no answer
    assert readings(usage(1000, 300, 1400)) == (False, None)
    assert readings(usage(1000, 300, 1999)) == (None, None)  # no reading fits
    assert readings(usage(1000, 300, 1300, reasoning=0)) == (None, None)


def test_cache_and_reasoning_are_decided_together() -> None:
    assert readings(usage(1000, 300, 1300, cached=800)) == (True, True)
    assert readings(usage(200, 300, 1300, cached=800)) == (True, False)
    # 1000 + 300 + 100 fits cache-inside/reasoning-on-top and cache-outside/reasoning-inside.
    ambiguous = probe.responses_counts(usage(1000, 300, 1400, cached=100, reasoning=100))
    assert probe.decide(ambiguous.fits()) == (None, None)
    assert probe.generated(ambiguous, ambiguous.fits()) is None
    # A second call that settles one question settles the other too.
    other = probe.responses_counts(usage(1000, 300, 1300))  # reasoning inside
    assert probe.decide(ambiguous.fits() & other.fits()) == (True, False)


def test_gemini_thoughts_and_cache_accounting() -> None:
    u = {
        "prompt_token_count": 1000,
        "cached_content_token_count": 600,
        "candidates_token_count": 50,
        "thoughts_token_count": 200,
        "total_token_count": 1250,
    }
    assert readings(u, gemini=True) == (False, True)
    # Thoughts inside candidates (which must then be at least as large).
    inside = u | {"candidates_token_count": 250, "total_token_count": 1250}
    assert readings(inside, gemini=True) == (True, True)
    # Cached tokens on top of the prompt count: 1000 + 600 + 50 + 200.
    assert readings(u | {"total_token_count": 1850}, gemini=True) == (False, False)
    assert readings(u | {"total_token_count": 1251}, gemini=True) == (None, None)


def test_judge() -> None:
    assert probe.judge(True, True) == OK
    assert probe.judge(False, True) == NO
    assert probe.judge(None, True) == UNSURE


def split(u: dict) -> tuple[int, int] | None:
    counts = probe.responses_counts(u)
    return probe.generated(counts, counts.fits())


def test_output_cap_scope_counts_from_resolved_raw_usage() -> None:
    cap = 256
    on_top = split(usage(5000, 256, 5456, reasoning=200))
    assert on_top == (256, 200)
    # Visible output hit the cap and reasoning came on top: the cap excludes reasoning.
    assert probe.output_cap_scope(on_top, "max_tokens", cap, False)[0] == OK
    assert probe.output_cap_scope(on_top, "max_tokens", cap, True)[0] == NO
    # Reasoning inside output (total = input + output): 256 generated, stopped at the cap.
    inside = split(usage(5000, 256, 5256, reasoning=200))
    assert inside == (56, 200)
    assert probe.output_cap_scope(inside, "max_tokens", cap, True)[0] == OK
    # The reviewer's case: xAI actually counting reasoning inside must not confirm "excludes".
    assert probe.output_cap_scope(inside, "max_tokens", cap, False)[0] == NO
    assert (
        probe.output_cap_scope(split(usage(5000, 256, 9999, reasoning=200)), "x", cap, False)[0]
        == UNSURE
    )
    under = split(usage(5000, 120, 5120, reasoning=100))
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


def test_probe_calls_never_use_the_sdks_own_retries() -> None:
    """A real SDK client retries timeouts twice by default: three billed requests
    behind one metered call. The probe turns retries off, so each request is metered."""
    import httpx2
    import openai

    sent: list[str] = []

    def timeout(request: httpx2.Request) -> httpx2.Response:
        sent.append(str(request.url))
        raise httpx2.ReadTimeout("slow", request=request)

    client = openai.OpenAI(
        api_key="sk-test", http_client=httpx2.Client(transport=httpx2.MockTransport(timeout))
    )
    ledger = Ledger(5)
    findings = probe.probe_model(OpenAIAdapter(model_spec("gpt-6.1-sol"), client), ledger)
    assert [(f.check, f.verdict) for f in findings] == [("serves_request", probe.FAILED)]
    assert len(sent) == 1  # no hidden retries
    assert ledger.spent_usd > 0  # the lost call's worst case is held


def test_the_cap_probe_reserves_the_models_full_cap() -> None:
    """Only the cap sent shrinks: a lab whose cap leaves out reasoning can bill more."""
    grok = model_spec("grok-4.7")
    fake = OpenAIFake(reply=xai_reply)
    adapter, _ = probe._variant(XAIAdapter(grok, fake), max_output_tokens=probe.CAP_PROBE_TOKENS)
    assert adapter.model == grok
    assert adapter.build_params(probe.probe_request())["max_output_tokens"] == 256


def test_calls_that_disagree_decide_nothing() -> None:
    """Total 1300 says reasoning inside; with 800 cached, total 1400 says on top. The
    readings both calls admit are intersected, so neither order confirms anything."""
    a = probe.responses_counts(usage(1000, 300, 1300))
    b = probe.responses_counts(usage(1000, 300, 1400, cached=800))
    assert not a.fits() & b.fits()
    assert probe.decide(a.fits() & b.fits()) == (None, None)


def test_a_missing_counter_is_unknown_not_zero() -> None:
    # Reasoning not reported: total 1400 also fits cache inside with 100 reasoning on top.
    u = usage(1000, 300, 1400, cached=100, reasoning=None)
    del u["output_tokens_details"]
    assert probe.decide(probe.responses_counts(u).fits()) == (None, None)
    g = {"prompt_token_count": 1000, "candidates_token_count": 50, "total_token_count": 1250}
    assert probe.decide(probe.gemini_counts(g).fits())[0] is None


def test_visible_output_over_the_cap_proves_nothing_about_reasoning() -> None:
    assert probe.output_cap_scope((512, 0), "max_tokens", 256, False)[0] == UNSURE
    assert probe.output_cap_scope((300, 200), "max_tokens", 256, False)[0] == UNSURE


def test_a_part_larger_than_its_container_is_no_reading() -> None:
    # 200 reasoning tokens can't sit inside 50 output tokens, nor 800 cached inside 500 input.
    reasoning = probe.responses_counts(usage(1000, 50, 1050, cached=800, reasoning=200))
    assert reasoning.fits() == frozenset()  # 1050 = 1000 + 50 only with the impossible split
    cached = probe.responses_counts(usage(500, 50, 550, cached=800, reasoning=0))
    assert all(not c for _, c in cached.fits())


def test_an_xai_bill_above_our_prices_counts_against_the_cap_and_stops_the_probe() -> None:
    grok = model_spec("grok-4.7")

    def respond(params: dict[str, Any]) -> Any:
        reply = xai_reply(("wait", {}), input_tokens=6000, cached=5000, output_tokens=40)
        u = reply.usage
        u.total_tokens = u.input_tokens + u.output_tokens + u.output_tokens_details.reasoning_tokens
        u.cost_in_usd_ticks = 800_000_000  # $0.08, far above our prices
        return reply

    fake = OpenAIFake(respond)
    ledger = Ledger(2)
    findings = by_check(probe.probe_model(XAIAdapter(grok, fake), ledger))
    assert findings["xai_billed_cost"].verdict == NO
    assert findings["stopped"].verdict == probe.FAILED
    assert len(fake.responses.calls) == 1  # no call after the bill disproved our prices
    assert ledger.spent_usd == pytest.approx(0.08)


def test_a_model_listed_twice_is_refused(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("ZANTINE_LIVE", "1")
    with pytest.raises(SystemExit):
        main(["probe", "--models", "gpt-6.1-sol,gpt-6.1-sol", "--live"])
    assert "listed twice" in capsys.readouterr().err


def test_tokens_the_total_shows_but_no_counter_names_still_count_against_the_cap() -> None:
    """Gemini leaving out thoughts_token_count must not make thinking free."""
    argon = model_spec("gemini-4-argon")

    def respond(params: dict[str, Any]) -> Any:
        reply = google_reply(("wait", {}), thoughts=None)
        reply.usage_metadata.total_token_count += 14_000  # thoughts the reply didn't name
        return reply

    ledger = Ledger(5)
    probe.probe_model(GoogleAdapter(argon, GoogleFake(respond)), ledger)
    recorded = sum(r.cost_usd for r in ledger.records)
    unpriced = len(ledger.records) * 14_000 * PER_TOKEN * argon.output
    assert ledger.spent_usd == pytest.approx(recorded + unpriced)


def test_any_xai_bill_our_prices_cant_reproduce_stops_the_probe() -> None:
    """Not only the first two calls: a later mismatch stops it too."""
    grok = model_spec("grok-4.7")
    fake: OpenAIFake

    def respond(params: dict[str, Any]) -> Any:
        reply = xai_reply(("wait", {}), input_tokens=6000, cached=5000, output_tokens=40)
        u = reply.usage
        u.total_tokens = u.input_tokens + u.output_tokens + u.output_tokens_details.reasoning_tokens
        cost = PER_TOKEN * (1000 * grok.input + 5000 * grok.cached_input + 1540 * grok.output)
        late = len(fake.responses.calls) > 2
        u.cost_in_usd_ticks = round((0.20 if late else cost) * 1e10)
        return reply

    fake = OpenAIFake(respond)
    ledger = Ledger(5)
    findings = probe.probe_model(XAIAdapter(grok, fake), ledger)
    assert len(fake.responses.calls) == 3
    assert [f.check for f in findings][-2:] == ["xai_billed_cost", "stopped"]
    assert findings[-2].verdict == NO and findings[-2].detail.startswith("output_cap")
    assert ledger.spent_usd >= 0.20


def test_a_cut_off_flag_off_reply_confirms_nothing() -> None:
    def respond(params: dict[str, Any]) -> Any:
        if "Probe instruction" not in str(params["input"]):
            return openai_reply(("wait", {}))
        if params["parallel_tool_calls"]:
            return openai_reply(("wait", {}), ("move", {"room": "Medbay"}))
        return openai_reply(status="incomplete", incomplete="max_output_tokens")

    fake = OpenAIFake(respond)
    findings = by_check(
        probe.probe_model(OpenAIAdapter(model_spec("gpt-6.1-sol"), fake), Ledger(5))
    )
    assert findings["parallel_tool_calls"].verdict == UNSURE
    assert not any(c["parallel_tool_calls"] for c in fake.responses.calls)  # no control sent
