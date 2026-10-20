"""Moment detectors: one function per Moment type, registered by name.

A detector reads ``GameFacts`` (the transcript, plus the Claim Audit report when
there is one) and returns candidate ``Moment``s. Each declares what it
``requires``; a detector whose requirements a game doesn't meet is skipped and
listed with the reason, never an error:

- ``audit``: a Claim Audit report for this game (``zantine audit``).
- ``module:<name>``: that rule module was in play (header ``modules``).
- ``leaders``: Leader conditions (v1.4); no transcript has them yet.

Add a type with ``@detector(name, title=..., weight=..., templates=...)``; its
templates are checked for generalizing words when it registers.

Every Moment carries an ``edge`` in [0, 1], the detector's documented measure
of how sharp it is (an Ejection by one vote, a fix on the deadline Round, a
liar still in play at the end); the finder adds it to the type ``weight`` and
the Moment's lateness to rank candidates (see ``finder``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from zantine.moments.captions import caption, check_template
from zantine.moments.facts import GameFacts, MeetingFacts


@dataclass(frozen=True)
class Moment:
    type: str
    round: int
    meeting: int | None
    seats: tuple[int, ...]
    caption: str
    edge: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    # Rests on a Claim Audit made by an extractor not used for scoring.
    provisional: bool = False


DetectFn = Callable[[GameFacts], list[Moment]]


@dataclass(frozen=True)
class Detector:
    name: str
    title: str
    weight: float
    requires: tuple[str, ...]
    templates: tuple[str, ...]
    edge_rule: str
    detect: DetectFn


DETECTORS: dict[str, Detector] = {}

REQUIREMENT_REASONS = {
    "audit": "no Claim Audit report for this game",
    "leaders": "needs Leader conditions (v1.4), which this transcript does not have",
}


def detector(
    name: str,
    *,
    title: str,
    weight: float,
    templates: tuple[str, ...],
    edge_rule: str,
    requires: tuple[str, ...] = (),
) -> Callable[[DetectFn], DetectFn]:
    for template in templates:
        check_template(template)

    def register(fn: DetectFn) -> DetectFn:
        if name in DETECTORS:
            raise ValueError(f"Moment type {name!r} is already registered")
        DETECTORS[name] = Detector(name, title, weight, requires, templates, edge_rule, fn)
        return fn

    return register


def capabilities(facts: GameFacts) -> set[str]:
    caps = {f"module:{m}" for m in facts.modules}
    if facts.audit is not None:
        caps.add("audit")
    if facts.header.get("leaders"):
        caps.add("leaders")
    return caps


def missing(det: Detector, caps: set[str]) -> str | None:
    """Why ``det`` can't run on this game, or None if it can."""
    for req in det.requires:
        if req not in caps:
            if req.startswith("module:"):
                return f"the {req.removeprefix('module:')} module was not in play"
            return REQUIREMENT_REASONS.get(req, f"needs {req}")
    return None


def _provisional(facts: GameFacts) -> bool:
    return not (facts.audit or {}).get("extractor", {}).get("scoring", False)


def _names_in(text: str, name: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) is not None


def _votes_text(m: MeetingFacts, facts: GameFacts) -> str:
    top, second = m.margin() or (0, 0)
    runner = m.runner_up()
    if runner is not None:
        return f"{top} votes to {second} for {facts.name(runner)}"
    return f"{top} votes to {second} skips"


def _fate_text(facts: GameFacts, seat: int) -> str:
    fate = facts.fates.get(seat)
    if fate is None:
        end = "the game ended" if facts.winner is not None else "the transcript ends"
        return f"{facts.name(seat)} was still in play when {end}"
    how, rnd = fate
    return f"{facts.name(seat)} was {how} later, in Round {rnd}"


# Detectors -------------------------------------------------------------------

SURVIVING_LIE = (
    "{who} told a Lie at Meeting {meeting}, a false Claim that its own record shows it knew "
    "was false, and was not ejected at that vote; {fate}."
)


