"""Live smoke test: one short game with a real Claude Fable 5.1 Seat.

Costs real money. Runs only with ``ZANTINE_LIVE=1 uv run pytest --live`` and
``ANTHROPIC_API_KEY`` set; capped at $2.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zantine.cli import main
from zantine.transcript import Transcript


@pytest.mark.live
def test_fable_seat_plays_two_rounds(tmp_path: Path) -> None:
    out = tmp_path / "smoke.jsonl"
    argv = ["play", "--seed", "1", "--round-cap", "2", "--quiet", "--live", "--spend-cap", "2"]
    main([*argv, "--bots", "live:claude-fable-5-1,scripted*9", "--out", str(out)])
    records = Transcript.read(out)
    calls = [r for r in records if r["kind"] == "event" and r["type"] == "harness.call"]
    assert calls and all(c["data"]["ok"] for c in calls)
    assert records[-1]["kind"] == "cost" and records[-1]["seats"][0]["cost_usd"] > 0
