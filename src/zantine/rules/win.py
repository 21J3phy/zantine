"""Core win conditions. Checked in order after every Round and every Meeting.

Ejecting every Impostor (Containment) does not end the game; see
``rules/containment.py``. The round cap is enforced by the engine itself, after
any Meeting in the final Round, so that other conditions always take precedence
over it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from zantine.roles import Team

if TYPE_CHECKING:
    from zantine.engine import Game


def parity(game: Game) -> tuple[Team, str] | None:
    living = game.state.living()
    impostors = sum(p.is_impostor for p in living)
    if impostors >= len(living) - impostors:
        return Team.IMPOSTORS, "parity"
    return None


def round_cap(game: Game) -> tuple[Team, str]:
    """The game ran out of Rounds: the Impostors win if any is still in play."""
    in_play = any(p.is_impostor for p in game.state.living())
    return (Team.IMPOSTORS if in_play else Team.NONE), "round_cap"
