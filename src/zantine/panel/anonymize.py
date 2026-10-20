"""What a Label Panel judge sees for one Claim: an anonymized view of the game.

``item_view`` turns a game record, one statement and a quote into plain JSON
data with:

- every Seat name (a colour) replaced by a neutral token (``P1`` .. ``P10``),
  in a seeded random order per item, so tokens never encode Seat index or name;
- the same replacement inside every piece of player speech, plus model, lab and
  product names and "Seat N" references scrubbed to ``[redacted]``;
- no role, controller, model, Seat index or seed anywhere.

The view holds what a judge needs to label the Claim and nothing else: the
anonymized record up to the statement (each living player's room at the start
and end of every Round so far, kills and completed Tasks before the statement)
and the Meeting's spoken statements in order. Kills name the killers, because
kill Claims can't be checked without them; no role is ever listed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from zantine.audit.record import GameRecord, StatementRecord
from zantine.harness.pricing import load_models
from zantine.judge.batches import NO_ONE, SKIP, spoken
from zantine.rng import GameRng

REDACTED = "[redacted]"
# Product names a Model might use for itself or others in speech. Model ids and
# lab names come from models.toml; these are the brand words around them.
BRAND_WORDS = (
    "Claude",
    "Fable",
    "Opus",
    "Sonnet",
    "Haiku",
    "ChatGPT",
    "GPT",
    "Astra",
    "Gemini",
    "Argon",
    "Grok",
    "Muse",
    "Llama",
)


@dataclass(frozen=True)
class View:
    data: dict[str, Any]
    tokens: dict[int, str]  # Seat -> token (kept locally, never sent)
    target_statement: str  # id of the statement holding the Claim, in ``data``
    push_options: tuple[str, ...]

    def token(self, seat: int | None) -> str | None:
        return None if seat is None else self.tokens.get(seat)


def model_words(extra: Iterable[str] = ()) -> tuple[str, ...]:
    """Model ids, labs and brand words to scrub from speech."""
    models = load_models()
    words = {*BRAND_WORDS, *extra}
    for m in models.values():
        words.add(m.id)
        words.add(m.lab)
    return tuple(sorted(words, key=len, reverse=True))


def _flexible(word: str) -> str:
    """``gpt-6.1-sol`` also matches ``GPT 6.1 Sol`` and ``gpt6.1sol``."""
    parts = re.split(r"[-\s_.]+", word)
    return r"[-\s_.]?".join(re.escape(p) for p in parts if p)


class Scrubber:
    """Replaces Seat names with tokens and model names with ``[redacted]`` in speech."""

    def __init__(self, names: dict[int, str], tokens: dict[int, str], words: Iterable[str]):
        by_name = {names[s].casefold(): tokens[s] for s in names}
        self._names = by_name
        alternation = "|".join(re.escape(n) for n in sorted(names.values(), key=len, reverse=True))
        self._name_re = re.compile(rf"\b(?:{alternation})\b", re.I) if names else None
        models = "|".join(_flexible(w) for w in words)
        self._model_re = re.compile(rf"\b(?:{models})\b", re.I) if models else None
        self._seat_re = re.compile(r"\bseats?\s*#?\s*\d+\b", re.I)

    def __call__(self, text: str) -> str:
        if self._model_re is not None:
            text = self._model_re.sub(REDACTED, text)
        if self._name_re is not None:
            text = self._name_re.sub(lambda m: self._names[m.group(0).casefold()], text)
        return self._seat_re.sub(REDACTED, text)


def tokens_for(rec: GameRecord, rng: GameRng) -> dict[int, str]:
    seats = rng.shuffled(sorted(rec.names))
    return {seat: f"P{i + 1}" for i, seat in enumerate(seats)}


def item_view(
    rec: GameRecord,
    statement: StatementRecord,
    quote: str,
    rng: GameRng,
    *,
    words: Iterable[str] | None = None,
) -> View:
    """The anonymized data one judge call sees for the Claim ``quote`` in ``statement``."""
    tokens = tokens_for(rec, rng)
    scrub = Scrubber(rec.names, tokens, model_words() if words is None else words)
    meeting = rec.meeting(statement.meeting)
    in_meeting = [st for st in rec.statements if st.meeting == statement.meeting]
    living = sorted({st.speaker for st in in_meeting})
    said = spoken(in_meeting)
    ids = {st.seq: f"s{i + 1}" for i, st in enumerate(said)}
    if statement.seq not in ids:
        raise ValueError("the statement holding a Claim must say something")

    def where(round: int, boundary: str) -> dict[str, str]:
        at = rec.positions.get((round, boundary), {})  # type: ignore[arg-type]
        rows = sorted((tokens[s], room) for s, room in at.items())
        return dict(sorted(rows, key=lambda x: _token_order(x[0])))

    record = {
        "rounds": [
            {"round": r, "start": where(r, "start"), "end": where(r, "end")}
            for r in range(1, meeting.round + 1)
        ],
        "kills": [
            {
                "round": k.round,
                "room": k.room,
                "victim": tokens[k.victim],
                "killers": sorted(tokens[x] for x in k.killers),
            }
            for k in rec.kills
            if k.seq < statement.seq
        ],
        "tasks_done": [
            {"round": t.round, "player": tokens[t.seat], "task": t.task}
            for t in rec.tasks_done
            if t.seq < statement.seq
        ],
    }
    others = sorted((tokens[s] for s in living if s != statement.speaker), key=_token_order)
    options = (*others, SKIP, NO_ONE)
    data = {
        "players": sorted((tokens[s] for s in living), key=_token_order),
        "all_players": sorted(tokens.values(), key=_token_order),
        "rooms": list(rec.rooms),
        "meeting": {
            "round": meeting.round,
            "since_previous_meeting": [meeting.previous_round + 1, meeting.round],
        },
        "record": record,
        "statements": [
            {"id": ids[st.seq], "speaker": tokens[st.speaker], "text": scrub(st.text)}
            for st in said
        ],
        "target": {
            "statement": ids[statement.seq],
            "speaker": tokens[statement.speaker],
            "quote": scrub(quote),
        },
        "push_options": list(options),
    }
    return View(data, tokens, ids[statement.seq], options)


def _token_order(token: str) -> int:
    return int(token[1:])
