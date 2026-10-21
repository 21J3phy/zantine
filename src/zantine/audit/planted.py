"""**Planted Claims**: engine-written statements with known truth, for measuring the Claim Audit.

``generate(games, n, seed)`` writes ``n`` Planted Claims into real games'
**Meetings**: one third each in ``plain``, ``hedged`` and ``subtle`` phrasings,
true and false alternating within each phrasing, rotating through the checkable
Claim kinds (``location``, ``together``, ``kill``, ``task_done``; some
``location`` and ``kill`` Claims are denials). Each is spoken by a Seat that
speaks in that Meeting, about Rounds up to the Meeting. About a quarter are
``in_meeting``: the planted sentence is spliced into a real statement from that
Meeting (by that statement's speaker), so the extractor must pick it out of real
speech; the rest are ``standalone`` statements that hold only the sentence.

*Labels.* Truth comes from Seats' ``observation`` records only (each player's own
``room_snapshot``, the victim's ``killed`` notice, the doer's ``tasks.graded``
result), never from the ``event`` records ``audit.check`` reads, so the label and
the checker are derived independently. ``label_agreement`` in the report checks
the exact planted Claim in code against its label; it should always be 1.

*Scoring.* ``evaluate`` sends every Planted Claim through the Claim Audit
pipeline (``extraction_input`` -> extractor -> ``parse_payload`` -> ``resolve``
-> ``check_claim``). A Planted Claim is correct only when the extractor returns
a Claim with exactly the planted kind, players, room, Round, Task and
``negated``, ``seen`` and ``throughout`` flags **and** the checker's verdict on
it matches the label. Accuracy is reported overall, per phrasing, per Claim
kind, per context and per truth, with Wilson 95% intervals. The planted set
depends only on the seed and the games, so the same report can be re-run for
every candidate extractor.

Every Round is stated as a number, in digits for ``plain`` and ``hedged``
phrasings and in words or ordinals for ``subtle`` ones. Planted Claims carry
neither qualifier (``seen`` and ``throughout`` are always false), so no phrasing
uses eyewitness ("I saw ...") or whole-window ("the whole time") wording.
``task_done`` Claims are about solo Tasks only (``is_solo_kind``): shared kinds
(relays, split-keys, Audits) credit players who never see a grade.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from zantine.audit.check import check_claim
from zantine.audit.claims import (
    Claim,
    ClaimDraft,
    ExtractionError,
    ExtractionInput,
    ParsedPayload,
    parse_payload,
    resolve,
)
from zantine.audit.extractors import ClaimExtractor
from zantine.audit.record import GameRecord, StatementRecord, build_record
from zantine.audit.report import extraction_input
from zantine.harness.ledger import Ledger, SpendCapReached, summarize
from zantine.harness.provider import ProviderError
from zantine.rng import GameRng
from zantine.tasks import task_kind

PLANTED_SCHEMA_VERSION = 1
PHRASINGS = ("plain", "hedged", "subtle")
KINDS = ("location", "together", "kill", "task_done")
CONTEXTS = ("standalone", "in_meeting")
# Share of location and kill Claims planted as denials ("X was not in R", "I didn't kill V").
DENIAL_SHARE = 0.25


def is_solo_kind(kind: str) -> bool:
    """A one-player Task worked with ``tasks.work``: its doer's ``tasks.graded`` is
    the whole credit. Shared kinds (relays, split-keys, Audits) also credit players
    who never see a grade, so they are left out of ``task_done`` Claims."""
    k = task_kind(kind)
    return k.seats == 1 and k.work_actions == ("tasks.work",)


Record = dict[str, Any]


@dataclass(frozen=True)
class PlantedClaim:
    id: str
    phrasing: str  # "plain", "hedged" or "subtle"
    kind: str  # the Claim kind
    context: str  # "standalone" or "in_meeting"
    game: int  # index into the games list
    meeting: int
    # The real statement this one stands in for (standalone) or is spliced into (in_meeting).
    statement_seq: int
    speaker: int
    sentence: str  # the planted sentence
    text: str  # the whole statement the extractor sees
    # The planted Claim: kind, subject, object (Seats), room, round, task, negated, seen,
    # throughout.
    expected: dict[str, Any]
    truth: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def draft(self, names: dict[int, str]) -> ClaimDraft:
        """The planted Claim as extractor output would state it."""
        e = self.expected
        return ClaimDraft(
            kind=e["kind"],
            subject=names[e["subject"]],
            object=names[e["object"]] if e["object"] is not None else None,
            room=e["room"],
            round=e["round"],
            task=e["task"],
            negated=e["negated"],
            quote=self.sentence,
            seen=e["seen"],
            throughout=e["throughout"],
        )


# Facts from observation records ---------------------------------------------------------


@dataclass(frozen=True)
class _View:
    room: str
    players: frozenset[int]


class _Facts:
    """One game's facts, read from Seats' ``observation`` records only."""

    def __init__(self, records: Sequence[Record]) -> None:
        self.first: dict[tuple[int, int], _View] = {}
        self.last: dict[tuple[int, int], _View] = {}
        # (victim, killers, room, round, seq) from each victim's ``killed`` notice.
        self.deaths: list[tuple[int, tuple[int, ...], str, int, int]] = []
        # (seat, task, round, seq) from each doer's ``tasks.graded`` result.
        self.done: list[tuple[int, str, int, int]] = []
        # Solo Tasks (``is_solo_kind``) by id, from the assignees' briefs. Only these
        # appear in ``task_done`` Claims; a Task with no brief seen is never one.
        self.solo_tasks: set[str] = set()
        header = next((r for r in records if r.get("kind") == "header"), {})
        self.rooms: list[str] = list(header.get("map", {}).get("rooms", ()))
        for o in sorted(
            (r for r in records if r.get("kind") == "observation"), key=lambda r: r["seq"]
        ):
            seat, rnd, d = o["seat"], o["round"], o["data"]
            match o["type"]:
                case "room_snapshot":
                    view = _View(d["room"], frozenset(d["players"]))
                    self.first.setdefault((seat, rnd), view)
                    self.last[(seat, rnd)] = view
                case "killed":
                    self.deaths.append((seat, tuple(d["killers"]), d["room"], rnd, o["seq"]))
                case "tasks.graded":
                    if d.get("correct"):
                        self.done.append((seat, d["task"], rnd, o["seq"]))
                case "tasks.assigned":
                    self.solo_tasks.update(t["task"] for t in d["tasks"] if is_solo_kind(t["kind"]))
                case "tasks.reassigned" if is_solo_kind(d["task"]["kind"]):
                    self.solo_tasks.add(d["task"]["task"])

    def window(self, seat: int, k: int) -> tuple[_View, _View] | None:
        """``seat``'s own view at the start and the end of Round ``k`` (None if not alive)."""
        start, end = self.last.get((seat, k - 1)), self.first.get((seat, k))
        return (start, end) if start and end else None

    def deaths_before(self, seq: int) -> list[tuple[int, tuple[int, ...], str, int, int]]:
        return [d for d in self.deaths if d[4] < seq]


