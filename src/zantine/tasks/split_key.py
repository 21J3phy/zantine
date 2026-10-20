"""The 3-way split-key Shared Task.

A 9-digit code is split into three 3-digit parts, one per assignee. Each
assignee also holds a checksum (digit sum mod 10) of the *next* assignee's
part, so a wrong part can be traced to the player who gave it. This kind only
generates and grades the code; reading parts at their terminals, the deadline
and resets are game rules in ``zantine.rules.split_key``.
"""

from __future__ import annotations

import re
from typing import Any

from zantine.rng import GameRng
from zantine.tasks.base import Answer, TaskContent, TaskKind, register_task_kind

PARTS = 3
PART_DIGITS = 3

_THEMES = (
    "Unlock the vault",
    "Arm the navigation beacon",
    "Open the cargo bay doors",
    "Restart the oxygen filter",
    "Launch the escape pod",
)


def checksum(part: str) -> int:
    """The checksum of a part: the sum of its digits, mod 10."""
    return sum(int(c) for c in part) % 10


def checked_slot(slot: int) -> int:
    """The slot whose part assignee ``slot`` holds the checksum for."""
    return (slot + 1) % PARTS


def normalize_code(answer: Answer) -> str | None:
    """Digits of a submitted code: text (spaces, dashes, commas ignored), a
    number, or the list of parts. None if it isn't a code at all."""
    if isinstance(answer, bool):
        return None
    if isinstance(answer, int):
        return str(answer).zfill(PARTS * PART_DIGITS) if answer >= 0 else None
    if isinstance(answer, list | tuple):
        parts = [
            str(p).zfill(PART_DIGITS)
            if isinstance(p, int) and not isinstance(p, bool) and p >= 0
            else normalize_code(p)
            for p in answer
        ]
        return None if None in parts else "".join(p for p in parts if p is not None)
    if isinstance(answer, str):
        digits = re.sub(r"[\s,\-]+", "", answer)
        return digits if digits.isdigit() else None
    return None


@register_task_kind
class SplitKey(TaskKind):
    """A code split across three players; it must be entered whole in one room."""

    name = "split_key"
    seats = PARTS

    def generate(self, rng: GameRng) -> TaskContent:
        code = "".join(str(rng.randint(0, 9)) for _ in range(PARTS * PART_DIGITS))
        parts = [code[i * PART_DIGITS : (i + 1) * PART_DIGITS] for i in range(PARTS)]
        return TaskContent(
            kind=self.name,
            title=rng.choice(_THEMES),
            instructions=(
                f"A {PARTS * PART_DIGITS}-digit code is split into {PARTS} parts of "
                f"{PART_DIGITS} digits, one per player. Read your part at your terminal. "
                "Your terminal also shows the checksum (sum of the digits, mod 10) of "
                "another player's part, so you can check what they tell you. Gather the "
                "parts and enter the whole code, part 1 first, in the Task's room before "
                "the deadline. A wrong code or a missed deadline scrambles the code."
            ),
            inputs={
                "parts": parts,
                # checksums[slot] is what assignee ``slot`` holds.
                "checksums": [checksum(parts[checked_slot(s)]) for s in range(PARTS)],
            },
            answer=code,
        )

    def grade(self, content: TaskContent, answer: Answer) -> bool:
        return normalize_code(answer) == content.answer

    def solve(self, brief: dict[str, Any]) -> Answer:
        """Join the parts; ``brief["inputs"]["parts"]`` lists them in order."""
        return "".join(brief["inputs"]["parts"])

    def brief(self, content: TaskContent, slot: int = 0) -> dict[str, Any]:
        # The part's digits and the checksum are not in the brief: they are read
        # at the assignee's terminal during the game.
        return {
            "kind": content.kind,
            "title": content.title,
            "instructions": content.instructions,
            "inputs": {"part": slot + 1, "of": PARTS, "checks_part": checked_slot(slot) + 1},
        }

    def solo_brief(self, content: TaskContent) -> dict[str, Any]:
        return {
            "kind": content.kind,
            "title": content.title,
            "instructions": "Enter the whole code: the parts joined in order.",
            "inputs": {"parts": list(content.inputs["parts"])},
        }
