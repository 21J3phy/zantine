"""The whole Model Pool: every Model can fill a Seat, at its lab's medium, with per-Model costs."""

from __future__ import annotations

import pytest

from fake_providers import calls_made, fake_adapter
from zantine.config import GameConfig
from zantine.harness.live import play_live
from zantine.harness.pricing import EFFORT_LEVELS, load_models
from zantine.harness.provider import make_adapter

POOL = load_models()
FLAGSHIPS = {"claude-fable-5-1", "gpt-6-astra", "gemini-4-argon", "grok-4.7", "muse-wonder-1.0"}
SIBLINGS = {"claude-opus-5-5", "gpt-6.1-sol"}


def test_pool_is_five_flagships_and_two_siblings_one_provider_each() -> None:
    assert set(POOL) == FLAGSHIPS | SIBLINGS
    assert len({POOL[m].lab for m in FLAGSHIPS}) == 5
    assert {POOL[m].provider for m in POOL} == {"anthropic", "openai", "google", "xai", "meta"}


@pytest.mark.parametrize("model_id", sorted(POOL))
def test_every_model_has_a_registered_adapter(model_id: str, monkeypatch) -> None:
    monkeypatch.delenv("ZANTINE_LIVE", raising=False)
    # The real factory exists and refuses without the live opt-in (no client is built).
    with pytest.raises(Exception, match="ZANTINE_LIVE"):
        make_adapter(POOL[model_id])


@pytest.mark.parametrize("model_id", sorted(POOL))
def test_every_model_runs_at_its_labs_medium_and_maps_each_level(model_id: str) -> None:
    spec = POOL[model_id]
    assert spec.effort == "medium" and spec.effort_setting == "medium"
    assert set(EFFORT_LEVELS) <= set(spec.efforts)
    spec.check_price_current()


def test_all_seven_models_fill_seats_in_one_game() -> None:
    lineup = ",".join(f"live:{m}" for m in sorted(POOL)) + ",scripted*3"
    adapters = {}

    def factory(spec):
        adapters[spec.id] = fake_adapter(spec)
        return adapters[spec.id]

    run = play_live(
        GameConfig(seed=3, round_cap=4), lineup, spend_cap_usd=50, adapter_factory=factory
    )
    assert run.aborted is None
    header = run.game.transcript.records[0]
    live = [s["controller"] for s in header["seats"] if s["controller"]["kind"] == "live"]
    assert sorted(c["model"] for c in live) == sorted(POOL)
    assert all(c["effort_setting"] == "medium" for c in live)
    assert all(calls_made(a) > 0 for a in adapters.values())

    calls = [r["data"] for r in run.game.transcript.events("harness.call")]
    assert {c["model"] for c in calls} == set(POOL)
    assert all(c["ok"] and c["thinking_tokens"] == 1500 and c["cost_usd"] > 0 for c in calls)
    # xAI's own charge is logged next to ours; other providers don't report one.
    assert all((c["provider_cost_usd"] is not None) == (c["model"] == "grok-4.7") for c in calls)

    # The cost record has per-Model totals: provider, effort, thinking tokens, dollars.
    models = run.cost["models"]
    assert set(models) == set(POOL)
    for model_id, m in models.items():
        mine = [c for c in calls if c["model"] == model_id]
        assert m["provider"] == POOL[model_id].provider and m["effort_setting"] == "medium"
        assert m["thinking_tokens"] == 1500 * len(mine)
        assert m["cost_usd"] == pytest.approx(sum(c["cost_usd"] for c in mine), abs=1e-5)
    total = sum(m["cost_usd"] for m in models.values())
    assert run.cost["game"]["cost_usd"] == pytest.approx(total, abs=1e-5)


def test_effort_override_is_mapped_per_lab_for_every_seat() -> None:
    lineup = ",".join(f"live:{m}" for m in sorted(POOL)) + ",scripted*3"
    run = play_live(
        GameConfig(seed=3, round_cap=1),
        lineup,
        spend_cap_usd=50,
        adapter_factory=fake_adapter,
        effort="high",
    )
    live = [
        s["controller"]
        for s in run.game.transcript.records[0]["seats"]
        if s["controller"]["kind"] == "live"
    ]
    for c in live:
        assert c["effort"] == "high" and c["effort_setting"] == POOL[c["model"]].efforts["high"]
