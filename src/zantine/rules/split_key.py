"""Split-key: game rules for the 3-way split-key Shared Task.

Each assignee reads its part of the code (and a checksum of another
assignee's part) at its own terminal room with ``split_key.read``. The first
read opens a window: the whole code must be entered with ``tasks.work`` in the
Task's room within ``split_key_window`` Rounds. A wrong code or a missed
deadline fails the attempt: the code is scrambled (a new one is generated from
the seed), every part must be read again, and the assignees are told. Parts
travel only by what players say (Room Chat, Meetings), so a holder can lie;
the checksums let the crew tell which part was wrong. Rules are documented in
``docs/rules.md``; keep the two in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from zantine.rules.actions import Action
from zantine.rules.registry import GameModule, RoundContext, Rules
from zantine.rules.tasks import GRADED, board, route
from zantine.tasks import Task, task_kind
from zantine.tasks.split_key import SplitKey, checked_slot

if TYPE_CHECKING:
    from zantine.engine import Game
    from zantine.state import GameState, PlayerState

NAME = "split_key"


@dataclass
class KeyState:
    """Per-Task state, in ``GameState.ext["split_key"][task_id]``."""

    # Codes generated so far minus one (resets).
    attempt: int = 0
    # The window: Round the first part of the current code was read, and the
    # last Round in which entering the code counts.
    opened: int | None = None
    deadline: int | None = None
    # Seats that have read their part of the current code.
    read: set[int] = field(default_factory=set)
    resets: list[dict[str, Any]] = field(default_factory=list)


def key_state(state: GameState, task_id: str) -> KeyState:
    return state.ext.setdefault(NAME, {}).setdefault(task_id, KeyState())


def split_key_tasks(state: GameState) -> list[Task]:
    b = state.ext.get("tasks")
    return [t for t in b.graph if t.kind == SplitKey.name] if b is not None else []


@dataclass(frozen=True)
class ReadPart(Action):
    """Read your part of a split-key code at your terminal room."""

    kind: ClassVar[str] = "split_key.read"
    task: str = ""

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        b = state.ext.get("tasks")
        if b is None or self.task not in b.graph or b.graph.get(self.task).kind != SplitKey.name:
            return f"no split-key Task {self.task!r}"
        task = b.graph.get(self.task)
        if actor.seat not in task.assignees:
            return f"{task.id} is not one of your Tasks"
        if task.done:
            return f"{task.id} is already done"
        room = task.slot_room(actor.seat)
        if actor.room != room:
            return f"your part of {task.id} is read in {room}, not {actor.room}"
        return None


class SplitKeyModule(GameModule):
    name = NAME

    def install(self, rules: Rules) -> None:
        rules.register_action(ReadPart)
        # After tasks.work (400), so this Round's entries are graded; resets
        # come before reads so a part read this Round is of the current code.
        rules.add_resolver(410, "split_key.fail", fail)
        rules.add_resolver(420, "split_key.read", read)

    def on_setup(self, game: Game) -> None:
        for task in split_key_tasks(game.state):
            key_state(game.state, task.id)

    def context_extra(self, game: Game, seat: int) -> dict[str, Any] | None:
        state = game.state
        room = state.player(seat).room
        keys = []
        for task in split_key_tasks(state):
            if seat not in task.assignees or task.done:
                continue
            key = key_state(state, task.id)
            slot = task.assignees.index(seat)
            checks = checked_slot(slot)
            known = seat in key.read
            inputs = task.content.inputs
            keys.append(
                {
                    "task": task.id,
                    "part": slot + 1,
                    "part_room": task.slot_room(seat),
                    "part_path": route(state.map, room, task.slot_room(seat)),
                    "entry_room": task.room,
                    "entry_path": route(state.map, room, task.room),
                    "holders": [{"part": i + 1, "seat": s} for i, s in enumerate(task.assignees)],
                    # Your part and checksum, once read (None after a reset).
                    "digits": inputs["parts"][slot] if known else None,
                    "checks_part": checks + 1,
                    "checksum": inputs["checksums"][slot] if known else None,
                    "deadline": key.deadline,
                    "window": game.config.split_key_window,
                }
            )
        return {"tasks": keys} if keys else None

    def result_extra(self, game: Game) -> dict[str, Any] | None:
        tasks = split_key_tasks(game.state)
        if not tasks:
            return None
        return {
            "tasks": [
                {
                    "task": t.id,
                    "done": t.done,
                    "done_round": t.done_round,
                    "resets": key_state(game.state, t.id).resets,
                }
                for t in tasks
            ]
        }


def fail(game: Game, rc: RoundContext) -> None:
    """Scramble the code after a wrong entry or a missed deadline."""
    wrong = {task_id for _, task_id, correct in rc.scratch.get(GRADED, []) if not correct}
    for task in split_key_tasks(game.state):
        if task.done:
            continue
        key = key_state(game.state, task.id)
        if task.id in wrong:
            reset(game, task, "wrong_code")
        elif key.deadline is not None and rc.round >= key.deadline:
            reset(game, task, "deadline")


def reset(game: Game, task: Task, reason: str) -> None:
    """Fail the current attempt: new code from the seed, window closed, parts unread.

    Sabotage modules may call this too (with their own ``reason``).
    """
    key = key_state(game.state, task.id)
    key.attempt += 1
    task.content = task_kind(task.kind).generate(game.rng.fork(f"{NAME}/{task.id}/{key.attempt}"))
    key.opened = key.deadline = None
    key.read.clear()
    key.resets.append({"round": game.state.round, "reason": reason})
    game.event(
        "split_key.reset",
        task=task.id,
        reason=reason,
        attempt=key.attempt,
        inputs=task.content.inputs,
        answer=task.content.answer,
    )
    for seat in task.assignees:
        if game.state.player(seat).alive:
            game.observe(seat, "split_key.reset", task=task.id, reason=reason)


def read(game: Game, rc: RoundContext) -> None:
    state = game.state
    for seat, action in rc.acting(ReadPart):
        task = board(state).graph.get(action.task)
        key = key_state(state, task.id)
        slot = task.assignees.index(seat)
        inputs = task.content.inputs
        key.read.add(seat)
        game.event("split_key.read", seat=seat, task=task.id, part=slot + 1)
        game.observe(
            seat,
            "split_key.part",
            task=task.id,
            part=slot + 1,
            digits=inputs["parts"][slot],
            checks_part=checked_slot(slot) + 1,
            checksum=inputs["checksums"][slot],
        )
        if key.opened is None:
            deadline = rc.round + game.config.split_key_window
            key.opened, key.deadline = rc.round, deadline
            game.event("split_key.window_opened", task=task.id, deadline=deadline)
            for holder in task.assignees:
                if state.player(holder).alive:
                    game.observe(holder, "split_key.window_opened", task=task.id, deadline=deadline)
