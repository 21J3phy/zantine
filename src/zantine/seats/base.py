"""The interface every Seat implements: scripted bots now, live Model Harnesses later.

A controller only ever sees context objects (``ActionContext``,
``MeetingContext``), never the ground-truth ``GameState``. Controllers may keep
their own memory between calls, but everything they are told is already in
``ctx.observations``.

Returning something the game can't accept (or ``Invalid(...)``) is an Invalid
Action: the engine asks once more with ``ctx.retry_reason`` set, then forfeits
(``Wait`` for actions, skip for votes).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from zantine.observations import ActionContext, MeetingContext
from zantine.rules.actions import Action


class SeatController(ABC):
    kind: ClassVar[str] = "abstract"

    def describe(self) -> dict[str, Any]:
        """Ground-truth description for the transcript header (e.g. model ID).

        Never shown to other players (Hidden Identity).
        """
        return {"kind": self.kind}

    @abstractmethod
    def choose_action(self, ctx: ActionContext) -> Action:
        """Pick this Round's action."""

    @abstractmethod
    def speak(self, ctx: MeetingContext) -> str:
        """Say something in the current Meeting pass (truncated to the Talk Budget)."""

    @abstractmethod
    def vote(self, ctx: MeetingContext) -> int | None:
        """Vote for a living seat, or None to skip."""
