"""Action types. Each action validates itself against the start-of-Round state.

Plug-ins define new actions in their own module (subclass ``Action``, set a
unique ``kind``) and register them with ``Rules.register_action``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import TYPE_CHECKING, Any, ClassVar, Self

if TYPE_CHECKING:
    from zantine.state import GameState, PlayerState


@dataclass(frozen=True)
class Action:
    kind: ClassVar[str] = ""

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        """None if legal, else a short reason shown to the player."""
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        names = {f.name for f in fields(cls)}
        extra = set(data) - names - {"kind"}
        if extra:
            raise ValueError(f"unexpected fields {sorted(extra)}")
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass(frozen=True)
class Invalid(Action):
    """A response the game cannot accept (e.g. unparseable model output)."""

    kind: ClassVar[str] = "invalid"
    reason: str = "invalid action"
    raw: str = ""

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        return self.reason


@dataclass(frozen=True)
class Wait(Action):
    kind: ClassVar[str] = "wait"


@dataclass(frozen=True)
class Move(Action):
    kind: ClassVar[str] = "move"
    to: str = ""

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        if not state.map.is_adjacent(actor.room, self.to):
            return f"{self.to!r} is not adjacent to {actor.room}"
        return None


@dataclass(frozen=True)
class Kill(Action):
    kind: ClassVar[str] = "kill"
    target: int = -1

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        if not actor.is_impostor:
            return "only Impostors can kill"
        if actor.kill_cooldown > 0:
            return f"kill cooldown: {actor.kill_cooldown} more Round(s)"
        if not isinstance(self.target, int) or not 0 <= self.target < len(state.players):
            return f"no such player {self.target!r}"
        target = state.player(self.target)
        if not target.alive or target.room != actor.room:
            return "target is not alive in your room"
        if target.is_impostor:
            return "cannot kill an Impostor"
        return None


@dataclass(frozen=True)
class Report(Action):
    """Report a body in your room, starting a Meeting."""

    kind: ClassVar[str] = "report"

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        if not state.bodies_in(actor.room):
            return "there is no body in your room"
        return None


@dataclass(frozen=True)
class CallMeeting(Action):
    """Call an emergency Meeting (limited uses per seat)."""

    kind: ClassVar[str] = "call_meeting"

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        if actor.emergency_meetings_left <= 0:
            return "no emergency meetings left"
        return None
