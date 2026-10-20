"""Task instances in a game and the crew's Task Graph.

A ``Task`` is one ``TaskContent`` placed in a game: an id, a room, its
assignees, and the Tasks it ``requires`` (the Task Graph's edges). A Task can
only be worked once every prerequisite is truly complete (graded correct), not
merely claimed done.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from zantine.rng import GameRng
from zantine.tasks.base import TaskContent, task_kind

# Longest chain of dependent steps the generator builds (keeps games finishable).
MAX_CHAIN = 3
# Task ids are drawn from T10..T99 regardless of how many Tasks there are, so an
# id never hints at the size of the crew (Hidden Impostor Count).
TASK_ID_RANGE = range(10, 100)


@dataclass
class Task:
    id: str
    room: str
    # Current assignees in slot order (they change if a Task is reassigned).
    assignees: list[int]
    content: TaskContent
    requires: tuple[str, ...] = ()
    # Hook for Fake Tasks: shown like a real Task but never counts toward
    # Productivity or the crew win.
    fake: bool = False
    done: bool = False
    done_by: int | None = None
    done_round: int | None = None

    @property
    def kind(self) -> str:
        return self.content.kind

    def to_record(self) -> dict[str, Any]:
        """Ground truth for the transcript (includes the answer key)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.content.title,
            "room": self.room,
            "assignees": list(self.assignees),
            "requires": list(self.requires),
            "fake": self.fake,
            "inputs": self.content.inputs,
            "answer": self.content.answer,
        }


class TaskGraph:
    """All Tasks of one game, in generation order, with their dependencies."""

    def __init__(self, tasks: Iterable[Task]) -> None:
        self.tasks: dict[str, Task] = {}
        for task in tasks:
            if task.id in self.tasks:
                raise ValueError(f"duplicate task id {task.id!r}")
            for req in task.requires:
                # Prerequisites must come first, which also rules out cycles.
                if req not in self.tasks:
                    raise ValueError(f"{task.id} requires unknown or later task {req!r}")
            self.tasks[task.id] = task

    def __iter__(self) -> Iterator[Task]:
        return iter(self.tasks.values())

    def __len__(self) -> int:
        return len(self.tasks)

    def __contains__(self, task_id: object) -> bool:
        return task_id in self.tasks

    def get(self, task_id: str) -> Task:
        return self.tasks[task_id]

    def of(self, seat: int) -> list[Task]:
        """Tasks currently assigned to ``seat``."""
        return [t for t in self if seat in t.assignees]

    def waiting_on(self, task_id: str) -> list[str]:
        """Prerequisites of ``task_id`` that are not truly complete yet."""
        return [r for r in self.get(task_id).requires if not self.get(r).done]

    def dependents(self, task_id: str) -> list[str]:
        """Tasks that list ``task_id`` as a prerequisite."""
        return [t.id for t in self if task_id in t.requires]

    def counted(self) -> list[Task]:
        """Tasks that count toward Productivity and the crew win (not fake)."""
        return [t for t in self if not t.fake]

    def all_done(self) -> bool:
        counted = self.counted()
        return bool(counted) and all(t.done for t in counted)


def build_task_graph(
    rng: GameRng,
    crew: Sequence[int],
    rooms: Sequence[str],
    kinds: Sequence[str],
    per_crewmate: int,
    dependency_chance: float,
) -> TaskGraph:
    """Generate the crew's Tasks and Task Graph from the seed.

    Every Crewmate gets ``per_crewmate`` solo steps, each of a kind drawn from
    ``kinds``, in a room drawn from ``rooms``, with an id drawn from
    ``TASK_ID_RANGE`` (see ``task_ids``). Steps are put in a random order;
    each step then requires, with probability ``dependency_chance``, one earlier
    step held by a *different* Crewmate (so steps unlock other players' steps),
    keeping every chain at most ``MAX_CHAIN`` steps long.
    """
    for name in kinds:
        if task_kind(name).seats != 1:
            raise ValueError(f"task kind {name!r} is not a solo step")
    if not kinds or not rooms:
        raise ValueError("need at least one task kind and one room")
    slots = [seat for seat in crew for _ in range(per_crewmate)]
    rng.shuffle(slots)
    ids = task_ids(rng.fork("ids"), len(slots))
    tasks: list[Task] = []
    depth: dict[str, int] = {}
    for task_id, seat in zip(ids, slots, strict=True):
        kind = task_kind(rng.choice(kinds))
        content = kind.generate(rng.fork(f"content/{task_id}"))
        candidates = [
            t.id for t in tasks if seat not in t.assignees and depth[t.id] < MAX_CHAIN - 1
        ]
        requires: tuple[str, ...] = ()
        if candidates and rng.random() < dependency_chance:
            requires = (rng.choice(candidates),)
        depth[task_id] = 1 + max((depth[r] for r in requires), default=-1)
        tasks.append(Task(task_id, rng.choice(rooms), [seat], content, requires))
    return TaskGraph(tasks)


def task_ids(rng: GameRng, n: int) -> list[str]:
    """``n`` distinct ids drawn from ``TASK_ID_RANGE`` (e.g. ``T47``) in a seeded order.

    Numbering Tasks 1..n would let a player who holds ``T19`` infer that there
    are at least 19 Tasks, and so how many Crewmates (and Impostors) there are.
    """
    if n > len(TASK_ID_RANGE):
        raise ValueError(f"at most {len(TASK_ID_RANGE)} Tasks per game, not {n}")
    return [f"T{i}" for i in rng.sample(TASK_ID_RANGE, n)]