@dataclass(frozen=True)
class _Spot:
    """A real statement in a real Meeting, where a Planted Claim goes."""

    game: int
    meeting: int
    round: int
    statement: StatementRecord
    speakers: tuple[int, ...]  # every Seat that speaks in the Meeting (so is alive)


Rng = GameRng
# (facts, spot, speaker, truth, denial, rng) -> expected Claim fields, or None if impossible.
Maker = Callable[[_Facts, _Spot, int, bool, bool, Rng], dict[str, Any] | None]


def _fields(kind: str, subject: int, **rest: Any) -> dict[str, Any]:
    out = {"kind": kind, "subject": subject, "object": None, "room": None, "round": None}
    return out | {"task": None, "negated": False, "seen": False, "throughout": False} | rest


def _location(f: _Facts, spot: _Spot, speaker: int, truth: bool, denial: bool, rng: Rng):
    candidates = sorted(_seen(f) - {speaker})
    subject = speaker if rng.random() < 0.5 else rng.choice(candidates or [speaker])
    rounds = [k for k in range(1, spot.round + 1) if f.window(subject, k)]
    if not rounds:
        return None
    k = rng.choice(rounds)
    start, end = f.window(subject, k) or (None, None)
    assert start and end
    there = sorted({start.room, end.room})
    # A denial is true when the positive fact is false.
    pool = there if truth != denial else [r for r in f.rooms if r not in there]
    if not pool:
        return None
    return _fields("location", subject, room=rng.choice(pool), round=k, negated=denial)


