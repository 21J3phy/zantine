"""Run one Task alone, outside a game (solo calibration of Task kinds).

A solo trial generates a fresh instance of a kind from a seed, shows the
``solo_brief`` to a solver (a Model Harness, a scripted solver, a human) and
grades the answer exactly as a game would. No game, rooms or other players.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from zantine.rng import GameRng
from zantine.tasks.base import Answer, Solver, task_kind


@dataclass(frozen=True)
class TrialResult:
    kind: str
    seed: int
    brief: dict[str, Any]
    answer: Answer
    expected: Answer
    correct: bool


def run_solo_trial(kind: str, seed: int, solver: Solver) -> TrialResult:
    k = task_kind(kind)
    content = k.generate(GameRng(seed).fork(f"solo/{kind}"))
    brief = k.solo_brief(content)
    answer = solver(brief)
    return TrialResult(kind, seed, brief, answer, content.answer, k.grade(content, answer))


def run_solo_trials(kind: str, seeds: Iterable[int], solver: Solver) -> list[TrialResult]:
    return [run_solo_trial(kind, seed, solver) for seed in seeds]