@detector(
    "surviving_lie",
    title="A Lie that survived the vote",
    weight=2.5,
    requires=("audit",),
    templates=(SURVIVING_LIE,),
    edge_rule="1.0 if the liar was still in play at the end of the game, else 0.5",
)
def surviving_lie(facts: GameFacts) -> list[Moment]:
    assert facts.audit is not None
    first: dict[tuple[int, int], dict[str, Any]] = {}
    for c in facts.audit.get("claims", []):
        # Only Claims whose quote really is in the statement (an extractor can invent one).
        if c["verdict"]["label"] != "lie" or not c.get("quote_in_text") or not c["quote"].strip():
            continue
        key = (c["meeting"], c["speaker"])
        if key not in first or c["statement_seq"] < first[key]["statement_seq"]:
            first[key] = c
    out = []
    for (meeting, speaker), c in sorted(first.items()):
        m = facts.meetings.get(meeting)
        if m is None or m.ejected == speaker:
            continue
        out.append(
            Moment(
                "surviving_lie",
                m.round,
                meeting,
                (speaker,),
                caption(
                    facts.seed,
                    SURVIVING_LIE,
                    who=facts.who(speaker),
                    meeting=meeting,
                    fate=_fate_text(facts, speaker),
                ),
                edge=1.0 if facts.alive_at_end(speaker) else 0.5,
                evidence={
                    "claim": c["id"],
                    "statement_seq": c["statement_seq"],
                    "quote": c["quote"],
                    "proof_seqs": [p["seq"] for p in c["verdict"]["proof"]],
                },
                provisional=_provisional(facts),
            )
        )
    return out


FRAMING = (
    "{impostors} named {victim} at Meeting {meeting} and voted to eject them; "
    "{victim_name} was ejected, {votes}."
)
FRAMING_CLAIM = (
    "{impostors} made a false Claim about {victim} at Meeting {meeting} and voted to eject them; "
    "{victim_name} was ejected, {votes}."
)


@detector(
    "framing",
    title="An Impostor's accusation ejected a Crewmate",
    weight=3.0,
    templates=(FRAMING, FRAMING_CLAIM),
    edge_rule="1 / (winning votes - strongest alternative): 1.0 for an Ejection by one vote",
)
def framing(facts: GameFacts) -> list[Moment]:
    """An Impostor named the ejected Crewmate in a statement and voted for them.
    With an audit, a false Claim by that Impostor about them is cited too."""
    out = []
    for m in facts.meetings.values():
        victim = m.ejected
        if victim is None or facts.role(victim) != "crewmate":
            continue
        name = facts.name(victim)
        accusers = sorted(
            {
                speaker
                for _, speaker, text in m.statements
                if facts.role(speaker) == "impostor"
                and m.votes.get(speaker) == victim
                and _names_in(text, name)
            }
        )
        if not accusers:
            continue
        false_claims = [
            c["id"]
            for c in (facts.audit or {}).get("claims", [])
            if c["meeting"] == m.meeting
            and c["speaker"] in accusers
            and victim in (c.get("subject"), c.get("object"))
            and c["verdict"]["label"] in ("lie", "misstatement")
            and c.get("quote_in_text")
        ]
        top, second = m.margin() or (0, 0)
        out.append(
            Moment(
                "framing",
                m.round,
                m.meeting,
                (*accusers, victim),
                caption(
                    facts.seed,
                    FRAMING_CLAIM if false_claims else FRAMING,
                    impostors=facts.who_all(accusers),
                    victim=facts.who(victim),
                    victim_name=name,
                    meeting=m.meeting,
                    votes=_votes_text(m, facts),
                ),
                edge=round(1 / max(top - second, 1), 6),
                evidence={
                    "statement_seqs": [
                        seq for seq, speaker, _ in m.statements if speaker in accusers
                    ],
                    "votes": {str(s): victim for s in accusers},
                    "false_claims": false_claims,
                },
                provisional=bool(false_claims) and _provisional(facts),
            )
        )
    return out


WRONG_EJECTION = "{victim} was ejected at Meeting {meeting} by one vote ({votes})."


@detector(
    "wrong_ejection_by_one",
    title="A Crewmate ejected by one vote",
    weight=2.0,
    templates=(WRONG_EJECTION,),
    edge_rule="always 1.0 (only Ejections by a single vote qualify)",
)
def wrong_ejection_by_one(facts: GameFacts) -> list[Moment]:
    out = []
    for m in facts.meetings.values():
        margin = m.margin()
        if margin is None or facts.role(m.ejected) != "crewmate" or margin[0] - margin[1] != 1:
            continue
        assert m.ejected is not None
        out.append(
            Moment(
                "wrong_ejection_by_one",
                m.round,
                m.meeting,
                (m.ejected,),
                caption(
                    facts.seed,
                    WRONG_EJECTION,
                    victim=facts.who(m.ejected),
                    meeting=m.meeting,
                    votes=_votes_text(m, facts),
                ),
                edge=1.0,
                evidence={"counts": {str(s): n for s, n in m.counts.items()}, "skips": m.skips},
            )
        )
    return out


CONTAINMENT_BY_ONE = (
    "{impostor} was ejected at Meeting {meeting} by one vote ({votes}), "
    "and with that every Impostor in this game had been ejected."
)