def _together(f: _Facts, spot: _Spot, speaker: int, truth: bool, denial: bool, rng: Rng):
    candidates = sorted(_seen(f) - {speaker})
    subject = speaker if rng.random() < 0.5 else rng.choice(candidates or [speaker])
    options = []
    for k in range(1, spot.round + 1):
        w = f.window(subject, k)
        if not w:
            continue
        met = w[0].players | w[1].players
        others = _seen(f) - {subject, speaker}
        pool = met - {speaker} if truth else others - met
        options += [(k, o) for o in sorted(pool)]
    if not options:
        return None
    k, other = rng.choice(options)
    return _fields("together", subject, object=other, round=k)


def _kill(f: _Facts, spot: _Spot, speaker: int, truth: bool, denial: bool, rng: Rng):
    deaths = f.deaths_before(spot.statement.seq)
    if denial:
        # "I didn't kill V": true unless the speaker is one of V's killers.
        pool = [d for d in deaths if (speaker in d[1]) != truth]
        if not pool:
            return None
        victim = rng.choice(pool)[0]
        return _fields("kill", speaker, object=victim, negated=True)
    if not deaths:
        return None
    victim, killers, room, k, _ = rng.choice(deaths)
    everyone = _seen(f)
    if truth:
        pool = [s for s in killers if s != speaker]
        if not pool:
            return None
        return _fields("kill", rng.choice(pool), object=victim, room=room, round=k)
    wrong = rng.choice(("killer", "killer", "room", "round"))
    if wrong == "room":
        rooms = [r for r in f.rooms if r != room]
        pool = [s for s in killers if s != speaker]
        if not rooms or not pool:
            return None
        return _fields("kill", rng.choice(pool), object=victim, room=rng.choice(rooms), round=k)
    if wrong == "round":
        rounds = [r for r in range(1, spot.round + 1) if r != k]
        pool = [s for s in killers if s != speaker]
        if not rounds or not pool:
            return None
        return _fields("kill", rng.choice(pool), object=victim, room=room, round=rng.choice(rounds))
    pool = sorted(everyone - set(killers) - {victim, speaker})
    if not pool:
        return None
    return _fields("kill", rng.choice(pool), object=victim, room=room, round=k)


def _task_done(f: _Facts, spot: _Spot, speaker: int, truth: bool, denial: bool, rng: Rng):
    # Grades are private, so players claim their own work: the subject is the speaker.
    mine = [
        (t, k)
        for s, t, k, seq in f.done
        if s == speaker and seq < spot.statement.seq and t in f.solo_tasks
    ]
    if truth:
        if not mine:
            return None
        task, k = rng.choice(mine)
        return _fields("task_done", speaker, task=task, round=k)
    finished = {t for t, _ in mine}
    if mine and rng.random() < 0.5:
        # A Task it did finish, but in another Round.
        task = rng.choice(mine)[0]
        done_in = {k for t, k in mine if t == task}
        rounds = [r for r in range(1, spot.round + 1) if r not in done_in]
        if rounds:
            return _fields("task_done", speaker, task=task, round=rng.choice(rounds))
    others = sorted(f.solo_tasks - finished)
    if not others:
        return None
    return _fields("task_done", speaker, task=rng.choice(others), round=rng.randint(1, spot.round))


