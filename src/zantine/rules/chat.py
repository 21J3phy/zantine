"""Room Chat: talk during the action phase, heard only by players in the same room.

Talking is not an action. Every Round, a Seat may say something *alongside* its
action (``SeatController.say``; the Neutral Harness's ``say`` tool argument);
the engine collects it into ``RoundContext.speech``. At order 410, after kills
and movement, each message is cut to the speaker's Talk Budget for the Round
and delivered to every living player in the room where the speaker *ends* the
Round, the speaker included: the same set of players its end-of-Round room
snapshot shows. A speaker killed that Round says nothing, and a forfeited Round
(two Invalid Actions) says nothing.

Room Chat observations are private (room-scoped): they never go in the
summarizer's ``PUBLIC_TYPES``.

The rules implemented here are documented in ``docs/rules.md``; keep them in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from zantine.rules.registry import GameModule, RoundContext, Rules

if TYPE_CHECKING:
    from zantine.engine import Game

NAME = "chat"


@dataclass
class ChatLog:
    """Module state in ``GameState.ext["chat"]``: per-Seat counts (Floor Share)."""

    messages: dict[int, int] = field(default_factory=dict)
    chars: dict[int, int] = field(default_factory=dict)
    truncated: dict[int, int] = field(default_factory=dict)


class ChatModule(GameModule):
    name = NAME

    def install(self, rules: Rules) -> None:
        # After kills (100) and movement (200): who hears depends on end-of-Round rooms.
        rules.add_resolver(410, "chat.deliver", deliver)

    def on_setup(self, game: Game) -> None:
        seats = [p.seat for p in game.state.players]
        game.state.ext[NAME] = ChatLog(
            messages=dict.fromkeys(seats, 0),
            chars=dict.fromkeys(seats, 0),
            truncated=dict.fromkeys(seats, 0),
        )

    def context_extra(self, game: Game, seat: int) -> dict[str, Any] | None:
        return {"budget_chars": game.config.room_chat_budget(seat)}

    def result_extra(self, game: Game) -> dict[str, Any] | None:
        log: ChatLog = game.state.ext[NAME]
        return {
            "messages": sum(log.messages.values()),
            "seats": [
                {
                    "seat": seat,
                    "messages": log.messages[seat],
                    "chars": log.chars[seat],
                    "truncated": log.truncated[seat],
                }
                for seat in sorted(log.messages)
            ],
        }


def deliver(game: Game, rc: RoundContext) -> None:
    state = game.state
    log: ChatLog = state.ext[NAME]
    for seat in sorted(rc.speech):
        raw = rc.speech[seat]
        if not raw.strip():
            continue
        if seat in rc.killed:
            game.event("chat.dropped", speaker=seat, reason="killed", said=raw)
            continue
        budget = game.config.room_chat_budget(seat)
        text = raw[:budget]
        truncated = len(raw) > budget
        if not text.strip():
            game.event("chat.dropped", speaker=seat, reason="no_budget", said=raw)
            continue
        room = state.player(seat).room
        listeners = [p.seat for p in state.living_in(room)]
        log.messages[seat] += 1
        log.chars[seat] += len(text)
        log.truncated[seat] += int(truncated)
        game.event(
            "chat.message",
            speaker=seat,
            room=room,
            text=text,
            truncated=truncated,
            listeners=listeners,
        )
        for listener in listeners:
            game.observe(listener, "chat.message", speaker=seat, room=room, text=text)
