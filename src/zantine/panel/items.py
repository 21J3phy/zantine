"""What the Label Panel labels: real-game Claims (with the pipeline's labels) and Planted Claims.

``candidates(records, audit)`` turns one audited game into ``Candidate``s, one
per Claim, each with the pipeline's own four labels (``truth`` from the Claim
Audit verdict; ``assertion``, ``challenged`` and ``push`` from the judgments,
#15, where they exist) and the speaker's Model and role, used only to stratify
the sample. ``stratified_sample`` draws a seeded sample balanced across
(Model, role) strata.

Planted Claims come through ``PlantedSource``: anything with a
``plain_items()`` method returning ``PlantedItem``s. ``PlantedReport`` reads the
item dicts of a Planted Claims report (#14, ``audit/planted.py``) or any list in
that shape; a test fixture can implement the protocol directly.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from zantine.audit.record import GameRecord, StatementRecord, build_record
from zantine.judge.batches import NO_ONE, SKIP
from zantine.judge.report import (
    JUDGMENTS_SCHEMA_VERSION,
    check_audit_matches,
    transcript_digest,
)
from zantine.rng import GameRng

Record = dict[str, Any]
TRUTH_OF = {"true": "true", "lie": "false", "misstatement": "false", "unverifiable": "unverifiable"}


@dataclass(frozen=True)
class Candidate:
    id: str
    game: str  # transcript digest prefix
    claim_id: str
    model: str
    role: str
    rec: GameRecord
    statement: StatementRecord
    quote: str
    # The Claim's structured proposition (no verdict): ``anonymize.MEANING_FIELDS``.
    meaning: Mapping[str, Any]
    # The pipeline's labels. ``push`` is ("seat", n), "skip", "no_one" or None.
    truth: str
    assertion: str | None
    challenged: bool | None
    push: Any

    @property
    def stratum(self) -> tuple[str, str]:
        return (self.model, self.role)


def seat_model(controller: Mapping[str, Any] | None) -> str:
    """The Model behind a Seat, from its header ``controller`` (bots by kind)."""
    if not controller:
        return "unknown"
    return str(controller.get("model") or controller.get("kind") or "unknown")


def candidates(records: Sequence[Record], audit: Mapping[str, Any]) -> list[Candidate]:
    """Every Claim of one audited game. Refuses an audit not made from these records."""
    rec = build_record(records)
    check_audit_matches(dict(audit), rec)
    game = transcript_digest(records)[:12]
    models = {s["seat"]: seat_model(s.get("controller")) for s in rec.header.get("seats", [])}
    by_seq = {st.seq: st for st in rec.statements}
    rows, pushes = _judgments(audit, records, rec)
    out = []
    for i, c in enumerate(audit["claims"]):
        st = by_seq[c["statement_seq"]]
        if not st.text.strip():
            continue
        row = rows[i] if rows else {}
        asserted = row.get("asserted")
        assertion = None if asserted is None else ("claim" if asserted["value"] >= 0.5 else "hedge")
        out.append(
            Candidate(
                id=f"{game}.{c['id']}",
                game=game,
                claim_id=c["id"],
                model=models.get(st.speaker, "unknown"),
                role=rec.roles.get(st.speaker) or "unknown",
                rec=rec,
                statement=st,
                quote=c["quote"],
                meaning=claim_meaning(c),
                truth=TRUTH_OF[c["verdict"]["label"]],
                assertion=assertion,
                challenged=row.get("challenged"),
                push=_push(pushes.get(st.seq)),
            )
        )
    return out


def claim_meaning(c: Mapping[str, Any]) -> dict[str, Any]:
    """An audit Claim's proposition, with Seats; never its verdict, evidence or proof."""
    return {
        "kind": c["kind"],
        "subject": c["subject"],
        "object": c["object"],
        "room": c["room"],
        "round": c["claimed_round"],
        "task": c["task"],
        "negated": c["negated"],
        "seen": c.get("seen", False),
        "throughout": c.get("throughout", False),
    }


def _judgments(
    audit: Mapping[str, Any], records: Sequence[Record], rec: GameRecord
) -> tuple[list[Mapping[str, Any]], dict[int, Mapping[str, Any]]]:
    """The judgments' Claim rows (in audit order) and push rows, after checking they were
    made from this transcript and this audit; refuses anything else. Empty if none."""
    j = audit.get("judgments")
    if not j:
        return [], {}
    if j.get("kind") != "judgments" or j.get("schema") != JUDGMENTS_SCHEMA_VERSION:
        raise ValueError("the audit's judgments are not a judgments report this code reads")
    if j.get("transcript_sha256") != transcript_digest(records):
        raise ValueError("the audit's judgments were made from a different transcript")
    claims = audit["claims"]
    rows = list(j.get("claims", []))
    if len(rows) != len(claims):
        raise ValueError("the audit's judgments cover a different set of Claims")
    by_seq = {st.seq: st for st in rec.statements}
    for i, (row, c) in enumerate(zip(rows, claims, strict=True)):
        st = by_seq[c["statement_seq"]]
        own_id = c["id"].rsplit(".c", 1)[0] == f"m{st.meeting}.p{st.pass_index}.o{st.order}"
        bound = (row.get("index"), row.get("id"), row.get("speaker"), row.get("label"))
        if not own_id or bound != (i, c["id"], c["speaker"], c["verdict"]["label"]):
            raise ValueError(f"judgments for Claim {c['id']!r} don't match the audit's Claim {i}")
    pushes: dict[int, Mapping[str, Any]] = {}
    for row in j.get("statements", []):
        st = by_seq.get(row.get("seq"))
        if st is None or (row.get("speaker"), row.get("meeting")) != (st.speaker, st.meeting):
            raise ValueError(f"judged statement {row.get('seq')!r} is not in the transcript")
        pushes[st.seq] = row
    return rows, pushes


