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
``phase=<task kind>``, so the spend cap covers calibration too.

Bump ``SOLO_HARNESS_VERSION`` whenever anything a Model sees changes; results
recorded under another version are not reused.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from zantine.harness.ledger import CallRecord, Ledger
from zantine.harness.neutral import action_tool
from zantine.harness.provider import HarnessRequest, ProviderAdapter, ProviderResponse, ToolSpec
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


class SoloSolver:
    """A Model doing one Task alone: ``solver(brief) -> SoloOutcome``.

    Raises ``SpendCapReached`` or ``ProviderError`` from the ledger; the
    calibration runner decides what those mean for the trial.
    """

    def __init__(self, adapter: ProviderAdapter, ledger: Ledger, kind: str) -> None:
        self.adapter = adapter
        self.ledger = ledger
        self.kind = kind

    def __call__(self, brief: Mapping[str, Any]) -> SoloOutcome:
        first = len(self.ledger.records)
        retry: str | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = self.ledger.send(
                self.adapter,
                solo_request(brief, retry),
                purpose=PURPOSE,
                phase=self.kind,
                attempt=attempt,
            )
            answer, retry = parse_answer(response)
            if retry is None:
                return SoloOutcome(answer, None, attempt, tuple(self.ledger.records[first:]))
        return SoloOutcome(None, retry, MAX_ATTEMPTS, tuple(self.ledger.records[first:]))