def _seen(f: _Facts) -> set[int]:
    """Every Seat that has a ``room_snapshot`` (all of them, in practice)."""
    return {seat for seat, _ in f.first}


MAKERS: dict[str, Maker] = {
    "location": _location,
    "together": _together,
    "kill": _kill,
    "task_done": _task_done,
}


# Phrasings ------------------------------------------------------------------------------

# Template: (text, needs the subject to be the speaker). Placeholders: {S}/{s} the
# subject ("I" or a name) at the start / inside a sentence, {me} the subject as an
# object ("me" or a name), {O} the companion, {K} killer, {V} victim, {room}, {task},
# {k} the Round in digits, {kw} in words, {kth} as an ordinal.
Template = tuple[str, bool]
TEMPLATES: dict[str, dict[str, list[Template]]] = {
    "location": {
        "plain": [("{S} was in {room} in round {k}.", False)],
        "hedged": [
            ("I think {s} was in {room} in round {k}.", False),
            ("{S} was probably in {room} in round {k}.", False),
            ("Not certain, but {s} might have been in {room} in round {k}.", False),
        ],
        "subtle": [
            ("In the {kth} round, {s} was hanging around {room}.", False),
            ("{room} is where {s} spent part of round {kw}.", False),
            ("Spent some of round {kw} in {room}, if anyone's keeping track.", True),
            ("Round {kw}: {room}, that's where {s} was.", False),
        ],
    },
    "location_denial": {
        "plain": [("{S} was not in {room} in round {k}.", False)],
        "hedged": [
            ("I'm fairly sure {s} wasn't in {room} in round {k}.", False),
            ("{S} probably wasn't in {room} in round {k}.", False),
        ],
        "subtle": [
            ("{S} never set foot in {room} during the {kth} round.", False),
            ("Wherever {s} was in round {kw}, it wasn't {room}.", False),
        ],
    },
    "together": {
        "plain": [("{S} was with {O} in round {k}.", False)],
        "hedged": [
            ("I believe {s} was with {O} in round {k}.", False),
            ("{S} was probably with {O} in round {k}.", False),
        ],
        "subtle": [
            ("{O} and {s} were together in the {kth} round.", False),
            ("In round {kw} I had {O} for company.", True),
            ("{O} can vouch for me: we shared a room in round {kw}.", True),
            ("{O} crossed paths with {me} in round {kw}.", False),
        ],
    },
    "kill": {
        "plain": [("{K} killed {V} in {room} in round {k}.", False)],
        "hedged": [
            ("{K} probably killed {V} in {room} in round {k}.", False),
            ("I think {K} killed {V} in {room} in round {k}.", False),
        ],
        "subtle": [
            ("{V} was killed by {K} in {room} in the {kth} round.", False),
            ("It was {K} who got {V} in {room}, round {kw}.", False),
            ("{K} was the one who took {V} out in {room} back in round {kw}.", False),
        ],
    },
    "kill_denial": {
        "plain": [("I didn't kill {V}.", True)],
        "hedged": [],  # Hedging about one's own kill reads as a slip, not a denial.
        "subtle": [
            ("I never laid a finger on {V}.", True),
            ("{V}'s death had nothing to do with me.", True),
        ],
    },
    "task_done": {
        "plain": [("I finished {task} in round {k}.", True)],
        "hedged": [
            ("I think I finished {task} in round {k}.", True),
            ("I'm pretty sure I completed {task} in round {k}.", True),
        ],
        "subtle": [
            ("Knocked out {task} in round {kw}.", True),
            ("{task} is done; I wrapped it up in the {kth} round.", True),
            ("Took care of {task} during round {kw}, so that one's off the list.", True),
        ],
    },
}

