"""The fixed, engine-side summarizer for the older public part of a Seat's log.

Players never write their own notes. When the public part of a Seat's log grows
past ``SUMMARY_TRIGGER_TOKENS``, older Meetings are condensed in place by plain
code: no Model call, no cost, and no input but the log itself and the roster, so
two Seats with the same log get the same summary whatever Model plays them.

Public and private observations (by ``type``):

- **Public**: ``PUBLIC_TYPES``, announcements every living player receives
  identically: Meeting calls, statements, vote results, Ejections, the return to
  the meeting room, Crisis announcements and game over. Every living Seat holds the
  same public log, and the trigger counts only public lines, so all living Seats
  are condensed at the same moment. Only Meeting entries (``MEETING_TYPES``) are
  ever condensed; the other public types are one line each and stay verbatim.
- **Private**: everything else, i.e. what this Seat alone (or only the players in
  its room) learned: its role, room snapshots, kills it saw, its own Invalid
  Actions, its Tasks and grades, its Crisis fix attempts, and any plug-in type not
  listed as public (e.g. Room Chat). Private observations are never condensed or
  dropped; a plug-in observation counts as private unless added to
  ``PUBLIC_TYPES``.

Levels, by the age of a completed Meeting (one whose vote result is in the log):
the in-progress Meeting and the ``VERBATIM_MEETINGS`` most recent completed ones
stay verbatim; the next ``DIGEST_MEETINGS`` become a *digest* (header, one line
per speaker with each statement clipped to ``DIGEST_STATEMENT_CHARS``, the vote);
older ones become a one-line *brief* (who named whom, the vote). A summary sits
where its Meeting began, so private lines keep their order. A Meeting's summary
at a given level never changes and a Meeting's level only rises as the game goes
on. A digest is replaced once by a brief; a brief is immutable thereafter.
This deliberately trades some cache reuse for more detail about recent Meetings.

Token counts use ``estimate_tokens``, a fixed character-based estimate (no
provider tokenizer, no network), so the trigger fires identically everywhere.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from zantine.harness.neutral import _meeting_reason, render_observation
from zantine.harness.neutral import _people as people
from zantine.harness.neutral import _who as who
from zantine.observations import Observation

MEETING_TYPES = frozenset({"meeting_called", "statement", "vote_result", "ejected"})
PUBLIC_TYPES = MEETING_TYPES | {
    "returned_to_meeting_room",
    "game_over",
    "crisis.started",
    "crisis.resolved",
    "crisis.failed",
}

SUMMARY_TRIGGER_TOKENS = 6000
VERBATIM_MEETINGS = 1
DIGEST_MEETINGS = 3
DIGEST_STATEMENT_CHARS = 100


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate: about four characters per token."""
    return (len(text) + 3) // 4


def is_public(obs: Observation) -> bool:
    return obs.type in PUBLIC_TYPES


def condense(observations: Sequence[Observation], roster: Mapping[int, str]) -> list[str]:
    """Memory lines with older Meetings condensed; private lines are always kept."""
    lines = [render_observation(o, roster) for o in observations]
    public = sum(
        estimate_tokens(line + "\n")
        for o, line in zip(observations, lines, strict=True)
        if is_public(o)
    )
    if public <= SUMMARY_TRIGGER_TOKENS:
        return lines

    groups: dict[int, list[Observation]] = {}
    for obs in observations:
        if obs.type in MEETING_TYPES:
            groups.setdefault(obs.data["meeting"], []).append(obs)
    completed = [m for m, g in groups.items() if any(o.type == "vote_result" for o in g)]
    older = completed[: max(len(completed) - VERBATIM_MEETINGS, 0)]
    briefs = len(older) - DIGEST_MEETINGS
    summaries = {
        m: [_brief(groups[m], roster)] if i < briefs else _digest(groups[m], roster)
        for i, m in enumerate(older)
    }

    out: list[str] = []
    for obs, line in zip(observations, lines, strict=True):
        meeting = obs.data.get("meeting") if obs.type in MEETING_TYPES else None
        if meeting in summaries:
            # The whole summary goes where the Meeting's first entry was.
            out += summaries.pop(meeting)
        elif meeting is None or meeting not in older:
            out.append(line)
    return out


def _header(group: Sequence[Observation], roster: Mapping[int, str]) -> str:
    first = group[0]
    head = f"[Round {first.round}] Meeting {first.data['meeting']} (condensed)"
    called = next((o for o in group if o.type == "meeting_called"), None)
    if called is None:
        return head + "."
    return f"{head}: {_meeting_reason(called.data, roster)}. Dead so far: " + (
        people(called.data["dead"], roster) + "."
    )


def _speeches(group: Sequence[Observation]) -> dict[int, list[str]]:
    """Each speaker's statements in order, speakers in order of first speaking."""
    said: dict[int, list[str]] = {}
    for o in group:
        if o.type == "statement":
            said.setdefault(o.data["speaker"], []).append(o.data["text"])
    return said


def _vote(group: Sequence[Observation], roster: Mapping[int, str]) -> str | None:
    result = next((o for o in group if o.type == "vote_result"), None)
    if result is None:
        return None
    d = result.data
    counts = ", ".join(f"{who(c['seat'], roster)}: {c['votes']}" for c in d["counts"])
    ejected = who(d["ejected"], roster) if d["ejected"] is not None else "nobody"
    return f"Vote: {counts or 'no votes'}; skips: {d['skips']}. Ejected: {ejected}."


def _clip(text: str) -> str:
    text = " ".join(text.split())
    if len(text) > DIGEST_STATEMENT_CHARS:
        text = text[: DIGEST_STATEMENT_CHARS - 1].rstrip() + "…"
    return json.dumps(text, ensure_ascii=False)


def _digest(group: Sequence[Observation], roster: Mapping[int, str]) -> list[str]:
    lines = [_header(group, roster)]
    for speaker, texts in _speeches(group).items():
        quotes = [_clip(t) for t in texts if t.strip()]
        lines.append(f"  {who(speaker, roster)}: " + (" / ".join(quotes) or "said nothing."))
    vote = _vote(group, roster)
    if vote:
        lines.append("  " + vote)
    return lines


def _named(text: str, speaker: int, roster: Mapping[int, str]) -> list[int]:
    """Seats a statement refers to, by name (whole word) or as ``seat N``."""
    return [
        seat
        for seat, name in sorted(roster.items())
        if seat != speaker
        and (
            re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE)
            or re.search(rf"\bseat\s*{seat}\b", text, re.IGNORECASE)
        )
    ]


def _brief(group: Sequence[Observation], roster: Mapping[int, str]) -> str:
    speeches = _speeches(group)
    spoken = sum(1 for texts in speeches.values() for t in texts if t.strip())
    named = []
    for speaker, texts in speeches.items():
        seats = sorted({s for t in texts for s in _named(t, speaker, roster)})
        if seats:
            names = ", ".join(roster.get(s, "?") for s in seats)
            named.append(f"{roster.get(speaker, '?')} named {names}")
    parts = [
        _header(group, roster),
        f"{spoken} statement(s) from {len(speeches)} player(s);",
        ("; ".join(named) + ".") if named else "nobody named another player.",
    ]
    vote = _vote(group, roster)
    if vote:
        parts.append(vote)
    return " ".join(parts)
