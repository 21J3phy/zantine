"""Core win conditions. Checked in order after every Round and every Meeting.

The round cap is enforced by the engine itself, after any Meeting in the final
Round, so that other conditions always take precedence over it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from zantine.roles import Team

if TYPE_CHECKING:
    from zantine.engine import Game


def impostors_ejected(game: Game) -> tuple[Team, str] | None:
    if not any(p.is_impostor for p in game.state.living()):
        return Team.CREW, "impostors_ejected"
    return None


def parity(game: Game) -> tuple[Team, str] | None:
    living = game.state.living()
    impostors = sum(p.is_impostor for p in living)
    if impostors >= len(living) - impostors:
        return Team.IMPOSTORS, "parity"
    return None
