"""The structured **Claim**: the extractor's output schema, its validation and name resolution.

An extractor sees one **Meeting** statement as quoted data plus public facts
(player names, room names, the Round) and returns ``{"claims": [...]}``
matching ``CLAIMS_SCHEMA``. Its output is never trusted: ``parse_payload``
keeps only the known fields of well-formed items, and ``resolve`` maps names to
Seats. The speaker always comes from the game record, never from the
extractor, and there is no field through which an extractor could report a
verdict; verdicts come only from ``audit.check``.

Claim kinds (``subject`` is always a player name; the speaker's own name for "I"):

- ``location``: ``subject`` was in ``room`` during ``round``.
- ``together``: ``subject`` was with ``object`` (another player), in ``room`` if given.
- ``kill``: ``subject`` killed ``object`` (the victim; null = someone).
- ``task_done``: ``subject`` completed ``task`` (an id like T12; null = some Task).

``room`` and ``round`` narrow any kind when given.
- ``other``: any other factual Claim; always unverifiable in v0.

``round`` null means "since the last Meeting" for ``location`` and ``together``,
and "at any time so far" for ``kill`` and ``task_done``. ``negated`` is true for
denials ("I was not in Storage", "I didn't kill Red").
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# Bump when the schema or the meaning of a field changes; recorded in every audit.
CLAIM_SCHEMA_VERSION = 1

CLAIM_KINDS = ("location", "together", "kill", "task_done", "other")

_NULLABLE_STRING = {"type": ["string", "null"]}
CLAIM_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(CLAIM_KINDS)},
        "subject": _NULLABLE_STRING,
        "object": _NULLABLE_STRING,
        "room": _NULLABLE_STRING,
        "round": {"type": ["integer", "null"]},
        "task": _NULLABLE_STRING,
        "negated": {"type": "boolean"},
        "quote": {"type": "string"},
    },
    "required": ["kind", "subject", "object", "room", "round", "task", "negated", "quote"],
    "additionalProperties": False,
}
CLAIMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"claims": {"type": "array", "items": CLAIM_ITEM_SCHEMA}},
    "required": ["claims"],
    "additionalProperties": False,
}

_STRING_FIELDS = ("subject", "object", "room", "task")
_SELF_WORDS = frozenset({"i", "me", "myself", "self"})


@dataclass(frozen=True)
class ExtractionInput:
    """Everything an extractor may see for one statement: public facts only, never the record."""

    text: str
    speaker: str
    players: tuple[str, ...]
    rooms: tuple[str, ...]
    meeting: int
    round: int

    def to_data(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "statement": self.text,
            "players": list(self.players),
            "rooms": list(self.rooms),
            "meeting": self.meeting,
            "current_round": self.round,
        }


@dataclass(frozen=True)
class ClaimDraft:
    """One validated item of extractor output; names are still strings."""

    kind: str
    subject: str | None
    object: str | None
    room: str | None
    round: int | None
    task: str | None
    negated: bool
    quote: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParsedPayload:
    drafts: tuple[ClaimDraft, ...]
    # Why each dropped item was dropped.
    rejected: tuple[str, ...] = ()
    # Unknown keys the extractor added (ignored; they can never affect a verdict).
    ignored_fields: tuple[str, ...] = ()


class ExtractionError(Exception):
    """The extractor's reply as a whole is unusable (not JSON, no ``claims`` list)."""


