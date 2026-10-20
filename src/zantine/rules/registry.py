"""The plug-in surface of the engine.

A ``GameModule`` adds rules without editing the engine: it registers action
kinds, resolver steps (run in ``order`` during every Round), win conditions,
and optional hooks. Modules keep their own state in ``GameState.ext[name]``.

Resolver order bands (leave gaps so plug-ins can slot in between):

====  ===========================================================
 100  kills (core)            kills resolve first; victims' actions are cancelled
 200  movement (core)
 300  reports and emergency calls (core)
 400  -  plug-ins: work on Tasks, Room Chat, Crisis, Sabotage ...
 900  cooldown ticks (core)
1000  end-of-Round room snapshots (core)
====  ===========================================================
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from zantine.roles import Team
from zantine.rules.actions import Action, Invalid

if TYPE_CHECKING:
    from zantine.engine import Game
    from zantine.state import GameState, PlayerState


@dataclass(frozen=True)
class MeetingRequest:
    reason: str  # "report" | "emergency" (plug-ins may add more)
    caller: int
    body_victim: int | None = None
    body_room: str | None = None
    # Every seat that asked for a Meeting this Round, in seat order.
    requested_by: tuple[int, ...] = ()


@dataclass
class RoundContext:
    """Scratch space shared by the resolvers of one Round."""

    round: int
    # The accepted action of every seat alive at the start of the Round.
    actions: dict[int, Action]
    start_rooms: dict[int, str]
    # Seats killed this Round; their other actions are cancelled.
    killed: set[int] = field(default_factory=set)
    meeting: MeetingRequest | None = None
    # Free-form per-module scratch data for this Round.
    scratch: dict[str, Any] = field(default_factory=dict)

    def acting[A: Action](self, kind: type[A]) -> Iterator[tuple[int, A]]:
        """(seat, action) for every surviving seat whose action is a ``kind``, by seat."""
        for seat in sorted(self.actions):
            action = self.actions[seat]
            if seat not in self.killed and isinstance(action, kind):
                yield seat, action


Resolver = Callable[["Game", RoundContext], None]
WinCheck = Callable[["Game"], tuple[Team, str] | None]


class GameModule:
    """Base class for rule plug-ins. Override what you need."""

    name: ClassVar[str] = "module"

    def install(self, rules: Rules) -> None:
        """Register actions, resolvers and win conditions."""

    def on_setup(self, game: Game) -> None:
        """Called once before Round 1, after roles are assigned."""

    def on_meeting_end(self, game: Game, meeting: int) -> None:
        """Called after every Meeting (and any Ejection) finishes."""

    def context_extra(self, game: Game, seat: int) -> dict[str, Any] | None:
        """Extra fields for this seat's ``ActionContext.extra`` / ``MeetingContext.extra``."""
        return None

    def result_extra(self, game: Game) -> dict[str, Any] | None:
        """Extra data for the transcript's ``result`` record, stored under ``name``."""
        return None


@dataclass(frozen=True)
class _Step[F]:
    order: int
    name: str
    fn: F


class Rules:
    def __init__(self) -> None:
        self.modules: list[GameModule] = []
        self.actions: dict[str, type[Action]] = {}
        self._resolvers: list[_Step[Resolver]] = []
        self._win_checks: list[_Step[WinCheck]] = []

    def install(self, module: GameModule) -> None:
        if any(m.name == module.name for m in self.modules):
            raise ValueError(f"module {module.name!r} installed twice")
        self.modules.append(module)
        module.install(self)

    def register_action(self, cls: type[Action]) -> None:
        if not cls.kind or cls.kind in self.actions:
            raise ValueError(f"bad or duplicate action kind {cls.kind!r}")
        self.actions[cls.kind] = cls

    def add_resolver(self, order: int, name: str, fn: Resolver) -> None:
        self._resolvers.append(_Step(order, name, fn))

    def add_win_condition(self, order: int, name: str, fn: WinCheck) -> None:
        self._win_checks.append(_Step(order, name, fn))

    def resolvers(self) -> list[_Step[Resolver]]:
        return sorted(self._resolvers, key=lambda s: (s.order, s.name))

    def win_conditions(self) -> list[_Step[WinCheck]]:
        return sorted(self._win_checks, key=lambda s: (s.order, s.name))

    def validate(self, action: object, state: GameState, actor: PlayerState) -> str | None:
        """None if ``action`` is legal for ``actor`` right now, else the reason it isn't."""
        if not isinstance(action, Action):
            return f"not an action: {action!r}"
        if isinstance(action, Invalid):
            return action.reason
        if self.actions.get(action.kind) is not type(action):
            return f"unknown action kind {action.kind!r}"
        return action.validate(state, actor)

    def parse_action(self, data: dict[str, Any]) -> Action:
        """Build an action from its ``to_dict()`` form; returns ``Invalid`` on bad input."""
        kind = data.get("kind")
        cls = self.actions.get(kind) if isinstance(kind, str) else None
        if cls is None:
            return Invalid(reason=f"unknown action kind {kind!r}", raw=repr(data))
        try:
            return cls.from_dict(data)
        except (TypeError, ValueError) as exc:
            return Invalid(reason=f"bad {kind} action: {exc}", raw=repr(data))
