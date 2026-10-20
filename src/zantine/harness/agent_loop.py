"""The agent-loop Harness: a lean in-repo port of the OpenAI Agents SDK run loop.

An alternative to the Neutral Harness (``harness.neutral``) for the Harness
comparison (roadmap P8): the same Model, provider adapter, effort, pricing,
Ledger and spend cap, with only the scaffolding swapped. It follows how
``Runner.run`` drives an ``Agent``:

- *The loop.* One decision is a run of up to ``MAX_TURNS`` model calls. Each
  call sees the steps taken so far in this decision; the run stops at the first
  usable decision-tool call (the SDK's ``tool_use_behavior=StopAtTools`` with
  the game tools as the stop tools). Running out of turns is the SDK's
  ``MaxTurnsExceeded``; here it becomes an Invalid Action.
- *Tool errors go back to the model.* An unusable tool call (wrong tool for the
  request, bad arguments, several calls at once) is answered with the error as
  that step's result and the loop goes on, as the SDK's default
  ``failure_error_function`` does. A reply with no tool call ends the run like
  the SDK's final text output, and is unusable. The engine's one retry and
  forfeit then apply exactly as for the Neutral Harness.
- *Memory.* The SDK's sessions keep the whole history, so the log is the Seat's
  observations verbatim (never condensed by ``harness.summarizer``), plus an
  agent-written notepad: ``update_notes`` replaces it (at most ``NOTES_LIMIT``
  characters) and it is shown in every later request.

Requests are still single-turn ``HarnessRequest``s: earlier steps of the current
decision are written into the tail as text, not sent as native tool-result
messages. That keeps every provider adapter, the cache layout and pricing
shared with the Neutral Harness. The game rules text, tools, state lines and
requests are the Neutral Harness's own (``neutral.RULES_PROMPT`` etc.).

Bump ``HARNESS_VERSION`` whenever anything a Model sees changes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from zantine.harness import neutral
from zantine.harness.provider import HarnessRequest, ToolCall, ToolSpec
from zantine.observations import ActionContext, MeetingContext
from zantine.rules.registry import Rules

HARNESS_NAME = "agent-loop"
HARNESS_VERSION = 1
# Model calls per decision (the SDK's ``max_turns``; its default is 10).
MAX_TURNS = 4
NOTES_LIMIT = 1500
# How much of a rejected call's arguments is echoed back in the step list.
_ECHO_CHARS = 200

NOTES_TOOL = ToolSpec(
    "update_notes",
    "Replace your private notes with new text. Only you see them; they are shown in "
    f"every later request. At most {NOTES_LIMIT} characters; longer text is cut off. "
    "This does not answer the request.",
    {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Your new notes."}},
        "required": ["text"],
        "additionalProperties": False,
    },
)

LOOP_PROMPT = f"""\
How to respond: every message ends with a request, and answering it is one decision. A \
decision may take several steps, at most {MAX_TURNS} tool calls in all. Call `update_notes` \
to rewrite your private notes, which only you see and which are shown in every later \
message. To answer, call exactly one tool that fits the request: an action tool during a \
Round, `speak` during Meeting discussion, `vote` or `skip_vote` during a Meeting vote; that \
call ends the decision. If a call cannot be used, the next message lists your steps so far \
with the error, and you may call again. A reply without a tool call ends the decision \
unanswered. If the game then rejects your answer you get one retry with the reason; a second \
rejected answer forfeits that decision."""

# The Neutral rules, tool note and response format, minus the condensed-log note
# (this Harness never condenses the log).
SYSTEM_PROMPT = "\n\n".join((neutral.RULES_PROMPT, neutral.TOOLS_NOTE, LOOP_PROMPT))
LOG_HEADER = "Your log (complete, oldest first):\n"


def tools(rules: Rules) -> tuple[ToolSpec, ...]:
    """The Neutral Harness tools plus ``update_notes``, offered on every call."""
    return (*neutral.tools(rules), NOTES_TOOL)


@dataclass(frozen=True)
class Step:
    """One earlier model call in the current decision and what came of it."""

    call: str
    result: str


def render_call(calls: Sequence[ToolCall]) -> str:
    """A short echo of what the model called (arguments clipped)."""
    if not calls:
        return "no tool call"
    out = []
    for call in calls:
        if call.name == NOTES_TOOL.name:
            out.append(f"{call.name}(...)")
            continue
        args = json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
        if len(args) > _ECHO_CHARS:
            args = args[:_ECHO_CHARS] + "..."
        out.append(f"{call.name}({args})")
    return " + ".join(out)


def new_notes(call: ToolCall) -> tuple[str | None, str]:
    """(notes, step result) for an ``update_notes`` call; notes is None if unusable."""
    text = call.arguments.get("text")
    if not isinstance(text, str):
        return None, "rejected: update_notes needs a string 'text'"
    if len(text) > NOTES_LIMIT:
        return text[:NOTES_LIMIT], f"notes saved, cut to {NOTES_LIMIT} characters"
    return text, "notes saved"


def _request(
    rules: Rules,
    ctx: ActionContext | MeetingContext,
    state: list[str],
    ask: str,
    notes: str,
    steps: Sequence[Step],
    retry: str | None,
) -> HarnessRequest:
    lines = neutral.memory_lines(ctx.observations, ctx.roster, summarize=False)
    blocks, head = neutral.log_parts(lines, LOG_HEADER)
    parts = [p for p in (head, "Now:\n" + "\n".join(state)) if p]
    parts.append("Your notes:\n" + notes if notes else "Your notes: (empty)")
    if retry:
        parts.append(f"Your previous answer was rejected: {retry}. Try again.")
    if steps:
        done = [f"{i}. {s.call} -> {s.result}" for i, s in enumerate(steps, 1)]
        left = MAX_TURNS - len(steps)
        parts.append(
            "Your steps so far in this decision:\n" + "\n".join(done) + f"\n({left} call(s) left.)"
        )
    parts.append(ask)
    return HarnessRequest(
        system=SYSTEM_PROMPT,
        tools=tools(rules),
        memory=blocks,
        tail="\n\n".join(parts),
    )


def action_request(
    rules: Rules,
    ctx: ActionContext,
    *,
    notes: str = "",
    steps: Sequence[Step] = (),
    retry: str | None = None,
) -> HarnessRequest:
    state = neutral.action_state(ctx)
    retry = retry or ctx.retry_reason
    return _request(rules, ctx, state, neutral.ACTION_ASK, notes, steps, retry)


def speak_request(
    rules: Rules,
    ctx: MeetingContext,
    *,
    notes: str = "",
    steps: Sequence[Step] = (),
    retry: str | None = None,
) -> HarnessRequest:
    state = neutral.meeting_state(ctx)
    return _request(rules, ctx, state, neutral.speak_ask(ctx), notes, steps, retry)


def vote_request(
    rules: Rules,
    ctx: MeetingContext,
    *,
    notes: str = "",
    steps: Sequence[Step] = (),
    retry: str | None = None,
) -> HarnessRequest:
    state = neutral.meeting_state(ctx)
    retry = retry or ctx.retry_reason
    return _request(rules, ctx, state, neutral.VOTE_ASK, notes, steps, retry)
