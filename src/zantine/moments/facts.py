"""The facts of one game that Moment detectors read, rebuilt from its transcript.

Reads ``header``, ``event`` and ``result`` records only (never engine
internals), plus the game's Claim Audit report when there is one. Everything
here is ground truth about this one game.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, fields
from typing import Any

from zantine.audit.check import check_claim
from zantine.audit.claims import Claim
from zantine.audit.record import build_record
from zantine.moments.captions import join


@dataclass
class MeetingFacts:
    meeting: int
    round: int
    seq: int
    # Each voter's final vote (the last attempt; a forfeited vote is a skip).
    votes: dict[int, int | None] = field(default_factory=dict)
    counts: dict[int, int] = field(default_factory=dict)
    skips: int = 0
    ejected: int | None = None
    # (seq, speaker, text) for every statement, in order.
    statements: list[tuple[int, int, str]] = field(default_factory=list)
    containment: bool = False
    # The vote was counted (a ``vote_tally`` exists); False if the transcript
    # stops mid-Meeting, e.g. a live run aborted during discussion or voting.
    tallied: bool = False

    def margin(self) -> tuple[int, int] | None:
        """For an Ejection: the winning count and the strongest alternative
        (the runner-up's count or the skips, whichever is larger)."""
        if self.ejected is None:
            return None
        top = self.counts.get(self.ejected, 0)
        others = [n for seat, n in self.counts.items() if seat != self.ejected]
        return top, max([self.skips, *others])

    def runner_up(self) -> int | None:
        """The seat with the most votes after the ejected one (None if skips led)."""
        _, second = self.margin() or (0, 0)
        best = [s for s, n in self.counts.items() if s != self.ejected and n == second]
        return min(best) if best and second > self.skips else None


@dataclass(frozen=True)
class KillFacts:
    seq: int
    round: int
    room: str
    victim: int
    killers: tuple[int, ...]


@dataclass
class CrisisFacts:
    number: int
    started: int
    deadline: int
    resolved: int | None = None
    resolved_seq: int | None = None
    fixers: tuple[int, ...] = ()
    failed: int | None = None


@dataclass
class GameFacts:
    seed: int | None
    names: dict[int, str]
    roles: dict[int, str]
    modules: tuple[str, ...]
    meetings: dict[int, MeetingFacts]
    kills: list[KillFacts]
    crises: dict[int, CrisisFacts]
    # How each seat left the game: ("killed", round) or ("ejected", round).
    fates: dict[int, tuple[str, int]]
    last_round: int
    winner: str | None = None
    win_reason: str | None = None
    header: dict[str, Any] = field(default_factory=dict)
    # seq -> (meeting, pass_index, order, speaker, text) for every statement.
    statements: dict[int, tuple[int, int, int, int, str]] = field(default_factory=dict)
    audit: dict[str, Any] | None = None
    # Why a given audit report was not used (it belongs to another transcript).
    audit_problem: str | None = None

    def name(self, seat: int | None) -> str:
        return "nobody" if seat is None else self.names.get(seat, f"Seat {seat}")

    def role(self, seat: int) -> str:
        return self.roles.get(seat, "unknown")

    def who(self, seat: int) -> str:
        """``Impostor Red`` / ``Crewmate Blue``."""
        role = self.roles.get(seat)
        return f"{role.capitalize()} {self.name(seat)}" if role else self.name(seat)

    def who_all(self, seats: Iterable[int]) -> str:
        """``Impostors Red and Blue``; mixed roles are named one by one."""
        seats = list(seats)
        roles = {self.roles.get(s) for s in seats}
        if len(seats) > 1 and len(roles) == 1 and None not in roles:
            (role,) = roles
            return f"{role.capitalize()}s {join([self.name(s) for s in seats])}"
        return join([self.who(s) for s in seats])

    def quoted(self, claim: dict[str, Any]) -> bool:
        """The Claim's quote is non-blank and really is in its statement in this
        transcript, said by its speaker (an extractor can invent a quote)."""
        quote = _normal(claim.get("quote") or "")
        st = self.statements.get(claim.get("statement_seq", -1))
        return (
            bool(quote)
            and bool(claim.get("quote_in_text"))
            and st is not None
            and st[3] == claim.get("speaker")
            and quote in _normal(st[4])
        )

    def alive_at_end(self, seat: int) -> bool:
        return seat not in self.fates


def build_facts(
    records: Iterable[dict[str, Any]], audit: dict[str, Any] | None = None
) -> GameFacts:
    records = sorted(
        (r for r in records if isinstance(r, dict) and "kind" in r), key=lambda r: r.get("seq", 0)
    )
    header = next((r for r in records if r["kind"] == "header"), {})
    seats = header.get("seats", [])
    facts = GameFacts(
        seed=header.get("seed"),
        names={s["seat"]: s["name"] for s in seats},
        roles={s["seat"]: s["role"] for s in seats},
        modules=tuple(header.get("modules", ())),
        meetings={},
        kills=[],
        crises={},
        fates={},
        last_round=0,
        header=header,
        audit=audit,
    )
    for r in records:
        if r["kind"] == "result":
            facts.winner, facts.win_reason = r.get("winner"), r.get("reason")
            facts.last_round = max(facts.last_round, r.get("rounds") or 0)
            continue
        if r["kind"] != "event":
            continue
        rnd, type, data = r["round"], r["type"], r["data"]
        facts.last_round = max(facts.last_round, rnd)
        match type:
            case "kill":
                kill = KillFacts(
                    r["seq"], rnd, data["room"], data["victim"], tuple(data["killers"])
                )
                facts.kills.append(kill)
                facts.fates[kill.victim] = ("killed", rnd)
            case "meeting_started":
                m = data["meeting"]
                facts.meetings[m] = MeetingFacts(m, rnd, r["seq"])
            case "statement":
                facts.meetings[data["meeting"]].statements.append(
                    (r["seq"], data["speaker"], data["text"])
                )
                facts.statements[r["seq"]] = (
                    data["meeting"],
                    data["pass_index"],
                    data["order"],
                    data["speaker"],
                    data["text"],
                )
            case "vote":
                meeting = facts.meetings[data["meeting"]]
                value = data.get("value") if data.get("valid") else None
                meeting.votes[data["seat"]] = value if isinstance(value, int) else None
            case "vote_tally":
                meeting = facts.meetings[data["meeting"]]
                meeting.counts = {c["seat"]: c["votes"] for c in data["counts"]}
                meeting.skips, meeting.ejected = data["skips"], data["ejected"]
                meeting.tallied = True
            case "ejection":
                facts.fates[data["seat"]] = ("ejected", rnd)
            case "containment":
                facts.meetings[data["meeting"]].containment = True
            case "crisis.started":
                n = data["number"]
                facts.crises[n] = CrisisFacts(n, rnd, data["deadline"])
            case "crisis.resolved" if data["number"] in facts.crises:
                crisis = facts.crises[data["number"]]
                crisis.resolved, crisis.resolved_seq = rnd, r["seq"]
                crisis.fixers = tuple(data.get("fixers", ()))
            case "crisis.failed" if data["number"] in facts.crises:
                facts.crises[data["number"]].failed = rnd
    if audit is not None:
        facts.audit_problem = audit_mismatch(facts, audit, records)
        if facts.audit_problem is not None:
            facts.audit = None
    return facts


def audit_mismatch(
    facts: GameFacts, audit: dict[str, Any], records: list[dict[str, Any]]
) -> str | None:
    """Why ``audit`` is not this transcript's report, or None if it is.

    The seed alone is not enough (runs reuse seeds, and a rerun can overwrite a
    transcript next to an old ``.audit.json``). Every audited statement must
    match this transcript's statement at the same ``seq`` (and vice versa), and
    every Claim's verdict, including its proof, must be what checking that Claim
    against this transcript gives: same speech over different events must not
    reuse a stale Lie.
    """
    seed = audit.get("game", {}).get("seed")
    if seed != facts.seed:
        return f"the audit report is for seed {seed}, this transcript is seed {facts.seed}"
    audited = {
        st.get("seq"): (
            st.get("meeting"),
            st.get("pass_index"),
            st.get("order"),
            st.get("speaker"),
            st.get("text"),
        )
        for st in audit.get("statements", [])
    }
    for seq in sorted(set(audited) | set(facts.statements)):
        if audited.get(seq) != facts.statements.get(seq):
            return f"the audit report's statements differ from this transcript's (seq {seq})"
    rec = build_record(records)
    for c in audit.get("claims", []):
        try:
            claim = Claim(**{f.name: c[f.name] for f in fields(Claim) if f.name != "problems"})
            claim = Claim(**{**claim.__dict__, "problems": tuple(c.get("problems", ()))})
            verdict = _plain(check_claim(claim, rec).to_dict())
        except (KeyError, TypeError, ValueError, StopIteration):
            return f"the audit report's Claim {c.get('id')} can't be checked on this transcript"
        if verdict != _plain(c.get("verdict")):
            return f"the audit report's verdict for Claim {c.get('id')} differs on this transcript"
    return None


def _plain(value: Any) -> Any:
    """As it reads back from JSON (tuples become lists)."""
    return json.loads(json.dumps(value, sort_keys=True))


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()
