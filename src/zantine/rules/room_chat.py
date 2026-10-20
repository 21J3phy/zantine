"""Room Chat: talking during the action phase, heard only in the speaker's room.

Saying something is a Round's action (``room_chat.say``). It is heard by every
player who started the Round in the speaker's room and is still alive after
kills, including the speaker and anyone walking out that Round (talk happens
at the same time as movement); a player walking in does not hear it. Speech
longer than the speaker's Talk Budget is truncated. Rules are documented in
``docs/rules.md``; keep the two in sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from zantine.rules.actions import Action
from zantine.rules.registry import GameModule, RoundContext, Rules

if TYPE_CHECKING:
    from zantine.engine import Game
    from zantine.state import GameState, PlayerState

NAME = "room_chat"


@dataclass(frozen=True)
class Say(Action):
    """Say something to everyone in your room this Round."""

    kind: ClassVar[str] = "room_chat.say"
    text: str = ""

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        if not isinstance(self.text, str) or not self.text.strip():
            return "text must be a non-empty string"
        return None


class RoomChatModule(GameModule):
    name = NAME

    def install(self, rules: Rules) -> None:
        rules.register_action(Say)
        rules.add_resolver(450, "room_chat.deliver", deliver)

    def context_extra(self, game: Game, seat: int) -> dict[str, Any] | None:
        return {"talk_budget_chars": game.config.talk_budget(seat)}


def deliver(game: Game, rc: RoundContext) -> None:
    state = game.state
    for seat, action in rc.acting(Say):
        room = rc.start_rooms[seat]
        budget = game.config.talk_budget(seat)
        text = action.text[:budget]
        listeners = [
            s for s in sorted(rc.start_rooms) if rc.start_rooms[s] == room and state.player(s).alive
        ]
        message = {"speaker": seat, "room": room, "text": text, "listeners": listeners}
        game.event("room_chat.message", truncated=len(action.text) > budget, **message)
        for listener in listeners:
            game.observe(listener, "room_chat.message", **message)
