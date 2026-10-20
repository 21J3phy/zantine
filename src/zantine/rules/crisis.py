"""Crisis: an emergency an Impostor triggers that the crew must resolve in time.

An Impostor triggers a Crisis (team-wide cooldown). Two station rooms are drawn
from the seed. Within the deadline, players standing in *both* stations must
each submit ``crisis.fix`` in the same Round; otherwise the Impostors win.
Rules are documented in ``docs/rules.md``; keep the two in sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from zantine.roles import Team
from zantine.rules.actions import Action
from zantine.rules.registry import GameModule, RoundContext, Rules

if TYPE_CHECKING:
    from zantine.engine import Game
    from zantine.state import GameState, PlayerState

NAME = "crisis"


def _crisis(state: GameState) -> dict[str, Any]:
    return state.ext[NAME]


@dataclass(frozen=True)
class TriggerCrisis(Action):
    """Impostor only: start a Crisis."""

    kind: ClassVar[str] = "crisis.trigger"

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        crisis = _crisis(state)
        if not actor.is_impostor:
            return "only Impostors can trigger a Crisis"
        if crisis["active"] is not None:
            return "a Crisis is already active"
        if crisis["cooldown"] > 0:
            return f"Crisis cooldown: {crisis['cooldown']} more Round(s)"
        return None


@dataclass(frozen=True)
class FixCrisis(Action):
    """Work on the active Crisis at a station in your room."""

    kind: ClassVar[str] = "crisis.fix"

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        active = _crisis(state)["active"]
        if active is None:
            return "there is no active Crisis"
        if actor.room not in active["stations"]:
            return f"no Crisis station in {actor.room}; stations: {', '.join(active['stations'])}"
        return None


class CrisisModule(GameModule):
    name = NAME

    def install(self, rules: Rules) -> None:
        rules.register_action(TriggerCrisis)
        rules.register_action(FixCrisis)
        rules.add_resolver(500, "crisis.resolve", self.resolve)
        rules.add_win_condition(300, "crisis.unresolved", unresolved)

    def on_setup(self, game: Game) -> None:
        game.state.ext[NAME] = {
            "cooldown": game.config.crisis_cooldown,
            # {"stations": [a, b], "started": round, "deadline": round, "triggered_by": [...]}
            "active": None,
            "failed": False,
            "count": 0,
        }
        self.rng = game.rng.fork(NAME)

    def resolve(self, game: Game, rc: RoundContext) -> None:
        crisis = _crisis(game.state)
        active = crisis["active"]
        if active is not None:
            self._resolve_fixes(game, rc, crisis, active)
            return
        triggers = [seat for seat, _ in rc.acting(TriggerCrisis)]
        if triggers:
            self._start(game, rc, crisis, triggers)
        else:
            crisis["cooldown"] = max(0, crisis["cooldown"] - 1)

    def _start(
        self, game: Game, rc: RoundContext, crisis: dict[str, Any], triggers: list[int]
    ) -> None:
        state = game.state
        candidates = [r for r in state.map.rooms if r != state.map.meeting_room]
        if len(candidates) < 2:
            candidates = list(state.map.rooms)
        stations = sorted(self.rng.sample(candidates, 2))
        deadline = rc.round + game.config.crisis_deadline
        crisis["count"] += 1
        crisis["active"] = {
            "number": crisis["count"],
            "stations": stations,
            "started": rc.round,
            "deadline": deadline,
            "triggered_by": triggers,
        }
        game.event(
            "crisis.started",
            number=crisis["count"],
            triggered_by=triggers,
            stations=stations,
            deadline=deadline,
        )
        # Everyone is told, but never who triggered it.
        for p in state.living():
            game.observe(
                p.seat,
                "crisis.started",
                number=crisis["count"],
                stations=stations,
                deadline=deadline,
                rounds_left=deadline - rc.round,
            )

    def _resolve_fixes(
        self, game: Game, rc: RoundContext, crisis: dict[str, Any], active: dict[str, Any]
    ) -> None:
        state = game.state
        fixers: dict[str, list[int]] = {room: [] for room in active["stations"]}
        for seat, _ in rc.acting(FixCrisis):
            fixers[rc.start_rooms[seat]].append(seat)
        resolved = all(fixers.values())
        for room, seats in fixers.items():
            for seat in seats:
                game.observe(seat, "crisis.fix_attempted", room=room, resolved=resolved)
        if resolved:
            crisis["active"] = None
            crisis["cooldown"] = game.config.crisis_cooldown
            all_fixers = sorted(s for seats in fixers.values() for s in seats)
            game.event("crisis.resolved", number=active["number"], fixers=all_fixers)
            for p in state.living():
                game.observe(p.seat, "crisis.resolved", number=active["number"])
        elif rc.round >= active["deadline"]:
            crisis["failed"] = True
            game.event("crisis.failed", number=active["number"], stations=active["stations"])
            for p in state.living():
                game.observe(p.seat, "crisis.failed", number=active["number"])

    def context_extra(self, game: Game, seat: int) -> dict[str, Any] | None:
        crisis = _crisis(game.state)
        active = crisis["active"]
        extra: dict[str, Any] = {"active": active is not None}
        if active is not None:
            # ``deadline`` is the last Round in which a fix counts.
            extra.update(stations=list(active["stations"]), deadline=active["deadline"])
        if game.state.player(seat).is_impostor:
            extra["cooldown"] = crisis["cooldown"]
        return extra


def unresolved(game: Game) -> tuple[Team, str] | None:
    if _crisis(game.state)["failed"]:
        return Team.IMPOSTORS, "crisis"
    return None