def _push(row: Mapping[str, Any] | None) -> Any:
    if not row or not row.get("judged"):
        return None
    if row.get("push_seat") is not None:
        return ("seat", row["push_seat"])
    return row["push"] if row["push"] in (SKIP, NO_ONE) else None


def stratified_sample(pool: Sequence[Candidate], n: int, seed: int) -> list[Candidate]:
    """``n`` Claims spread as evenly as possible over (Model, role) strata; seeded.

    Strata are visited round-robin in a seeded order, taking one Claim (in a
    seeded order within the stratum) from each that still has some, so small
    strata are taken whole and the rest share what's left equally.
    """
    if n < 0:
        raise ValueError("sample size must be non-negative")
    rng = GameRng(seed).fork("panel/sample")
    strata: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for c in sorted(pool, key=lambda c: c.id):
        strata[c.stratum].append(c)
    order = rng.shuffled(sorted(strata))
    queues = {k: rng.fork(f"{k[0]}/{k[1]}").shuffled(strata[k]) for k in order}
    picked: list[Candidate] = []
    while len(picked) < n and any(queues.values()):
        for k in order:
            if queues[k] and len(picked) < n:
                picked.append(queues[k].pop())
    return picked


def strata_counts(pool: Iterable[Candidate], sample: Iterable[Candidate]) -> list[dict[str, Any]]:
    available: dict[tuple[str, str], int] = defaultdict(int)
    taken: dict[tuple[str, str], int] = defaultdict(int)
    for c in pool:
        available[c.stratum] += 1
    for c in sample:
        taken[c.stratum] += 1
    return [
        {"model": m, "role": r, "available": available[(m, r)], "sampled": taken[(m, r)]}
        for m, r in sorted(available)
    ]


# Planted Claims -------------------------------------------------------------------------


@dataclass(frozen=True)
class PlantedItem:
    """A known-truth statement placed in a real game's Meeting."""

    id: str
    records: Sequence[Record]
    statement_seq: int  # the real statement it stands in for or is spliced into
    speaker: int
    text: str  # the whole statement
    quote: str  # the planted sentence
    truth: bool
    # The planted proposition (``anonymize.MEANING_FIELDS``, Seats), if the source has it.
    meaning: Mapping[str, Any] | None = None

    def placed(self) -> tuple[GameRecord, StatementRecord]:
        """The game record with the planted statement in place of the real one."""
        rec = build_record(self.records)
        real = next(s for s in rec.statements if s.seq == self.statement_seq)
        st = dataclasses.replace(real, speaker=self.speaker, text=self.text)
        statements = [st if s.seq == st.seq else s for s in rec.statements]
        return dataclasses.replace(rec, statements=statements), st


class PlantedSource(Protocol):
    def plain_items(self) -> Iterable[PlantedItem]:
        """Plain-phrasing Planted Claims only (the qualification set)."""
        ...


class PlantedReport:
    """Planted Claims in #14's item shape: ``game`` indexes ``games``; ``phrasing`` filters."""

    def __init__(self, games: Sequence[Sequence[Record]], items: Iterable[Mapping[str, Any]]):
        self.games = games
        self.items = list(items)

    @classmethod
    def from_json(cls, games: Sequence[Sequence[Record]], data: Any) -> PlantedReport:
        """A Planted Claims report (``{"items": [...]}``) or a bare list of items."""
        items = data.get("items") if isinstance(data, Mapping) else data
        if not isinstance(items, list):
            raise ValueError("expected a Planted Claims report or a list of planted items")
        return cls(games, items)

    def plain_items(self) -> list[PlantedItem]:
        out = []
        for d in self.items:
            if d.get("phrasing") != "plain":
                continue
            game = d["game"]
            if not 0 <= game < len(self.games):
                raise ValueError(f"planted item {d['id']!r} refers to game {game}, not given")
            out.append(
                PlantedItem(
                    id=str(d["id"]),
                    records=self.games[game],
                    statement_seq=d["statement_seq"],
                    speaker=d["speaker"],
                    text=d["text"],
                    quote=d["sentence"],
                    truth=bool(d["truth"]),
                    meaning=d.get("expected"),
                )
            )
        return out
