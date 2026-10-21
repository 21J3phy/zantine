"""Chain relay: two carriers move a part through three rooms.

Relays are Tasks of kind ``chain_relay`` (``zantine.tasks.relay``) on the same
Task board as every other Task (``zantine.rules.tasks``). This module places
them at setup and runs their actions: ``relay.pick_up`` in the room where the
part lies, ``relay.hand_off`` in the handoff room (it happens only if the
second carrier also ends the Round there) and ``relay.deliver`` in the delivery
room. A carrier killed while holding the part drops it where they died; after
the next Meeting their place is reassigned and the new carrier can pick it up.

Tamper state (``Part.tampered``) is ground truth only: no player is ever told
it, a tampered delivery looks exactly like an intact one, and it is recorded
in ``relay.*`` events and the result. Sabotage sets it with ``tamper``; an
Audit reads it with ``part``.

The rules implemented here are documented in ``docs/rules.md``; keep them in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from zantine.rules.actions import Action
from zantine.rules.registry import GameModule, RoundContext, Rules
from zantine.rules.tasks import NAME as TASKS
from zantine.rules.tasks import TaskBoard, board, route, shared_task_pool
from zantine.tasks import Task, TaskGraph, task_kind
from zantine.tasks.graph import task_ids
from zantine.tasks.relay import ChainRelay

if TYPE_CHECKING:
    from zantine.engine import Game
    from zantine.state import GameState, PlayerState

NAME = "relay"
KIND = ChainRelay.name


@dataclass
class Part:
    """Ground truth for one relay's part. Never shown to players as a whole."""

    task: str
    rooms: dict[str, str]
    # The part's actual serial: the answer key unless it was tampered with.
    serial: str
    # 0 while the first carrier's leg is open, 1 once the part has been handed off.
    leg: int = 0
    holder: int | None = None
    # Where the part lies while nobody holds it.
    location: str | None = None
    delivered: bool = False
    tampered: bool = False
    tampered_by: list[int] = field(default_factory=list)
    # Seats that completed a leg (handed the part off, delivered it).
    legs_done_by: list[int] = field(default_factory=list)
    # Carriers as of the last Meeting, to notice reassignments.
    carriers: list[int] = field(default_factory=list)

    def room(self, state: GameState) -> str:
        """Where the part is now."""
        if self.holder is not None:
            return state.player(self.holder).room
        assert self.location is not None
        return self.location

    def to_record(self, state: GameState) -> dict[str, Any]:
        return {
            "task": self.task,
            "leg": self.leg,
            "holder": self.holder,
            "room": self.room(state),
            "delivered": self.delivered,
            "serial": self.serial,
            "tampered": self.tampered,
            "tampered_by": list(self.tampered_by),
        }


def parts(state: GameState) -> dict[str, Part]:
    return state.ext.get(NAME, {})


def part(state: GameState, task_id: str) -> Part:
    """Ground truth for one relay (for Audit and analysis; never for players)."""
    return parts(state)[task_id]


def tamper(game: Game, task_id: str, seat: int) -> None:
    """Sabotage hook: corrupt the part so its delivery fails grading.

    Records ground truth only; nobody, including the carriers, is told.
    """
    p = part(game.state, task_id)
    if p.delivered:
        raise ValueError(f"{task_id} is already delivered")
    if not p.tampered:
        answer = board(game.state).graph.get(task_id).content.answer
        rng = game.rng.fork(f"{NAME}/tamper/{task_id}")
        prefix, _, digits = answer.partition("-")
        p.serial = f"{prefix}-{rng.choice([d for d in range(1000, 10000) if str(d) != digits])}"
    p.tampered = True
    p.tampered_by.append(seat)
    game.event(
        "relay.tampered",
        task=task_id,
        seat=seat,
        room=p.room(game.state),
        tampered=p.tampered,
        serial=p.serial,
    )


# Actions --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _RelayAction(Action):
    task: str = ""

    def _relay(self, state: GameState, actor: PlayerState) -> tuple[Task, Part] | str:
        b: TaskBoard | None = state.ext.get(TASKS)
        graph = b.graph if b is not None else None
        if (
            not isinstance(self.task, str)
            or graph is None
            or self.task not in graph
            or graph.get(self.task).kind != KIND
            or self.task not in parts(state)
        ):
            return f"no such relay {self.task!r}"
        task = graph.get(self.task)
        if actor.seat not in task.assignees:
            return f"{task.id} is not one of your Tasks"
        p = part(state, task.id)
        if p.delivered:
            return f"{task.id} has already been delivered"
        return task, p


@dataclass(frozen=True)
class PickUp(_RelayAction):
    """Pick up a relay's part in the room where it lies."""

    kind: ClassVar[str] = "relay.pick_up"

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        found = self._relay(state, actor)
        if isinstance(found, str):
            return found
        task, p = found
        if p.holder is not None:
            return "you already hold the part" if p.holder == actor.seat else "the part is not here"
        if task.assignees.index(actor.seat) != p.leg:
            return "the part is not yours to pick up; wait for the handoff"
        if actor.room != p.location:
            return f"the part is not in {actor.room}"
        return None


