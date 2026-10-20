"""Live smoke test: one short game per pool Model, each with one real Seat and 9 bots.

Costs real money. Runs only with ``ZANTINE_LIVE=1 uv run pytest --live``; each
Model is skipped unless its lab's key is set (``ANTHROPIC_API_KEY``,
``OPENAI_API_KEY``, ``GEMINI_API_KEY``, ``XAI_API_KEY``, ``META_API_KEY``).
Each game is capped at $2. Pick one Model with ``-k``, e.g. ``-k grok``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from zantine.cli import main
from zantine.harness.anthropic_adapter import AnthropicAdapter
from zantine.harness.google_adapter import GoogleAdapter
from zantine.harness.meta_adapter import MetaAdapter
from zantine.harness.openai_adapter import OpenAIAdapter
from zantine.harness.pricing import load_models
from zantine.harness.xai_adapter import XAIAdapter
from zantine.transcript import Transcript

POOL = load_models()
ADAPTERS = {
    a.provider: a for a in (AnthropicAdapter, OpenAIAdapter, GoogleAdapter, XAIAdapter, MetaAdapter)
}


@pytest.mark.live
@pytest.mark.parametrize("model_id", sorted(POOL))
def test_model_seat_plays_a_short_game(model_id: str, tmp_path: Path) -> None:
    key_env = ADAPTERS[POOL[model_id].provider].key_env
    if not os.environ.get(key_env):
        pytest.skip(f"{key_env} is not set")
    out = tmp_path / "smoke.jsonl"
    argv = ["play", "--seed", "1", "--round-cap", "2", "--quiet", "--live", "--spend-cap", "2"]
    main([*argv, "--bots", f"live:{model_id},scripted*9", "--out", str(out)])
    records = Transcript.read(out)
    calls = [r for r in records if r["kind"] == "event" and r["type"] == "harness.call"]
    assert calls and all(c["data"]["ok"] for c in calls)
    assert all(c["data"]["model"] == model_id for c in calls)
    cost = records[-1]
    assert cost["kind"] == "cost" and cost["complete"]
    seat = next(s for s in cost["seats"] if s["controller"]["kind"] == "live")
    assert seat["controller"]["effort_setting"] == "medium" and seat["cost_usd"] > 0
    # Every lab's API reports thinking tokens for these Models (0 is fine on easy turns).
    assert all(c["data"]["thinking_tokens"] is not None for c in calls)
