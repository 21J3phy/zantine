"""The Solo Harness: one standalone Task for one Model, outside any game (solo calibration).

Solo calibration asks whether a Model can do a Task kind *alone*, so that low
Productivity in games reflects teamwork, not raw ability. This Harness is kept
separate from the Neutral Harness (``harness/neutral.py``), which it never
changes, but reuses its wording and structure where they apply, so solo
performance is comparable to in-game work:

- The brief is rendered exactly as a game renders a Seat's Task (one JSON
  object, keys sorted), without the game-only fields (room, path, Task Graph).
- The one tool, ``submit_answer``, takes ``answer`` with the very schema of the
  in-game ``tasks.work`` tool's ``answer`` field (text, a number, or a list).
- Replies are parsed with the Neutral Harness's rules: exactly one tool call,
  refusals and truncated replies are unusable. Like the engine's Invalid Action
  rule, an unusable reply gets one retry with the reason; a second unusable
  reply is a failed trial, with the reason recorded. A wrong answer is never
  retried (grading is private, as in a game).

Every call goes through a ``Ledger`` with ``purpose="calibration"`` and
``phase=<task kind>``, so the spend cap covers calibration too. An optional
journal hears about each call as it starts (with its worst-case cost) and as it
returns (with its record and parsed outcome), so a crash never loses a charge,
and ``history`` resumes a trial whose first reply was already rejected.

Bump ``SOLO_HARNESS_VERSION`` whenever anything a Model sees changes; results
recorded under another version are not reused.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from zantine.harness.ledger import CallRecord, Ledger
from zantine.harness.neutral import action_tool
from zantine.harness.provider import (
    HarnessRequest,
    ProviderAdapter,
    ProviderError,
    ProviderResponse,
    ToolSpec,
)
from zantine.rules.tasks import WorkOnTask
from zantine.tasks.base import Answer

SOLO_HARNESS_NAME = "solo"
SOLO_HARNESS_VERSION = 1
PURPOSE = "calibration"
# The engine's Invalid Action rule: one retry, then the decision is lost.
MAX_ATTEMPTS = 2

SOLO_SYSTEM_PROMPT = """\
You are doing one standalone Task: a small piece of work with a checkable answer. It is not \
part of any game; there are no other players, rooms or Rounds. Only you learn whether your \
answer was right, and you get no second chance after a wrong answer.

The Task's title, instructions and inputs are given below as one JSON object.

