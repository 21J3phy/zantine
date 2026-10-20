"""Dry-run Seats: a scripted bot standing in for a scheduled Model, at $0.

``dry:<model-id>`` plays exactly like ``scripted`` but carries the Model it
stands in for, so a dry run of a schedule (``zantine run --dry-run``) produces
transcripts that analysis can group by Model. The label is ground truth: it is
written only to the transcript header via ``describe()``, never to a context or
observation (Hidden Identity).
"""

from __future__ import annotations

from typing import Any

from zantine.rng import GameRng
from zantine.seats.base import SeatController
from zantine.seats.bots import ScriptedBot
from zantine.seats.registry import register_controller


class DryRunSeat(ScriptedBot):
    kind = "dry"

    def __init__(self, rng: GameRng, model: str) -> None:
        super().__init__(rng)
        self.model = model

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "model": self.model, "plays_as": ScriptedBot.kind}


@register_controller("dry")
def _dry_factory(rng: GameRng, arg: str | None) -> SeatController:
    if not arg:
        raise ValueError("dry seats need a model label, e.g. dry:claude-fable-5-1")
    return DryRunSeat(rng, arg)