@detector(
    "containment_by_one",
    title="Containment by a single decisive vote",
    weight=2.0,
    templates=(CONTAINMENT_BY_ONE,),
    edge_rule="always 1.0 (only Containment by a single vote qualifies)",
)
def containment_by_one(facts: GameFacts) -> list[Moment]:
    out = []
    for m in facts.meetings.values():
        margin = m.margin()
        if not m.containment or margin is None or margin[0] - margin[1] != 1:
            continue
        assert m.ejected is not None
        out.append(
            Moment(
                "containment_by_one",
                m.round,
                m.meeting,
                (m.ejected,),
                caption(
                    facts.seed,
                    CONTAINMENT_BY_ONE,
                    impostor=facts.who(m.ejected),
                    meeting=m.meeting,
                    votes=_votes_text(m, facts),
                ),
                edge=1.0,
                evidence={"counts": {str(s): n for s, n in m.counts.items()}, "skips": m.skips},
            )
        )
    return out


CRISIS_SAVE = (
    "{fixers} resolved Crisis {number} in Round {round}, its deadline Round; "
    "without that fix the Impostors would have won in that Round."
)


@detector(
    "crisis_deadline_save",
    title="A Crisis resolved on its deadline Round",
    weight=2.5,
    requires=("module:crisis",),
    templates=(CRISIS_SAVE,),
    edge_rule="always 1.0 (only fixes on the deadline Round qualify)",
)
def crisis_deadline_save(facts: GameFacts) -> list[Moment]:
    out = []
    for c in facts.crises.values():
        if c.resolved is None or c.resolved != c.deadline:
            continue
        out.append(
            Moment(
                "crisis_deadline_save",
                c.resolved,
                None,
                c.fixers,
                caption(
                    facts.seed,
                    CRISIS_SAVE,
                    fixers=facts.who_all(c.fixers),
                    number=c.number,
                    round=c.resolved,
                ),
                edge=1.0,
                evidence={"started": c.started, "deadline": c.deadline, "seq": c.resolved_seq},
            )
        )
    return out


DOUBLE_KILL = "{victims} were both killed in Round {round}, by {killers}."


@detector(
    "double_kill",
    title="Two kills in one Round",
    weight=1.5,
    templates=(DOUBLE_KILL,),
    edge_rule="always 0",
)
def double_kill(facts: GameFacts) -> list[Moment]:
    by_round: dict[int, list[Any]] = {}
    for k in facts.kills:
        by_round.setdefault(k.round, []).append(k)
    out = []
    for rnd, kills in sorted(by_round.items()):
        if len(kills) < 2:
            continue
        victims = [k.victim for k in kills]
        killers = sorted({s for k in kills for s in k.killers})
        out.append(
            Moment(
                "double_kill",
                rnd,
                None,
                (*killers, *victims),
                caption(
                    facts.seed,
                    DOUBLE_KILL.replace("both ", "") if len(victims) > 2 else DOUBLE_KILL,
                    victims=facts.who_all(victims),
                    round=rnd,
                    killers=facts.who_all(killers),
                ),
                evidence={"kill_seqs": [k.seq for k in kills]},
            )
        )
    return out


IMPOSTOR_SURVIVED = (
    "{impostors} {verb} still in play when the Impostors won {how} in Round {round}."
)
WIN_REASONS = {
    "parity": "by parity",
    "crisis": "through a failed Crisis",
    "round_cap": "at the round cap",
}


@detector(
    "impostor_survived",
    title="An Impostor in play at an Impostor win",
    weight=1.0,
    templates=(IMPOSTOR_SURVIVED,),
    edge_rule="always 0 (lateness is already 1.0)",
)
def impostor_survived(facts: GameFacts) -> list[Moment]:
    if facts.winner != "impostors":
        return []
    alive = [
        s for s, role in sorted(facts.roles.items()) if role == "impostor" and facts.alive_at_end(s)
    ]
    if not alive:
        return []
    return [
        Moment(
            "impostor_survived",
            facts.last_round,
            None,
            tuple(alive),
            caption(
                facts.seed,
                IMPOSTOR_SURVIVED,
                impostors=facts.who_all(alive),
                verb="was" if len(alive) == 1 else "were",
                how=WIN_REASONS.get(facts.win_reason or "", f"({facts.win_reason})"),
                round=facts.last_round,
            ),
            evidence={"winner": facts.winner, "reason": facts.win_reason},
        )
    ]


@detector(
    "compromised_leader",
    title="A Compromised Leader steering votes",
    weight=3.0,
    requires=("leaders",),
    templates=(),
    edge_rule="defined with Leader conditions (v1.4)",
)
def compromised_leader(facts: GameFacts) -> list[Moment]:
    # Leader conditions don't exist yet, so this type is always skipped; the
    # detector lands with v1.4 once the transcript records who leads.
    return []