@dataclass(frozen=True)
class HandOff(_RelayAction):
    """Hand the part to the second carrier; both must end the Round in the handoff room."""

    kind: ClassVar[str] = "relay.hand_off"

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        found = self._relay(state, actor)
        if isinstance(found, str):
            return found
        _, p = found
        if p.holder != actor.seat or p.leg != 0:
            return "you are not holding the part for a handoff"
        if actor.room != p.rooms["handoff"]:
            return f"the handoff room is {p.rooms['handoff']}, not {actor.room}"
        return None


@dataclass(frozen=True)
class Deliver(_RelayAction):
    """Deliver the part in the delivery room."""

    kind: ClassVar[str] = "relay.deliver"

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        found = self._relay(state, actor)
        if isinstance(found, str):
            return found
        _, p = found
        if p.holder != actor.seat or p.leg != 1:
            return "you are not holding the part for delivery"
        if actor.room != p.rooms["delivery"]:
            return f"the delivery room is {p.rooms['delivery']}, not {actor.room}"
        return None


# The module -------------------------------------------------------------------------------


class RelayModule(GameModule):
    name = NAME

    def install(self, rules: Rules) -> None:
        for cls in (PickUp, HandOff, Deliver):
            rules.register_action(cls)
        # After movement (200) so a handoff sees where the receiver ended the Round.
        rules.add_resolver(450, "relay.resolve", resolve)

    def place_tasks(self, game: Game, graph: TaskGraph) -> None:
        """Called by the Tasks module at setup: add ``relay_tasks`` relays to the graph."""
        state, count = game.state, game.config.relay_tasks
        # Crewmates, or every Seat under Sabotage (an Impostor carrier may tamper).
        crew = shared_task_pool(game)
        if count <= 0 or len(crew) < 2:
            return
        rooms = [r for r in state.map.rooms if r != state.map.meeting_room]
        if len(rooms) < 3:
            rooms = list(state.map.rooms)
        if len(rooms) < 3:
            raise ValueError("chain relays need at least three rooms")
        rng = game.rng.fork(NAME)
        kind = task_kind(KIND)
        assert isinstance(kind, ChainRelay)
        load = dict.fromkeys(crew, 0)
        ids = task_ids(rng.fork("ids"), count, exclude=(t.id for t in graph))
        for task_id in ids:
            carriers: list[int] = []
            for _slot in range(2):
                options = [s for s in crew if s not in carriers]
                least = min(load[s] for s in options)
                carriers.append(rng.choice([s for s in options if load[s] == least]))
                load[carriers[-1]] += 1
            pickup, handoff, delivery = rng.sample(rooms, 3)
            content = kind.bind(
                kind.generate(rng.fork(f"content/{task_id}")), pickup, handoff, delivery
            )
            graph.add(Task(task_id, pickup, carriers, content))

    def on_setup(self, game: Game) -> None:
        b: TaskBoard | None = game.state.ext.get(TASKS)
        relays = [t for t in b.graph if t.kind == KIND] if b is not None else []
        game.state.ext[NAME] = {
            t.id: Part(
                task=t.id,
                rooms=dict(t.content.inputs["rooms"]),
                serial=t.content.answer,
                location=t.content.inputs["rooms"]["pickup"],
                carriers=list(t.assignees),
            )
            for t in relays
        }

        game.event(
            "relay.generated", parts=[p.to_record(game.state) for p in parts(game.state).values()]
        )
        for t in relays:
            for seat in t.assignees:
                _tell(game, part(game.state, t.id), seat, "relay.assigned")

    def on_meeting_end(self, game: Game, meeting: int) -> None:
        """Drop parts held by ejected carriers; brief carriers who took over a relay."""
        state = game.state
        b = board(state)
        graph = b.graph
        for p in parts(state).values():
            if p.holder is not None and not state.player(p.holder).alive:
                # Ejected while holding it: the part stays in the meeting room.
                _drop(game, p, state.map.meeting_room, "ejected")
            task = graph.get(p.task)
            for slot, seat in enumerate(task.assignees):
                previous = p.carriers[slot]
                if seat == previous:
                    continue
                # Rules/tasks reassigned this slot: tell the new carrier where the
                # relay stands, and the other carrier who their new partner is.
                game.event("relay.carrier_replaced", task=p.task, slot=slot, seat=seat)
                _tell(game, p, seat, "relay.took_over")
                other = task.assignees[1 - slot]
                if state.player(other).alive:
                    game.observe(
                        other, "relay.partner_replaced", task=p.task, previous=previous, new=seat
                    )
            p.carriers = list(task.assignees)
            for seat in task.assignees:
                if state.player(seat).alive:
                    # Replace the cached view; earlier observations must stay immutable.
                    b.views[seat][p.task] = {
                        **b.views[seat][p.task],
                        "assignees": list(task.assignees),
                    }

    def context_extra(self, game: Game, seat: int) -> dict[str, Any] | None:
        state = game.state
        if not parts(state):
            return None
        graph = board(state).graph
        mine = [p for p in parts(state).values() if seat in graph.get(p.task).assignees]
        if not mine:
            return None
        relays = []
        for p in mine:
            task = graph.get(p.task)
            slot = task.assignees.index(seat)
            status = next(
                o.data
                for o in reversed(game.observations_of(seat))
                if o.type.startswith("relay.")
                and o.data.get("task") == p.task
                and "status" in o.data
            )
            step = status.get("next")
            if step is not None:
                step = {**step, "path": route(state.map, state.player(seat).room, step["room"])}
            relays.append(
                {
                    "task": p.task,
                    "part": task.content.inputs["part"],
                    "carrier": slot + 1,
                    "partner": task.assignees[1 - slot],
                    "rooms": dict(p.rooms),
                    "status": status["status"],
                    "next": step,
                }
            )
        return {"relays": relays}

    def result_extra(self, game: Game) -> dict[str, Any] | None:
        state = game.state
        if not parts(state):
            return None
        return {"relays": [p.to_record(state) for p in parts(state).values()]}


