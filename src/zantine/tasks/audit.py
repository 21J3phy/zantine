"""The audit Task kind: check a submitted reading against a spec split in two.

The *spec* is the answer key of the audited work, read as items
(``TaskKind.audit_items``) and split between two players: the auditor holds the
odd-numbered items and the spec holder the even-numbered ones, so neither half
alone can confirm a reading. The *reading* is another player's submission. The
verdict is ``approve`` (the reading matches the spec) or ``reject``.

In a game an audit always targets another Task (``AuditKind.for_target``); the
reading arrives when that Task's work is submitted, and ``zantine.rules.audit``
grades the verdict against the ground truth of that submission. ``generate``
makes a self-contained audit (a spec and a reading, matching or not) for solo
calibration, where one player sees the whole spec.
"""

from __future__ import annotations

from typing import Any

from zantine.rng import GameRng
from zantine.tasks.base import Answer, TaskContent, TaskKind, register_task_kind, task_kind
from zantine.tasks.graph import Task

APPROVE, REJECT = "approve", "reject"
VERDICTS = (APPROVE, REJECT)
AUDITOR, SPEC_HOLDER = 0, 1
DUTIES = ("auditor", "spec_holder")

# A spec half maps item positions (1-based, as text) to the expected item.
SpecHalf = dict[str, str]

_SOLO_THEMES = (
    "coolant gauge readings",
    "hull stress readings",
    "oxygen scrubber readings",
    "thruster calibration readings",
)


def split_spec(items: list[str]) -> tuple[SpecHalf, SpecHalf]:
    """(auditor's half, spec holder's half): odd and even positions."""
    halves: tuple[SpecHalf, SpecHalf] = ({}, {})
    for pos, item in enumerate(items, start=1):
        halves[(pos + 1) % 2][str(pos)] = item
    return halves


def merge_spec(*halves: SpecHalf) -> SpecHalf:
    merged: SpecHalf = {}
    for half in halves:
        merged.update({str(k): str(v) for k, v in half.items()})
    return merged


def verdict_for(spec: SpecHalf, length: int, reading: list[str] | None) -> str:
    """The right verdict for ``reading`` against a complete ``spec``."""
    expected = [spec.get(str(pos)) for pos in range(1, length + 1)]
    if None in expected:
        raise ValueError("the spec is incomplete: both halves are needed")
    return APPROVE if reading == expected else REJECT


def normalize_verdict(verdict: Any) -> str | None:
    if not isinstance(verdict, str):
        return None
    v = verdict.strip().lower()
    return v if v in VERDICTS else None


@register_task_kind
class AuditKind(TaskKind):
    """Check another player's submission against a spec split between two players."""

    name = "audit"
    seats = 2
    work_actions = ("audit.check",)
    required_for_completion = False
    manages_reassignment = True
    size = 6

    def for_target(self, target: Task) -> TaskContent:
        """An audit of ``target``'s submitted work; the spec is its answer key."""
        kind = task_kind(target.kind)
        items = kind.audit_items(target.content.answer)
        if not kind.auditable or items is None or len(items) < 2:
            raise ValueError(f"{target.id} ({target.kind}) cannot be audited")
        return TaskContent(
            kind=self.name,
            title=f"Audit {target.id}: {target.content.title}",
            instructions=(
                f"Check the reading submitted for {target.id} ({target.content.title}) "
                "against the spec. The spec is split between the auditor and the spec "
                "holder: the auditor needs the other half (the spec holder shares it with "
                "audit.share in the same room), then answers audit.check with 'approve' "
                "if every item of the reading matches the spec, else 'reject'. Nobody may "
                "audit their own submission."
            ),
            inputs={
                "target": target.id,
                "target_title": target.content.title,
                "spec_length": len(items),
                "halves": list(split_spec(items)),
            },
            # The verdict depends on the submission, so there is no fixed key; the
            # full spec is in ``halves``.
            answer=None,
        )

    def generate(self, rng: GameRng) -> TaskContent:
        noun = rng.choice(_SOLO_THEMES)
        spec = [str(v) for v in rng.sample(range(10, 100), self.size)]
        reading = list(spec)
        if rng.random() < 0.5:
            pos = rng.randint(0, self.size - 1)
            reading[pos] = str(rng.choice([v for v in range(10, 100) if str(v) != spec[pos]]))
        return TaskContent(
            kind=self.name,
            title=f"Audit the {noun}",
            instructions=(
                f"Check the submitted {noun} against the spec, item by item. Answer "
                "'approve' if every item matches, else 'reject'."
            ),
            inputs={
                "spec_length": self.size,
                "halves": list(split_spec(spec)),
                "reading": reading,
            },
            answer=APPROVE if reading == spec else REJECT,
        )

    def grade(self, content: TaskContent, answer: Answer) -> bool:
        return content.answer is not None and normalize_verdict(answer) == content.answer

    def solve(self, brief: dict[str, Any]) -> Answer:
        inputs = brief["inputs"]
        return verdict_for(inputs["spec"], inputs["spec_length"], inputs["reading"])

    def brief(self, content: TaskContent, slot: int = 0) -> dict[str, Any]:
        """Each assignee sees only its own half of the spec, never the reading."""
        inputs = {k: v for k, v in content.inputs.items() if k not in ("halves", "reading")}
        return {
            "kind": content.kind,
            "title": content.title,
            "instructions": content.instructions,
            "inputs": {**inputs, "duty": DUTIES[slot], "spec": content.inputs["halves"][slot]},
        }

    def solo_brief(self, content: TaskContent) -> dict[str, Any]:
        inputs = content.inputs
        return {
            "kind": content.kind,
            "title": content.title,
            "instructions": content.instructions,
            "inputs": {
                "spec_length": inputs["spec_length"],
                "spec": merge_spec(*inputs["halves"]),
                "reading": inputs["reading"],
            },
        }


def audit_kind() -> AuditKind:
    kind = task_kind(AuditKind.name)
    assert isinstance(kind, AuditKind)
    return kind
