"""The Task-kind abstraction and its registry.

A ``TaskKind`` knows how to make one piece of real work from a seed, what the
assignee is shown (its *brief*), and how to grade an answer. It knows nothing
about rooms, Rounds or the Task Graph: placing Tasks in a game is the job of
``zantine.tasks.graph`` and ``zantine.rules.tasks``, and running one Task outside
a game is the job of ``zantine.tasks.trial``.

Register a new kind with ``@register_task_kind``; it then works in games (if
listed in ``GameConfig.task_kinds``) and in solo trials.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from zantine.rng import GameRng

# JSON-safe answer: what a seat submits and what the answer key holds.
Answer = Any


@dataclass(frozen=True)
class TaskContent:
    """One generated piece of work. ``answer`` is the hidden answer key."""

    kind: str
    title: str
    instructions: str
    # Puzzle input the assignee sees (JSON-safe).
    inputs: dict[str, Any]
    # Never shown to any player; only the grader reads it.
    answer: Answer


class TaskKind(ABC):
    """A kind of Task: seeded generation, the assignee's brief, private grading."""

    name: ClassVar[str] = ""
    # How many players a Task of this kind is assigned to (Shared Tasks > 1).
    seats: ClassVar[int] = 1
    # Action kinds that work a Task of this kind. Kinds with their own actions
    # (e.g. a relay's handoffs) leave out ``tasks.work``, which then rejects them.
    work_actions: ClassVar[tuple[str, ...]] = ("tasks.work",)
    # Optional Task kinds do not gate the crew win or team completion count.
    required_for_completion: ClassVar[bool] = True
    # Kinds with extra independence constraints handle Meeting reassignment in
    # their own GameModule instead of the generic TasksModule hook.
    manages_reassignment: ClassVar[bool] = False
    # Whether another player can audit a submission of this kind against its
    # answer key (``zantine.rules.audit``). Needs ``audit_items``, and the
    # kind's submissions must be graded through ``tasks.work`` (or reported the
    # same way, see ``zantine.rules.tasks.GRADED``).
    auditable: ClassVar[bool] = False

    @abstractmethod
    def generate(self, rng: GameRng) -> TaskContent:
        """Make fresh content (and its answer key) from ``rng`` alone."""

    @abstractmethod
    def grade(self, content: TaskContent, answer: Answer) -> bool:
        """True if ``answer`` completes the Task. Grading is private."""

    @abstractmethod
    def solve(self, brief: dict[str, Any]) -> Answer:
        """Reference solution computed from the brief only (scripted bots, tests)."""

    def brief(self, content: TaskContent, slot: int = 0) -> dict[str, Any]:
        """What assignee number ``slot`` is shown. Never includes the answer key.

        Kinds whose assignees hold different information (split-key) override this.
        """
        return {
            "kind": content.kind,
            "title": content.title,
            "instructions": content.instructions,
            "inputs": content.inputs,
        }

    def solo_brief(self, content: TaskContent) -> dict[str, Any]:
        """The brief for one player doing the whole Task alone (calibration)."""
        return self.brief(content)

    def audit_items(self, answer: Answer) -> list[str] | None:
        """``answer`` (a submission or the answer key) as items an auditor compares.

        A submission passes an audit exactly when its items equal the answer
        key's, so overrides must agree with ``grade``. Default: a list's
        elements, or the characters of a text or number; None if unreadable.
        """
        if isinstance(answer, list | tuple):
            return [str(x).strip() for x in answer]
        if isinstance(answer, str | int) and not isinstance(answer, bool):
            return list(str(answer).strip())
        return None


_KINDS: dict[str, TaskKind] = {}


def register_task_kind[K: type[TaskKind]](cls: K) -> K:
    """Class decorator: make a ``TaskKind`` available by its ``name``."""
    if not cls.name or cls.name in _KINDS:
        raise ValueError(f"bad or duplicate task kind {cls.name!r}")
    _KINDS[cls.name] = cls()
    return cls


def task_kind(name: str) -> TaskKind:
    if name not in _KINDS:
        raise ValueError(f"unknown task kind {name!r}; known: {', '.join(task_kind_names())}")
    return _KINDS[name]


def task_kind_names() -> list[str]:
    return sorted(_KINDS)


Solver = Callable[[dict[str, Any]], Answer]
