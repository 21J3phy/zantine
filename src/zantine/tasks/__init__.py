"""Tasks: Task kinds (seeded content + private grading), the Task Graph, solo trials.

Game rules for Tasks live in ``zantine.rules.tasks``. Importing this package
registers the built-in kinds.
"""

from zantine.tasks import audit as _audit  # noqa: F401  (registers built-in kinds)
from zantine.tasks import relay as _relay  # noqa: F401
from zantine.tasks import splitkey as _splitkey  # noqa: F401
from zantine.tasks import steps as _steps  # noqa: F401
from zantine.tasks.base import (
    Answer,
    Solver,
    TaskContent,
    TaskKind,
    register_task_kind,
    task_kind,
    task_kind_names,
)
from zantine.tasks.graph import Task, TaskGraph, build_task_graph
from zantine.tasks.trial import TrialResult, run_solo_trial, run_solo_trials

__all__ = [
    "Answer",
    "Solver",
    "Task",
    "TaskContent",
    "TaskGraph",
    "TaskKind",
    "TrialResult",
    "build_task_graph",
    "register_task_kind",
    "run_solo_trial",
    "run_solo_trials",
    "task_kind",
    "task_kind_names",
]
