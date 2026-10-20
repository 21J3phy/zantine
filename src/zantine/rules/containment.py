"""Containment: the moment every Impostor has been ejected.

Containment is recorded but does not end the game: play continues until all
Tasks are done or the round cap is reached, so Productivity is never cut short.
Players are never told about it (Hidden Impostor Count). Rules are documented
in ``docs/rules.md``; keep the two in sync.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from zantine.rules.registry import GameModule

if TYPE_CHECKING:
    from zantine.engine import Game

NAME = "containment"


class ContainmentModule(GameModule):
    name = NAME

    def on_setup(self, game: Game) -> None:
        # {"round": r, "meeting": m} once Containment happens; None until then.
        game.state.ext[NAME] = None

    def on_meeting_end(self, game: Game, meeting: int) -> None:
        # Impostors can only leave play by Ejection, so a Meeting is the only
        # place Containment can happen.
        state = game.state
        impostors = [p for p in state.players if p.is_impostor]
        if state.ext[NAME] is not None or not impostors or any(p.alive for p in impostors):
            return
        state.ext[NAME] = {"round": state.round, "meeting": meeting}
        # Ground truth only: telling players would reveal the Impostor count.
        game.event("containment", meeting=meeting, impostors=[p.seat for p in impostors])

    def result_extra(self, game: Game) -> dict[str, Any] | None:
        contained = game.state.ext[NAME]
        return {
            "contained": contained is not None,
            **(contained or {"round": None, "meeting": None}),
        }