def parse_payload(payload: Any) -> ParsedPayload:
    """Validate raw extractor output against ``CLAIMS_SCHEMA``.

    Malformed items are dropped (and counted); unknown keys are ignored rather
    than rejected, so speech that coaxes an extractor into adding fields can't
    make a Claim disappear.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("claims"), list):
        raise ExtractionError("expected an object with a 'claims' list")
    ignored = sorted(k for k in payload if k != "claims")
    drafts, rejected = [], []
    for i, item in enumerate(payload["claims"]):
        if not isinstance(item, dict):
            rejected.append(f"item {i}: not an object")
            continue
        ignored += sorted(
            f"claims[{i}].{k}" for k in item if k not in CLAIM_ITEM_SCHEMA["properties"]
        )
        problem = _item_problem(item)
        if problem:
            rejected.append(f"item {i}: {problem}")
            continue
        drafts.append(
            ClaimDraft(
                kind=item["kind"],
                subject=_clean(item["subject"]),
                object=_clean(item["object"]),
                room=_clean(item["room"]),
                round=item["round"],
                task=_clean(item["task"]),
                negated=item["negated"],
                quote=item["quote"],
            )
        )
    return ParsedPayload(tuple(drafts), tuple(rejected), tuple(ignored))


def _item_problem(item: dict[str, Any]) -> str | None:
    for key in CLAIM_ITEM_SCHEMA["required"]:
        if key not in item:
            return f"missing {key!r}"
    if item["kind"] not in CLAIM_KINDS:
        return f"unknown kind {item['kind']!r}"
    for key in _STRING_FIELDS:
        if item[key] is not None and not isinstance(item[key], str):
            return f"{key!r} must be a string or null"
    rnd = item["round"]
    if rnd is not None and (type(rnd) is not int):
        return "'round' must be an integer or null"
    if type(item["negated"]) is not bool:
        return "'negated' must be a boolean"
    if not isinstance(item["quote"], str):
        return "'quote' must be a string"
    return None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass(frozen=True)
class Claim:
    """A Claim tied to its statement, with names resolved to Seats.

    ``problems`` lists why the Claim can't be checked (unknown player, room or
    Task, missing subject, ...); such a Claim is unverifiable.
    """

    id: str
    statement_seq: int
    meeting: int
    pass_index: int
    order: int
    round: int  # the Meeting's Round (when the statement was made)
    speaker: int
    kind: str
    subject: int | None
    object: int | None
    room: str | None
    claimed_round: int | None
    task: str | None
    negated: bool
    quote: str
    quote_in_text: bool
    problems: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["problems"] = list(self.problems)
        return d


def resolve(
    draft: ClaimDraft,
    *,
    claim_id: str,
    statement: Any,
    names: dict[int, str],
    rooms: tuple[str, ...],
    task_ids: tuple[str, ...],
) -> Claim:
    """Map a draft's names to Seats, rooms and Task ids from the game record."""
    problems: list[str] = []
    by_name = {n.casefold(): s for s, n in names.items()}

    def seat(value: str | None, what: str) -> int | None:
        if value is None:
            return None
        key = value.casefold()
        if key in _SELF_WORDS:
            return statement.speaker
        if key not in by_name:
            problems.append(f"unknown {what} {value!r}")
            return None
        return by_name[key]

    subject = seat(draft.subject, "player")
    obj = seat(draft.object, "player")
    if draft.subject is None and draft.kind != "other":
        problems.append("no subject")
    room = None
    if draft.room is not None:
        room = next((r for r in rooms if r.casefold() == draft.room.casefold()), None)
        if room is None:
            problems.append(f"unknown room {draft.room!r}")
    task = None
    if draft.task is not None:
        wanted = re.sub(r"[^0-9a-z]", "", draft.task.casefold())
        task = next((t for t in task_ids if t.casefold() == wanted), None)
        if task is None:
            problems.append(f"unknown Task {draft.task!r}")
    if draft.kind == "together" and draft.object is None:
        problems.append("no companion")
    if draft.round is not None and not 1 <= draft.round <= statement.round:
        problems.append(f"Round {draft.round} is not between 1 and {statement.round}")
    if draft.kind == "other":
        problems.append("kind 'other' is not checked in v0")
    return Claim(
        id=claim_id,
        statement_seq=statement.seq,
        meeting=statement.meeting,
        pass_index=statement.pass_index,
        order=statement.order,
        round=statement.round,
        speaker=statement.speaker,
        kind=draft.kind,
        subject=subject,
        object=obj,
        room=room,
        claimed_round=draft.round,
        task=task,
        negated=draft.negated,
        quote=draft.quote,
        quote_in_text=_squash(draft.quote) in _squash(statement.text),
        problems=tuple(problems),
    )


def _squash(text: str) -> str:
    return " ".join(text.casefold().split())
