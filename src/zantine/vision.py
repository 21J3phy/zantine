"""What a player perceives of its own room. Players never perceive other rooms."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zantine.engine import Game
    from zantine.state import GameState


def room_view(state: GameState, seat: int) -> dict[str, Any]:
    room = state.player(seat).room
    return {
        "room": room,
        "players": sorted(p.seat for p in state.living_in(room) if p.seat != seat),
        "bodies": sorted(b.victim for b in state.bodies_in(room)),
    }


def emit_room_snapshots(game: Game) -> None:
    """Give every living player a ``room_snapshot`` of its current room."""
    for p in game.state.living():
        game.observe(p.seat, "room_snapshot", **room_view(game.state, p.seat))
