"""Pricing config, the cost ledger and the spend cap."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from fake_provider import FakeClient, message, tool_use
from zantine.harness.anthropic_adapter import AnthropicAdapter
from zantine.harness.ledger import Ledger, SpendCapReached, summarize
from zantine.harness.pricing import PER_TOKEN, PriceTier, load_models, model_spec
from zantine.harness.provider import HarnessRequest, ProviderError, Usage

POOL = {
    # id: (input, cached input, output) from docs/model-pool.md
    "claude-fable-5-1": (10.0, 0.25, 50.0),
    "claude-opus-5-5": (4.0, 0.20, 20.0),
    "gpt-6-astra": (10.0, None, 50.0),
    "gpt-6.1-sol": (2.0, None, 10.0),
    "gemini-4-argon": (2.0, 0.10, 10.0),
    "grok-4.7": (2.0, 0.50, 6.0),
    "muse-wonder-1.0": (4.0, 0.20, 20.0),
}
REQUEST = HarnessRequest(system="RULES", tools=(), memory=(), tail="NOW")


def test_config_lists_the_whole_model_pool_at_medium_effort() -> None:
    models = load_models()
    assert set(models) == set(POOL)
    for mid, (inp, cached, out) in POOL.items():
        m = models[mid]
        assert (m.input, m.output, m.effort) == (inp, out, "medium")
        if cached is not None:
            assert m.cached_input == cached
        assert m.cache_write >= m.input
    assert models["claude-fable-5-1"].provider == "anthropic"
    assert models["claude-fable-5-1"].cache_write == 12.5


def test_cost_prices_every_token_kind() -> None:
    fable = model_spec("claude-fable-5-1")
    usage = Usage(
        input_tokens=1_000_000,
        cached_input_tokens=2_000_000,
        cache_write_tokens=1_000_000,
        output_tokens=100_000,
        thinking_tokens=80_000,
    )
    # 10 + 2 * 0.25 + 12.5 + 0.1 * 50 (thinking is already inside output)
    assert fable.cost(usage) == pytest.approx(28.0)


def test_expired_prices_are_refused() -> None:
    argon = model_spec("gemini-4-argon")
    argon.check_price_current(dt.date(2026, 11, 30))
    with pytest.raises(ValueError, match="expired"):
        argon.check_price_current(dt.date(2026, 12, 1))


def adapter(**usage: int) -> AnthropicAdapter:
    return AnthropicAdapter(
        model_spec("claude-fable-5-1"), FakeClient(lambda p: message(tool_use("wait"), **usage))
    )


def test_ledger_records_tokens_and_cost_of_every_call() -> None:
    seen = []
    ledger = Ledger(10.0, sink=seen.append)
    a = adapter(
        input_tokens=1000, cache_read=0, cache_write=0, output_tokens=1000, thinking_tokens=800
    )
    ledger.send(a, REQUEST, purpose="seat", phase="action", seat=3, round=1)
    ledger.send(a, REQUEST, purpose="extractor", phase="claims")
    assert seen == ledger.records
    first = ledger.records[0]
    assert (first.seat, first.round, first.phase, first.purpose) == (3, 1, "action", "seat")
    assert first.cost_usd == pytest.approx(0.06)
    assert first.thinking_tokens == 800
    assert ledger.spent_usd == pytest.approx(0.12)
    total = summarize(ledger.records)
    assert total["calls"] == 2 and total["output_tokens"] == 2000
    assert total["cost_usd"] == pytest.approx(0.12)


def test_spend_cap_refuses_a_call_that_could_overshoot() -> None:
    a = adapter(input_tokens=0, cache_read=0, cache_write=0, output_tokens=16000)
    worst = a.model.worst_case_cost(REQUEST.text_size())
    assert worst > 0.8  # full output cap at $50/M, plus input
    ledger = Ledger(worst * 2.5)
    ledger.send(a, REQUEST, purpose="seat", phase="action")
    ledger.send(a, REQUEST, purpose="seat", phase="action")
    with pytest.raises(SpendCapReached):
        ledger.send(a, REQUEST, purpose="seat", phase="action")
    assert len(ledger.records) == 2
    assert ledger.spent_usd <= ledger.spend_cap_usd
    assert len(a.client.messages.calls) == 2


def test_failed_calls_are_recorded_at_zero_cost() -> None:
    def fail(params: dict) -> Exception:
        return ProviderError("overloaded", fatal=False, status=529)

    a = AnthropicAdapter(model_spec("claude-fable-5-1"), FakeClient(fail))
    ledger = Ledger(5.0)
    with pytest.raises(ProviderError):
        ledger.send(a, REQUEST, purpose="seat", phase="vote", seat=1)
    (record,) = ledger.records
    assert not record.ok and record.cost_usd == 0 and "overloaded" in record.error


def test_spend_cap_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Ledger(0)


def test_worst_case_grows_with_request_size() -> None:
    fable = model_spec("claude-fable-5-1")
    assert fable.worst_case_cost(30_000) > fable.worst_case_cost(3_000)
    cheap = replace(fable, max_output_tokens=0)
    assert cheap.worst_case_cost(3_000_000) == pytest.approx(12.5)


def test_effort_is_a_per_run_level_mapped_to_each_labs_setting() -> None:
    for spec in load_models().values():
        assert spec.effort == "medium"
        assert set(spec.efforts) >= {"low", "medium", "high"}
        for level in ("low", "medium", "high"):
            assert spec.at_effort(level).effort_setting == spec.efforts[level]
    with pytest.raises(ValueError, match="effort"):
        model_spec("claude-fable-5-1").at_effort("turbo")


def test_long_context_tier_reprices_the_whole_call() -> None:
    tier = PriceTier(
        above_input_tokens=1000, input=4.0, cached_input=1.0, cache_write=4.0, output=12.0
    )
    spec = replace(model_spec("grok-4.7"), long_context=tier)
    short = Usage(input_tokens=600, cached_input_tokens=400, output_tokens=1_000_000)
    long = Usage(input_tokens=600, cached_input_tokens=401, output_tokens=1_000_000)
    assert spec.cost(short) == pytest.approx(spec.output + PER_TOKEN * (600 * 2.0 + 400 * 0.5))
    assert spec.cost(long) == pytest.approx(12.0 + PER_TOKEN * (600 * 4.0 + 401 * 1.0))
    assert spec.worst_case_cost(3 * 2000) > replace(spec, long_context=None).worst_case_cost(6000)
