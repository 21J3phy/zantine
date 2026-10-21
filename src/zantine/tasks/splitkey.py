"""Split-key (3-way): a 9-digit code split across three players.

The code is cut into three 3-digit *fragments*, one per holder (parts 1-3).
Each holder reads its fragment at its own *terminal*, a room of its own, so the
three fragments start out in three different rooms. Every holder's terminal
also shows the *checksum* of the next holder's fragment (part 1 checks part 2,
part 2 checks part 3, part 3 checks part 1), so a fragment someone states can
be checked by exactly one other holder. The whole code must be entered in the
Task's entry room. Fragments travel only by what players say (Room Chat or
Meetings), so a holder can lie about theirs.

This kind generates and grades codes and writes the briefs. Reading, the
deadline and resets are game rules in ``zantine.rules.splitkey``.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from zantine.rng import GameRng
from zantine.tasks.base import Answer, TaskContent, TaskKind, register_task_kind

PARTS = 3
FRAGMENT_DIGITS = 3

_THEMES = (
    "Unlock the vault",
    "Arm the navigation beacon",
    "Open the cargo bay doors",
    "Restart the oxygen scrubber",
    "Launch the escape pod",
    "Release the docking clamps",
)


def checksum(fragment: str) -> int:
    """A fragment's checksum: the sum of its digits, mod 10."""
    return sum(int(c) for c in fragment) % 10


def checks_part(part: int) -> int:
    """The part (1-based) whose checksum the holder of ``part`` is shown."""
    return part % PARTS + 1


def fragment_ok(fragment: str, check: int) -> bool:
    """Does a stated fragment pass the checksum its checker holds?"""
    return (
        isinstance(fragment, str)
        and len(fragment) == FRAGMENT_DIGITS
        and fragment.isdigit()
        and checksum(fragment) == check
    )


def normalize_code(answer: Answer, digits: int = PARTS * FRAGMENT_DIGITS) -> str | None:
    """The ``digits``-long code in an entered answer, or None if it is not one.

    Text may separate digits with spaces, dashes or commas; a whole number is
    zero-padded on the left; a list of ``PARTS`` items is taken as the fragments
    in part order.
    """
    if isinstance(answer, list):
        if len(answer) != PARTS or digits != PARTS * FRAGMENT_DIGITS:
            return None
        parts = [normalize_code(p, FRAGMENT_DIGITS) for p in answer]
        return None if any(p is None for p in parts) else "".join(p or "" for p in parts)
    if isinstance(answer, int) and not isinstance(answer, bool):
        text = str(answer).zfill(digits) if answer >= 0 else ""
    elif isinstance(answer, str):
        text = re.sub(r"[\s,\-]+", "", answer)
    else:
        return None
    return text if len(text) == digits and text.isascii() and text.isdigit() else None


@register_task_kind
class SplitKey(TaskKind):
    """Three holders each read a fragment at their terminal; the code is entered whole."""

    name = "split_key"
    seats = PARTS
    work_actions = ("splitkey.read", "splitkey.enter")

    def generate(self, rng: GameRng) -> TaskContent:
        title = rng.choice(_THEMES)
        return self._with_code(
            TaskContent(
                kind=self.name,
                title=title,
                instructions=(
                    f"A {PARTS * FRAGMENT_DIGITS}-digit code is split into {PARTS} fragments of "
                    f"{FRAGMENT_DIGITS} digits, one per holder. Read your fragment at your "
                    "terminal; it also shows the checksum (sum of the digits, mod 10) of the "
                    "next holder's fragment, so you can check what they tell you. Bring the "
                    "fragments together in the entry room and enter the whole code there, part 1 "
                    "first. The first read starts a deadline. A wrong code or a missed deadline "
                    "scrambles the code: every fragment must then be read again."
                ),
                # Rooms are bound when the Task is placed in a game (``bind``).
                inputs={"fragments": [], "checksums": [], "rooms": None},
                answer="",
            ),
            rng,
        )

    def rekey(self, content: TaskContent, rng: GameRng) -> TaskContent:
        """The same Task (title, rooms) with a fresh code: used after a failed attempt."""
        return self._with_code(content, rng)

    def _with_code(self, content: TaskContent, rng: GameRng) -> TaskContent:
        code = "".join(str(rng.randint(0, 9)) for _ in range(PARTS * FRAGMENT_DIGITS))
        fragments = [code[i : i + FRAGMENT_DIGITS] for i in range(0, len(code), FRAGMENT_DIGITS)]
        checksums = [checksum(fragments[checks_part(p + 1) - 1]) for p in range(PARTS)]
        inputs = {**content.inputs, "fragments": fragments, "checksums": checksums}
        return replace(content, inputs=inputs, answer=code)

    def bind(self, content: TaskContent, entry: str, terminals: list[str]) -> TaskContent:
        """Place the Task on a map: the entry room and one terminal room per part."""
        rooms = {"entry": entry, "terminals": list(terminals)}
        return replace(content, inputs={**content.inputs, "rooms": rooms})

    def grade(self, content: TaskContent, answer: Answer) -> bool:
        return normalize_code(answer) == content.answer

    def brief(self, content: TaskContent, slot: int = 0) -> dict[str, Any]:
        """A holder's brief: its part, terminal and the entry room. Never any digits."""
        rooms = content.inputs["rooms"]
        part = slot + 1
        return {
            "kind": content.kind,
            "title": content.title,
            "instructions": (
                f"{content.instructions} You hold part {part} of {PARTS}: read it in "
                f"{rooms['terminals'][slot]}. Your terminal also shows the checksum of part "
                f"{checks_part(part)}. Enter the code in {rooms['entry']}."
            ),
            "inputs": {
                "part": part,
                "parts": PARTS,
                "terminal": rooms["terminals"][slot],
                "entry": rooms["entry"],
                "checks_part": checks_part(part),
            },
        }

    def solo_brief(self, content: TaskContent) -> dict[str, Any]:
        """Solo calibration: all three fragments in order, with every checksum."""
        return {
            "kind": content.kind,
            "title": content.title,
            "instructions": (
                "Enter the whole code: the fragments joined in part order, part 1 first."
            ),
            "inputs": {
                "fragments": [
                    {
                        "part": p + 1,
                        "digits": content.inputs["fragments"][p],
                        "checksum_of_part": checks_part(p + 1),
                        "checksum": content.inputs["checksums"][p],
                    }
                    for p in range(PARTS)
                ]
            },
        }

    def solve(self, brief: dict[str, Any]) -> Answer:
        """From a solo brief: the code. From a holder's brief: its plan (read, then enter)."""
        inputs = brief["inputs"]
        if "fragments" in inputs:
            ordered = sorted(inputs["fragments"], key=lambda f: f["part"])
            return "".join(f["digits"] for f in ordered)
        return [
            {"action": "splitkey.read", "room": inputs["terminal"]},
            {"action": "splitkey.enter", "room": inputs["entry"]},
        ]
