"""Solo step kinds: the steps of the dependent-solo Task family (the Task Graph).

Each step is easy for one player alone; the difficulty in a game comes from the
dependencies between steps held by different players. Content is generated from
the seed, so there is no fixed answer key.
"""

from __future__ import annotations

import re
from typing import Any

from zantine.rng import GameRng
from zantine.tasks.base import Answer, TaskContent, TaskKind, register_task_kind

_SORT_THEMES = (
    ("Sort the fuel cells", "fuel cell charge levels"),
    ("Order the star charts", "star chart distances"),
    ("Sort the sample tubes", "sample tube weights"),
    ("Rank the shield emitters", "shield emitter outputs"),
    ("Sort the cargo crates", "cargo crate serial numbers"),
)

_LOOKUP_THEMES = (
    ("Look up the shield frequency", "shield frequency"),
    ("Find the reactor access code", "reactor access code"),
    ("Look up the comms channel", "comms channel"),
    ("Find the vent pressure setting", "vent pressure setting"),
    ("Look up the docking clearance", "docking clearance"),
)

_SECTORS = (
    "Alpha",
    "Bravo",
    "Charlie",
    "Delta",
    "Echo",
    "Foxtrot",
    "Golf",
    "Hotel",
    "India",
    "Juliet",
    "Kilo",
    "Lima",
)


@register_task_kind
class SortList(TaskKind):
    """Sort a short list of numbers into ascending order."""

    name = "sort_list"
    size = 6

    def generate(self, rng: GameRng) -> TaskContent:
        title, noun = rng.choice(_SORT_THEMES)
        values = rng.sample(range(10, 1000), self.size)
        return TaskContent(
            kind=self.name,
            title=title,
            instructions=(
                f"Sort these {noun} from smallest to largest. "
                "Answer with the sorted list of numbers."
            ),
            inputs={"values": values},
            answer=sorted(values),
        )

    def grade(self, content: TaskContent, answer: Answer) -> bool:
        return _as_list(answer) == [str(v) for v in content.answer]

    def solve(self, brief: dict[str, Any]) -> Answer:
        return sorted(brief["inputs"]["values"])


@register_task_kind
class LookupValue(TaskKind):
    """Find the value for one key in a small table."""

    name = "lookup_value"
    size = 6

    def generate(self, rng: GameRng) -> TaskContent:
        title, noun = rng.choice(_LOOKUP_THEMES)
        sectors = rng.sample(_SECTORS, self.size)
        codes = rng.sample(range(1000, 10000), self.size)
        key = rng.choice(sectors)
        table = dict(zip(sectors, codes, strict=True))
        return TaskContent(
            kind=self.name,
            title=f"{title} for {key}",
            instructions=(
                f"Find the {noun} for sector {key} in the table. Answer with that value only."
            ),
            inputs={"table": table, "key": key},
            answer=table[key],
        )

    def grade(self, content: TaskContent, answer: Answer) -> bool:
        return _as_scalar(answer) == str(content.answer)

    def solve(self, brief: dict[str, Any]) -> Answer:
        inputs = brief["inputs"]
        return inputs["table"][inputs["key"]]


# Answer normalization: accept the obvious shapes a Harness might produce.


def _as_scalar(answer: Answer) -> str | None:
    if isinstance(answer, bool) or not isinstance(answer, str | int):
        return None
    return str(answer).strip()


def _as_list(answer: Answer) -> list[str] | None:
    if isinstance(answer, str):
        return [p for p in re.split(r"[\s,]+", answer.strip().strip("[]")) if p]
    if isinstance(answer, list | tuple):
        items = [_as_scalar(x) for x in answer]
        return None if None in items else [s for s in items if s is not None]
    return None