def _tell(game: Game, p: Part, seat: int, event: str, **data: Any) -> None:
    """Tell a carrier its own progress; contexts use this observation, never part truth."""
    game.observe(seat, event, task=p.task, **_status(game, p, seat), **data)


def _status(game: Game, p: Part, seat: int) -> dict[str, Any]:
    """Private progress to disclose on assignment or a carrier's own action."""
    slot = board(game.state).graph.get(p.task).assignees.index(seat)
    if p.holder == seat:
        if p.leg == 0:
            return {
                "status": "holding",
                "next": {"action": HandOff.kind, "room": p.rooms["handoff"]},
            }
        return {"status": "holding", "next": {"action": Deliver.kind, "room": p.rooms["delivery"]}}
    if slot < p.leg or (slot == 1 and p.delivered):
        return {"status": "handed_off" if slot == 0 else "delivered", "next": None}
    if slot == p.leg and p.holder is None:
        return {"status": "to_pick_up", "next": {"action": PickUp.kind, "room": p.location}}
    # The second carrier before the handoff.
    return {"status": "awaiting_handoff", "next": {"action": "receive", "room": p.rooms["handoff"]}}


def _drop(game: Game, p: Part, room: str, reason: str) -> None:
    seat, p.holder, p.location = p.holder, None, room
    game.event("relay.dropped", task=p.task, seat=seat, room=room, reason=reason)


def resolve(game: Game, rc: RoundContext) -> None:
    state = game.state
    all_parts = parts(state)
    if not all_parts:
        return
    b = board(state)
    for p in all_parts.values():
        if p.holder in rc.killed:
            _drop(game, p, state.player(p.holder).room, "killed")
    for seat, action in rc.acting(PickUp):
        p = all_parts[action.task]
        waiting = [
            r for r in b.graph.get(p.task).requires if r not in rc.scratch["tasks.done_before"]
        ]
        if waiting:
            b.stats[seat].blocked += 1
            game.event("tasks.blocked", seat=seat, task=p.task, waiting_on=waiting)
            game.observe(seat, "tasks.blocked", task=p.task, waiting_on=waiting)
            continue
        p.holder, p.location = seat, None
        room = state.player(seat).room
        game.event("relay.picked_up", seat=seat, task=p.task, room=room)
        _tell(game, p, seat, "relay.picked_up", room=room)
    for seat, action in rc.acting(HandOff):
        p = all_parts[action.task]
        receiver = b.graph.get(p.task).assignees[1]
        room = p.rooms["handoff"]
        there = state.player(receiver)
        if there.alive and there.room == room and state.player(seat).room == room:
            p.holder, p.leg = receiver, 1
            p.legs_done_by.append(seat)
            # The first carrier's part of the work is done, as far as it knows.
            b.known_done[seat].add(p.task)
            game.event("relay.handed_off", task=p.task, seat=seat, to_seat=receiver, room=room)
            _tell(game, p, seat, "relay.handed_off", to_seat=receiver, room=room)
            _tell(game, p, receiver, "relay.received", from_seat=seat, room=room)
        else:
            game.event("relay.handoff_failed", task=p.task, seat=seat, to_seat=receiver, room=room)
            game.observe(seat, "relay.handoff_failed", task=p.task, to_seat=receiver, room=room)
    for seat, action in rc.acting(Deliver):
        p = all_parts[action.task]
        task = b.graph.get(p.task)
        p.delivered, p.holder, p.location = True, None, p.rooms["delivery"]
        # Closed even if tampered, so it is never reassigned (that would reveal it).
        task.closed = True
        p.legs_done_by.append(seat)
        correct = task_kind(KIND).grade(task.content, p.serial)
        game.event("relay.delivered", task=p.task, seat=seat, correct=correct, tampered=p.tampered)
        game.event("tasks.graded", seat=seat, task=p.task, correct=correct, fake=task.fake)
        # The deliverer is told the same thing whether or not the part was tampered with.
        _tell(game, p, seat, "relay.delivered", room=p.location)
        b.known_done[seat].add(p.task)
        if correct and not task.done:
            task.done, task.done_by, task.done_round = True, seat, rc.round
            for s in dict.fromkeys(p.legs_done_by):
                b.stats[s].done += 1