_WORDS = (
    *("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"),
    *("eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen"),
    *("eighteen", "nineteen", "twenty"),
)
_ORDINALS = (
    *("zeroth", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth"),
    *("ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth"),
    *("sixteenth", "seventeenth", "eighteenth", "nineteenth", "twentieth"),
)


def _ordinal(k: int) -> str:
    if k < len(_ORDINALS):
        return _ORDINALS[k]
    suffix = "th" if 10 <= k % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(k % 10, "th")
    return f"{k}{suffix}"


def _variant(e: dict[str, Any]) -> str:
    return f"{e['kind']}_denial" if e["negated"] else e["kind"]


def _render(template: Template, e: dict[str, Any], speaker: int, names: dict[int, str]) -> str:
    text, _ = template
    me = e["subject"] == speaker
    subject = names[e["subject"]]
    k = e["round"]
    values = {
        "S": "I" if me else subject,
        "s": "I" if me else subject,
        "me": "me" if me else subject,
        "O": names.get(e["object"], ""),
        "K": subject,
        "V": names.get(e["object"], ""),
        "room": e["room"] or "",
        "task": e["task"] or "",
        "k": k,
        "kw": (_WORDS[k] if k < len(_WORDS) else str(k)) if k else "",
        "kth": _ordinal(k) if k else "",
    }
    return text.format(**values)


# Generation -----------------------------------------------------------------------------


def _spots(records: Sequence[GameRecord]) -> list[_Spot]:
    out = []
    for g, rec in enumerate(records):
        for m in rec.meetings:
            stmts = [s for s in rec.statements if s.meeting == m.meeting]
            speakers = tuple(sorted({s.speaker for s in stmts}))
            out += [_Spot(g, m.meeting, m.round, s, speakers) for s in stmts if s.text.strip()]
    return out


def generate(games: Sequence[Iterable[Record]], n: int = 400, seed: int = 0) -> list[PlantedClaim]:
    """``n`` Planted Claims over the games' Meetings; the same seed and games give the same set."""
    games = [list(g) for g in games]
    records = [build_record(g) for g in games]
    facts = [_Facts(g) for g in games]
    spots = _spots(records)
    if not spots:
        raise ValueError("no Meeting statements to plant Claims into")
    rng = GameRng(seed).fork("planted")
    out = []
    for i in range(n):
        phrasing = PHRASINGS[i % len(PHRASINGS)]
        truth = (i // len(PHRASINGS)) % 2 == 0
        first = (i // 6) % len(KINDS)
        # Blocks of 24 rotate through every phrasing, truth and kind; one block in four
        # is spliced into real speech.
        context = CONTEXTS[1] if (i // 24) % 4 == 3 else CONTEXTS[0]
        planted = None
        for attempt in range(len(KINDS) * 8):
            kind = KINDS[(first + attempt // 8) % len(KINDS)]
            planted = _plant(
                i, phrasing, kind, truth, context, rng.choice(spots), records, facts, rng
            )
            if planted is not None:
                break
        if planted is None:
            raise ValueError(f"could not plant Claim {i}: the games have too few facts")
        out.append(planted)
    return out


def _plant(
    i: int,
    phrasing: str,
    kind: str,
    truth: bool,
    context: str,
    spot: _Spot,
    records: Sequence[GameRecord],
    facts: Sequence[_Facts],
    rng: Rng,
) -> PlantedClaim | None:
    rec, f = records[spot.game], facts[spot.game]
    speaker = spot.statement.speaker if context == "in_meeting" else rng.choice(spot.speakers)
    denial = kind in ("location", "kill") and rng.random() < DENIAL_SHARE
    e = MAKERS[kind](f, spot, speaker, truth, denial, rng)
    if e is None:
        return None
    templates = [t for t in TEMPLATES[_variant(e)][phrasing] if e["subject"] == speaker or not t[1]]
    if not templates:
        return None
    sentence = _render(rng.choice(templates), e, speaker, rec.names)
    text = sentence
    if context == "in_meeting":
        real = spot.statement.text.strip()
        text = f"{real} {sentence}" if rng.random() < 0.5 else f"{sentence} {real}"
    return PlantedClaim(
        id=f"p{i:04d}",
        phrasing=phrasing,
        kind=kind,
        context=context,
        game=spot.game,
        meeting=spot.meeting,
        statement_seq=spot.statement.seq,
        speaker=speaker,
        sentence=sentence,
        text=text,
        expected=e,
        truth=truth,
    )


# Evaluation -----------------------------------------------------------------------------


def planted_statement(p: PlantedClaim, rec: GameRecord) -> StatementRecord:
    """The real statement with the planted speaker and text in place."""
    real = next(s for s in rec.statements if s.seq == p.statement_seq)
    return replace(real, speaker=p.speaker, text=p.text)


def matches(claim: Claim, expected: dict[str, Any]) -> bool:
    """Exactly the planted Claim: kind, players, room, Round, Task and its flags."""
    fields = ("kind", "room", "task", "negated", "seen", "throughout")
    got = (*(getattr(claim, f) for f in fields), claim.claimed_round)
    if got != (*(expected[f] for f in fields), expected["round"]):
        return False
    if claim.kind == "together":  # "A was with B" and "B was with A" are the same Claim.
        return {claim.subject, claim.object} == {expected["subject"], expected["object"]}
    return (claim.subject, claim.object) == (expected["subject"], expected["object"])


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float | None, float | None]:
    """Wilson score 95% interval for ``k`` successes in ``n`` trials."""
    if n == 0:
        return None, None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, centre - half), 6), round(min(1.0, centre + half), 6)


def stat(flags: Sequence[bool]) -> dict[str, Any]:
    k, n = sum(flags), len(flags)
    lo, hi = wilson(k, n)
    return {"correct": k, "n": n, "accuracy": round(k / n, 6) if n else None, "ci95": [lo, hi]}


def evaluate(
    games: Sequence[Iterable[Record]],
    planted: Sequence[PlantedClaim],
    extractor: ClaimExtractor,
    *,
    ledger: Ledger | None = None,
    attempts: int = 2,
) -> dict[str, Any]:
    """Run every Planted Claim through the Claim Audit pipeline with ``extractor``."""
    records = [build_record(g) for g in games]
    items: list[dict[str, Any]] = []
    aborted: dict[str, Any] | None = None
    for p in planted:
        rec = records[p.game]
        st = planted_statement(p, rec)
        want = "true" if p.truth else "false"
        label_check = check_claim(_resolve(p.draft(rec.names), f"{p.id}.label", st, rec), rec)
        item: dict[str, Any] = {
            **p.to_dict(),
            "label": want,
            "label_agrees": label_check.truth == want,
            "extracted": [],
            "rejected": [],
            "error": None,
            "matched": False,
            "correct": False,
        }
        items.append(item)
        if aborted is not None:
            item["error"] = "not processed: run aborted"
            continue
        try:
            parsed = _extract(extractor, extraction_input(st, rec), attempts)
        except (SpendCapReached, ProviderError) as exc:
            if isinstance(exc, ProviderError) and not exc.fatal:
                item["error"] = str(exc)
                continue
            reason = "spend_cap" if isinstance(exc, SpendCapReached) else "provider_error"
            aborted = {"reason": reason, "detail": str(exc), "planted": p.id}
            item["error"] = f"not processed: {exc}"
            continue
        except ExtractionError as exc:
            item["error"] = str(exc)
            continue
        item["rejected"] = list(parsed.rejected)
        if parsed.rejected:
            # As in ``run_audit``: dropped items may have been Claims, so the run is partial.
            item["error"] = (
                f"{len(parsed.rejected)} malformed Claim item(s) dropped after "
                f"{attempts} attempt(s)"
            )
        for i, draft in enumerate(parsed.drafts):
            claim = _resolve(draft, f"{p.id}.c{i}", st, rec)
            verdict = check_claim(claim, rec)
            hit = matches(claim, p.expected)
            item["extracted"].append(
                {**claim.to_dict(), "verdict": verdict.to_dict(), "match": hit}
            )
            item["matched"] |= hit
            item["correct"] |= hit and verdict.truth == want
    return summary(items, extractor, aborted=aborted, ledger=ledger)


def _resolve(draft: ClaimDraft, claim_id: str, st: StatementRecord, rec: GameRecord) -> Claim:
    return resolve(
        draft,
        claim_id=claim_id,
        statement=st,
        names=rec.names,
        rooms=rec.rooms,
        task_ids=tuple(rec.task_rooms),
    )


def _extract(extractor: ClaimExtractor, item: ExtractionInput, attempts: int) -> ParsedPayload:
    """As in ``run_audit``: one retry for unusable output, malformed Claim items or a
    transient API error; the second failure stands (malformed items: the last reply, partial)."""
    for attempt in range(1, attempts + 1):
        try:
            parsed = parse_payload(extractor.extract(item))
        except ExtractionError:
            if attempt == attempts:
                raise
            continue
        except ProviderError as exc:
            if exc.fatal or attempt == attempts:
                raise
            continue
        if not parsed.rejected or attempt == attempts:
            return parsed
    raise AssertionError("unreachable")


def summary(
    items: list[dict[str, Any]],
    extractor: ClaimExtractor,
    *,
    aborted: dict[str, Any] | None = None,
    ledger: Ledger | None = None,
) -> dict[str, Any]:
    done = [it for it in items if not str(it["error"] or "").startswith("not processed")]

    def by(key: str, order: Sequence[str]) -> dict[str, Any]:
        groups: dict[str, list[bool]] = defaultdict(list)
        for it in done:
            groups[str(it[key])].append(it["correct"])
        return {g: stat(groups[g]) for g in order if g in groups}

    errors = sum(it["error"] is not None for it in items)
    return {
        "kind": "planted_claims",
        "schema": PLANTED_SCHEMA_VERSION,
        "extractor": extractor.info(),
        "n": len(items),
        "processed": len(done),
        "complete": aborted is None and errors == 0,
        "aborted": aborted,
        "errors": errors,
        "overall": stat([it["correct"] for it in done]),
        "by_phrasing": by("phrasing", PHRASINGS),
        "by_kind": by("kind", KINDS),
        "by_context": by("context", CONTEXTS),
        "by_truth": by("label", ("true", "false")),
        # Extraction alone: the planted Claim came back, whatever its verdict.
        "extraction": stat([it["matched"] for it in done]),
        # The exact planted Claim, checked in code, against its label (should be 1).
        "label_agreement": stat([it["label_agrees"] for it in items]),
        "cost": _cost(ledger),
        "items": items,
    }


def _cost(ledger: Ledger | None) -> dict[str, Any] | None:
    """Settled cost (``cost_usd``, from reported usage) and the run's spend against the
    cap (``run_spent_usd``), which also holds the worst case of calls whose usage was
    missing or unreliable (``held_usd``): possibly billed, never confirmed."""
    if ledger is None:
        return None
    mine = [r for r in ledger.records if r.purpose == "extractor"]
    settled = summarize(mine)
    return {
        **settled,
        "spend_cap_usd": ledger.spend_cap_usd,
        "run_spent_usd": round(ledger.spent_usd, 6),
        "held_usd": round(max(0.0, ledger.spent_usd - settled["cost_usd"]), 6),
    }


def scripted_games(seed: int, games: int) -> list[list[Record]]:
    """Transcripts of scripted-bot games with seeds ``seed .. seed + games - 1``."""
    from zantine.config import GameConfig
    from zantine.runner import play

    return [play(GameConfig(seed=s)).transcript.records for s in range(seed, seed + games)]
