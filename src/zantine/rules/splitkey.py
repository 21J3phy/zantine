"""Split-key: the rules of the 3-way split-key Shared Task.

Split-key Tasks (kind ``split_key``, ``zantine.tasks.splitkey``) live on the
same Task board as every other Task (``zantine.rules.tasks``). This module
places them at setup and runs their two actions:

- ``splitkey.read`` at the holder's own terminal room privately shows that
  holder its fragment of the current code and the checksum of the next part.
  The first read of a code *opens* it: the whole code must then be entered
  within ``splitkey_window`` Rounds (the deadline is the read Round plus the
  window), and every living holder is told the deadline.
- ``splitkey.enter`` in the entry room submits the whole code. Only the
  enterer learns its grade (``tasks.graded``). A correct code completes the
  Task and every living holder is told. A wrong code, or a deadline passing,
  fails the attempt: the code is replaced by a fresh one from the seed, every
  fragment must be read again, and every living holder is told why (for a wrong
  code, which code was entered, so the checksum holders can find the bad part).

Fragments move only through what players say, so a holder can lie. The rules
never check a holder's role, so Sabotage (#8) can let an Impostor hold a
fragment by widening ``SplitKeyModule.holder_pool``. ``keys(state)`` exposes the
ground truth for analysis and Audit; ``reset(game, task_id, reason)`` lets a
Sabotage rule fail an attempt.

The rules implemented here are documented in ``docs/rules.md``; keep them in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from zantine.map import GameMap
from zantine.rules.actions import Action
from zantine.rules.registry import GameModule, RoundContext, Rules
from zantine.rules.tasks import NAME as TASKS
from zantine.rules.tasks import TaskBoard, board, route
from zantine.tasks import Task, TaskContent, TaskGraph, task_kind
from zantine.tasks.graph import task_ids
from zantine.tasks.splitkey import SplitKey, checks_part, normalize_code

if TYPE_CHECKING:
    from zantine.engine import Game
    from zantine.rng import GameRng
    from zantine.state import GameState, PlayerState

NAME = "splitkey"
KIND = SplitKey.name
# Terminals are placed at most this many corridors from the entry room when the
# map allows it, so honest holders who leave the entry room together can read,
# return and enter within the default window (2 * 2 - 1 <= 4 - 1).
MAX_TERMINAL_DISTANCE = 2


@dataclass
class Key:
    """Ground truth for one split-key Task. Never shown to players as a whole."""

    task: str
    # The current code: the Task's content with this attempt's fragments,
    # checksums and answer. The Task's own content keeps the first code.
    content: TaskContent
    # The current attempt (1 = the first code); each failure starts the next.
    attempt: int = 1
    # Round the current code's first fragment was read, and the last Round in
    # which entering it counts. None until the first read.
    opened: int | None = None
    deadline: int | None = None
    # Seats that have read their fragment of the current code.
    read_by: list[int] = field(default_factory=list)
    # One record per finished attempt (code, how it ended, when).
    history: list[dict[str, Any]] = field(default_factory=list)
    # Holders as of the last Meeting, to notice reassignments.
    holders: list[int] = field(default_factory=list)


def keys(state: GameState) -> dict[str, Key]:
    return state.ext.get(NAME, {})


def key(state: GameState, task_id: str) -> Key:
    """Ground truth for one split-key Task (analysis and Audit; never for players)."""
    return keys(state)[task_id]


# Actions --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _KeyAction(Action):
    task: str = ""

    def _task(self, state: GameState, actor: PlayerState) -> Task | str:
        b: TaskBoard | None = state.ext.get(TASKS)
        if (
            not isinstance(self.task, str)
            or b is None
            or self.task not in b.graph
            or b.graph.get(self.task).kind != KIND
            or self.task not in keys(state)
        ):
            return f"no split-key Task {self.task!r}"
        task = b.graph.get(self.task)
        if actor.seat not in task.assignees:
            return f"{task.id} is not one of your Tasks"
        if task.done or task.closed:
            return f"{task.id} is already done"
        return task


@dataclass(frozen=True)
class ReadFragment(_KeyAction):
    """Read your fragment of a split-key code (and the checksum of the next part) at your
    terminal room. The first read of a code starts its deadline."""

    kind: ClassVar[str] = "splitkey.read"
    tool_parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"task": {"type": "string", "description": "ID of your split-key Task."}},
        "required": ["task"],
        "additionalProperties": False,
    }

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        found = self._task(state, actor)
        if isinstance(found, str):
            return found
        terminal = found.content.inputs["rooms"]["terminals"][found.assignees.index(actor.seat)]
        if actor.room != terminal:
            return f"your terminal for {found.id} is in {terminal}, not {actor.room}"
        return None


@dataclass(frozen=True)
class EnterCode(_KeyAction):
    """Enter the whole code of a split-key Task in its entry room. A wrong code fails the
    attempt and scrambles the code."""

    kind: ClassVar[str] = "splitkey.enter"
    code: Any = None
    tool_parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "ID of your split-key Task."},
            "code": {
                "type": "string",
                "description": "The whole 9-digit code, part 1 first (e.g. 123456789).",
            },
        },
        "required": ["task", "code"],
        "additionalProperties": False,
    }

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        found = self._task(state, actor)
        if isinstance(found, str):
            return found
        entry = found.content.inputs["rooms"]["entry"]
        if actor.room != entry:
            return f"{found.id}'s code is entered in {entry}, not {actor.room}"
        if normalize_code(self.code) is None:
            return "code must be 9 digits"
        return None


# The module -------------------------------------------------------------------------------


class SplitKeyModule(GameModule):
    name = NAME

    def install(self, rules: Rules) -> None:
        rules.register_action(ReadFragment)
        rules.register_action(EnterCode)
        # After Room Chat (410), so a message said in the Round a code is reset is
        # logged before the reset; before Crisis (500).
        rules.add_resolver(460, "splitkey.resolve", resolve)

    def holder_pool(self, game: Game) -> list[int]:
        """Seats that may hold fragments. Sabotage (#8) may widen this to Impostors."""
        return [p.seat for p in game.state.players if not p.is_impostor]

    def place_tasks(self, game: Game, graph: TaskGraph) -> None:
        """Called by the Tasks module at setup: add ``splitkey_tasks`` split-key Tasks."""
        state, count = game.state, game.config.splitkey_tasks
        pool = self.holder_pool(game)
        if count <= 0 or len(pool) < 3:
            return
        rooms = [r for r in state.map.rooms if r != state.map.meeting_room]
        if len(rooms) < 4:
            rooms = list(state.map.rooms)
        if len(rooms) < 4:
            raise ValueError("split-key Tasks need at least four rooms")
        rng = game.rng.fork(NAME)
        kind = task_kind(KIND)
        assert isinstance(kind, SplitKey)
        load = {s: len(graph.of(s)) for s in pool}
        for task_id in task_ids(rng.fork("ids"), count, exclude=(t.id for t in graph)):
            holders: list[int] = []
            for _part in range(3):
                options = [s for s in pool if s not in holders]
                least = min(load[s] for s in options)
                holders.append(rng.choice([s for s in options if load[s] == least]))
                load[holders[-1]] += 1
            entry, terminals = _pick_rooms(state.map, rooms, rng)
            content = kind.bind(kind.generate(rng.fork(f"content/{task_id}")), entry, terminals)
            graph.add(Task(task_id, entry, holders, content))

    def on_setup(self, game: Game) -> None:
        b: TaskBoard | None = game.state.ext.get(TASKS)
        tasks = [t for t in b.graph if t.kind == KIND] if b is not None else []
        game.state.ext[NAME] = {
            t.id: Key(task=t.id, content=t.content, holders=list(t.assignees)) for t in tasks
        }
        if tasks:
            game.event(
                "splitkey.generated",
                keys=[
                    {
                        "task": t.id,
                        "holders": list(t.assignees),
                        "rooms": t.content.inputs["rooms"],
                        "fragments": list(t.content.inputs["fragments"]),
                        "checksums": list(t.content.inputs["checksums"]),
                        "code": t.content.answer,
                    }
                    for t in tasks
                ],
            )

    def on_meeting_end(self, game: Game, meeting: int) -> None:
        """Brief holders who took over a dead or ejected holder's part."""
        state = game.state
        b = board(state)
        for k in keys(state).values():
            task = b.graph.get(k.task)
            for slot, seat in enumerate(task.assignees):
                previous = k.holders[slot]
                if seat == previous:
                    continue
                game.event("splitkey.holder_replaced", task=k.task, part=slot + 1, seat=seat)
                game.observe(
                    seat,
                    "splitkey.took_over",
                    task=k.task,
                    part=slot + 1,
                    previous=previous,
                    attempt=k.attempt,
                    deadline=k.deadline,
                )
                for other in task.assignees:
                    if other != seat and state.player(other).alive:
                        game.observe(
                            other,
                            "splitkey.holder_replaced",
                            task=k.task,
                            part=slot + 1,
                            previous=previous,
                            new=seat,
                        )
            k.holders = list(task.assignees)
            for seat in task.assignees:
                if state.player(seat).alive and k.task in b.views[seat]:
                    # Replace the cached view; earlier observations stay immutable.
                    b.views[seat][k.task] = {
                        **b.views[seat][k.task],
                        "assignees": list(task.assignees),
                    }

    def context_extra(self, game: Game, seat: int) -> dict[str, Any] | None:
        state = game.state
        if not keys(state):
            return None
        graph = board(state).graph
        mine = [graph.get(t) for t in keys(state) if seat in graph.get(t).assignees]
        if not mine:
            return None
        here = state.player(seat).room
        out = []
        for task in mine:
            part = task.assignees.index(seat) + 1
            rooms = task.content.inputs["rooms"]
            terminal = rooms["terminals"][part - 1]
            out.append(
                {
                    "task": task.id,
                    "part": part,
                    "holders": [{"part": i + 1, "seat": s} for i, s in enumerate(task.assignees)],
                    "terminal": terminal,
                    "terminal_path": route(state.map, here, terminal),
                    "entry": rooms["entry"],
                    "entry_path": route(state.map, here, rooms["entry"]),
                    "checks_part": checks_part(part),
                    **_known(game, seat, task.id),
                }
            )
        return {"keys": out}

    def result_extra(self, game: Game) -> dict[str, Any] | None:
        state = game.state
        if not keys(state):
            return None
        graph = board(state).graph
        return {
            "keys": [
                {
                    "task": k.task,
                    "done": graph.get(k.task).done,
                    "done_round": graph.get(k.task).done_round,
                    "attempts": k.attempt,
                    "history": list(k.history),
                }
                for k in keys(state).values()
            ]
        }


def _known(game: Game, seat: int, task_id: str) -> dict[str, Any]:
    """What ``seat`` has been told about the current attempt, from its observations only."""
    known: dict[str, Any] = {
        "attempt": 1,
        "fragment": None,
        "checksum": None,
        "deadline": None,
        "done": False,
    }
    for o in game.observations_of(seat):
        if not o.type.startswith(f"{NAME}.") or o.data.get("task") != task_id:
            continue
        match o.type:
            case "splitkey.read":
                known.update(fragment=o.data["fragment"], checksum=o.data["checksum"])
            case "splitkey.opened":
                known.update(deadline=o.data["deadline"])
            case "splitkey.reset":
                known.update(attempt=o.data["attempt"], fragment=None, checksum=None, deadline=None)
            case "splitkey.took_over":
                known.update(
                    attempt=o.data["attempt"],
                    fragment=None,
                    checksum=None,
                    deadline=o.data["deadline"],
                )
            case "splitkey.completed":
                known.update(done=True)
    return known


def _pick_rooms(game_map: GameMap, rooms: list[str], rng: GameRng) -> tuple[str, list[str]]:
    """An entry room and three distinct terminal rooms near it, from the seed."""
    dist = {
        (a, b): len(route(game_map, a, b)) for a in rooms for b in rooms if a != b
    }  # route length = corridors walked
    order = rng.shuffled(rooms)
    for entry in order:
        near = [r for r in rooms if r != entry and dist[entry, r] <= MAX_TERMINAL_DISTANCE]
        if len(near) >= 3:
            return entry, rng.sample(near, 3)
    # No room has three others close enough: use the entry whose terminals are nearest.
    best = min(order, key=lambda e: sorted(dist[e, r] for r in rooms if r != e)[2])
    nearest = sorted((r for r in rooms if r != best), key=lambda r: dist[best, r])[:3]
    return best, rng.shuffled(nearest)


def reset(game: Game, task_id: str, reason: str, **data: Any) -> None:
    """Fail the current attempt: a fresh code from the seed, every fragment unread.

    Every living holder is told ``reason`` (and ``data``). Sabotage may call
    this with its own reason.
    """
    state = game.state
    k = key(state, task_id)
    task = board(state).graph.get(task_id)
    if task.done:
        raise ValueError(f"{task_id} is already done")
    k.history.append(
        {
            "attempt": k.attempt,
            "code": k.content.answer,
            "opened": k.opened,
            "read_by": list(k.read_by),
            "result": reason,
            "round": state.round,
        }
    )
    k.attempt += 1
    kind = task_kind(KIND)
    assert isinstance(kind, SplitKey)
    k.content = kind.rekey(k.content, game.rng.fork(f"{NAME}/{task_id}/attempt-{k.attempt}"))
    k.opened = k.deadline = None
    k.read_by = []
    game.event(
        "splitkey.reset",
        task=task_id,
        reason=reason,
        attempt=k.attempt,
        fragments=list(k.content.inputs["fragments"]),
        checksums=list(k.content.inputs["checksums"]),
        code=k.content.answer,
        **data,
    )
    for seat in task.assignees:
        if state.player(seat).alive:
            game.observe(
                seat, "splitkey.reset", task=task_id, reason=reason, attempt=k.attempt, **data
            )


def _blocked(game: Game, rc: RoundContext, task: Task, seat: int) -> bool:
    b = board(game.state)
    done_before = rc.scratch.get("tasks.done_before", set())
    waiting = [r for r in task.requires if r not in done_before]
    if waiting:
        b.stats[seat].blocked += 1
        game.event("tasks.blocked", seat=seat, task=task.id, waiting_on=waiting)
        game.observe(seat, "tasks.blocked", task=task.id, waiting_on=waiting)
    return bool(waiting)


def resolve(game: Game, rc: RoundContext) -> None:
    state = game.state
    all_keys = keys(state)
    if not all_keys:
        return
    b = board(state)
    kind = task_kind(KIND)
    # 1. Entries are graded against the code as it stood at the start of the Round.
    wrong: dict[str, list[str]] = {}
    completed: list[str] = []
    for seat, action in rc.acting(EnterCode):
        task = b.graph.get(action.task)
        k = all_keys[task.id]
        if _blocked(game, rc, task, seat):
            continue
        code = normalize_code(action.code)
        correct = kind.grade(k.content, action.code)
        game.event(
            "splitkey.entered",
            seat=seat,
            task=task.id,
            attempt=k.attempt,
            code=code,
            correct=correct,
        )
        game.event("tasks.graded", seat=seat, task=task.id, correct=correct, fake=task.fake)
        # Private: only the enterer learns its grade.
        game.observe(seat, "tasks.graded", task=task.id, correct=correct)
        if not correct:
            b.stats[seat].wrong += 1
            wrong.setdefault(task.id, []).append(code or "")
        elif not task.done:
            task.done, task.done_by, task.done_round = True, seat, rc.round
            completed.append(task.id)
            # Credit every holder who read a fragment of this code, and the enterer.
            for s in dict.fromkeys([*k.read_by, seat]):
                b.stats[s].done += 1
    # 2. Close the attempt: success, a wrong code, or the deadline.
    for k in all_keys.values():
        task = b.graph.get(k.task)
        if k.task in completed:
            task.closed = True
            k.history.append(
                {
                    "attempt": k.attempt,
                    "code": k.content.answer,
                    "opened": k.opened,
                    "read_by": list(k.read_by),
                    "result": "done",
                    "round": rc.round,
                }
            )
            game.event("splitkey.completed", task=k.task, attempt=k.attempt, seat=task.done_by)
            for seat in task.assignees:
                if state.player(seat).alive:
                    b.known_done[seat].add(k.task)
                    game.observe(seat, "splitkey.completed", task=k.task, attempt=k.attempt)
        elif task.done:
            continue
        elif k.task in wrong:
            reset(game, k.task, "wrong_code", entered=wrong[k.task])
        elif k.deadline is not None and rc.round >= k.deadline:
            reset(game, k.task, "deadline")
    # 3. Reads show the (possibly new) code; the first read starts the deadline.
    for seat, action in rc.acting(ReadFragment):
        task = b.graph.get(action.task)
        k = all_keys[task.id]
        if task.done or _blocked(game, rc, task, seat):
            continue
        slot = task.assignees.index(seat)
        inputs = k.content.inputs
        if seat not in k.read_by:
            k.read_by.append(seat)
        game.event("splitkey.read", seat=seat, task=task.id, attempt=k.attempt, part=slot + 1)
        game.observe(
            seat,
            "splitkey.read",
            task=task.id,
            attempt=k.attempt,
            part=slot + 1,
            fragment=inputs["fragments"][slot],
            checks_part=checks_part(slot + 1),
            checksum=inputs["checksums"][slot],
        )
        if k.opened is None:
            k.opened, k.deadline = rc.round, rc.round + game.config.splitkey_window
            game.event("splitkey.opened", task=task.id, attempt=k.attempt, deadline=k.deadline)
            for holder in task.assignees:
                if state.player(holder).alive:
                    game.observe(
                        holder,
                        "splitkey.opened",
                        task=task.id,
                        attempt=k.attempt,
                        deadline=k.deadline,
                    )
