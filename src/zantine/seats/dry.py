"""Dry-run Seats: a scripted bot standing in for a scheduled Model, at $0.

``dry:<model-id>`` plays exactly like ``scripted`` but carries the Model it
stands in for, so a dry run of a schedule (``zantine run --dry-run``) produces
transcripts that analysis can group by Model. ``dry:<model-id>?effort=<level>``
also carries the effort level (and, for a Model in ``models.toml``, the lab's
own setting for it), as a live Seat would, so an effort sweep can be dry-run.
The labels are ground truth: they are written only to the transcript header via
``describe()``, never to a context or observation (Hidden Identity).
"""

from __future__ import annotations

from typing import Any

from zantine.harness.pricing import load_models, split_effort
from zantine.rng import GameRng
from zantine.seats.base import SeatController
from zantine.seats.bots import SCRIPTED_BOT_VERSION, ScriptedBot
from zantine.seats.registry import register_controller


class DryRunSeat(ScriptedBot):
    kind = "dry"

    def __init__(
        self,
        rng: GameRng,
        model: str,
        effort: str | None = None,
        effort_setting: str | None = None,
    ) -> None:
        super().__init__(rng)
        self.model = model
        self.effort = effort
        self.effort_setting = effort_setting

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "model": self.model}
        if self.effort is not None:
            out |= {"effort": self.effort, "effort_setting": self.effort_setting}
        return out | {"plays_as": ScriptedBot.kind, "plays_as_version": SCRIPTED_BOT_VERSION}


@register_controller("dry")
def _dry_factory(rng: GameRng, arg: str | None) -> SeatController:
    if not arg:
        raise ValueError("dry seats need a model label, e.g. dry:claude-fable-5-1")
    model, effort = split_effort(arg)
    setting = None
    if effort is not None:
        spec = load_models().get(model)
        # Checked like a live Seat's level; a label outside models.toml keeps the level as-is.
        setting = spec.at_effort(effort).effort_setting if spec is not None else effort
    return DryRunSeat(rng, model, effort, setting)