How to respond: answer by calling exactly one tool, `submit_answer`, with your answer. If your \
reply cannot be used (no tool call, or more than one) you get one retry with the reason; a \
second unusable reply forfeits the Task."""

SUBMIT_TOOL_NAME = "submit_answer"


def submit_tool() -> ToolSpec:
    """``submit_answer``: its ``answer`` schema is the in-game ``tasks.work`` answer field."""
    answer = action_tool(WorkOnTask).parameters["properties"]["answer"]
    return ToolSpec(
        SUBMIT_TOOL_NAME,
        "Submit your answer to the Task.",
        {
            "type": "object",
            "properties": {"answer": answer},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )


def solo_request(brief: Mapping[str, Any], retry: str | None = None) -> HarnessRequest:
    """The request for one attempt at ``brief`` (a kind's ``solo_brief``)."""
    parts = ["Your Task:\n" + json.dumps(brief, sort_keys=True, ensure_ascii=False)]
    if retry:
        parts.append(f"Your previous answer was rejected: {retry}. Try again.")
    parts.append(f"Request: submit your answer by calling `{SUBMIT_TOOL_NAME}` exactly once.")
    return HarnessRequest(
        system=SOLO_SYSTEM_PROMPT, tools=(submit_tool(),), memory=(), tail="\n\n".join(parts)
    )


def parse_answer(response: ProviderResponse) -> tuple[Answer, str | None]:
    """(answer, None) or (None, reason the reply is unusable)."""
    if response.stop_reason == "refusal":
        return None, "the model refused to answer"
    if not response.tool_calls:
        if response.stop_reason == "max_tokens":
            return None, "ran out of output tokens before calling a tool"
        return None, f"no tool call; call {SUBMIT_TOOL_NAME}"
    if len(response.tool_calls) > 1:
        return None, "more than one tool call; call exactly one tool"
    call = response.tool_calls[0]
    if call.name != SUBMIT_TOOL_NAME:
        return None, f"{call.name!r} is not a tool here; call {SUBMIT_TOOL_NAME}"
    if "answer" not in call.arguments:
        return None, "submit_answer needs an 'answer'"
    answer = call.arguments["answer"]
    if not _answer_shape_ok(answer):
        return None, "answer must be text, a number, or a list of those"
    return answer, None


def _answer_shape_ok(answer: Any) -> bool:
    # Same accepted shapes as the in-game ``tasks.work`` action.
    if isinstance(answer, list):
        return all(_scalar(x) for x in answer)
    return _scalar(answer)


def _scalar(x: Any) -> bool:
    return isinstance(x, str | int | float) and not isinstance(x, bool)


@dataclass(frozen=True)
class SoloOutcome:
    """One solo attempt at a Task: the answer, or why there is none, and the calls it cost."""

    answer: Answer = None
    # Set when the reply was unusable after the retry; the trial then counts as failed.
    error: str | None = None
    attempts: int = 1
    calls: Sequence[CallRecord] = field(default_factory=tuple)


# Receives {"event": "started", "attempt", "reserved_usd"} just before each call is
# sent, and {"event": "call", "attempt", "call": CallRecord, "outcome", "answer",
# "reason"} when it returns; outcome is "answer", "unusable" or "provider_error".
# A provider error also has "held_usd": the worst case held against the cap when
# the charge is unknown (no HTTP status: lost in transit), else 0.
Journal = Callable[[Mapping[str, Any]], None]


class SoloSolver:
    """A Model doing one Task alone: ``solver(brief, history, journal) -> SoloOutcome``.

    ``history`` holds the reasons for unusable replies this trial already got (in
    an earlier, interrupted run): they count as spent attempts, and the next
    request carries the last reason, exactly as an uninterrupted retry would.
    Raises ``SpendCapReached`` or ``ProviderError`` from the ledger; the
    calibration runner decides what those mean for the trial.
    """

    def __init__(self, adapter: ProviderAdapter, ledger: Ledger, kind: str) -> None:
        self.adapter = adapter
        self.ledger = ledger
        self.kind = kind

    def __call__(
        self,
        brief: Mapping[str, Any],
        history: Sequence[str] = (),
        journal: Journal | None = None,
    ) -> SoloOutcome:
        note = journal or (lambda event: None)
        first = len(self.ledger.records)
        retry: str | None = history[-1] if history else None
        reserved: list[float] = []  # this attempt's worst-case cost, once it is sent
        for attempt in range(len(history) + 1, MAX_ATTEMPTS + 1):
            reserved.clear()

            def started(needed: float, attempt: int = attempt) -> None:
                reserved.append(needed)
                note({"event": "started", "attempt": attempt, "reserved_usd": needed})

            try:
                response = self.ledger.send(
                    self.adapter,
                    solo_request(brief, retry),
                    purpose=PURPOSE,
                    phase=self.kind,
                    attempt=attempt,
                    on_start=started,
                )
            except ProviderError as exc:
                # An HTTP error status is a rejection, never billed. Without one the
                # request was lost in transit and may still have been billed: hold
                # its worst case against the cap.
                held = reserved[0] if exc.status is None and reserved else 0.0
                self.ledger.hold(held)
                call = _call(attempt, self.ledger.records[-1], "provider_error", None, str(exc))
                note(call | {"held_usd": held})
                raise
            answer, retry = parse_answer(response)
            outcome = "answer" if retry is None else "unusable"
            note(_call(attempt, self.ledger.records[-1], outcome, answer, retry))
            if retry is None:
                return SoloOutcome(answer, None, attempt, tuple(self.ledger.records[first:]))
        return SoloOutcome(None, retry, MAX_ATTEMPTS, tuple(self.ledger.records[first:]))


def _call(
    attempt: int, call: CallRecord, outcome: str, answer: Answer, reason: str | None
) -> dict[str, Any]:
    return {
        "event": "call",
        "attempt": attempt,
        "call": call,
        "outcome": outcome,
        "answer": answer,
        "reason": reason,
    }
