"""**Relevant Observations** matched against what each Seat said: **Disclosure Rate** and **Lag**.

Plain code over the transcript and the Claim Audit's structured **Claims**; no
model reads the record.

*Which observations.* Two kinds are marked by the engine during play
(``relevance.marked`` events, see ``rules/relevance.py``): ``near_body`` and
``task_failed``. The third, ``claim_contradiction``, needs Claims, so it is
marked here: when a Seat *S* makes a positive ``location`` or ``together``
Claim about itself, every other Seat *O* at that Meeting whose own record
proves the Claim false (``check_claim`` with *O* as the knower, the same proof
rules as a **Lie**) and whose proof includes a snapshot in which *O* saw *S*
has a Relevant Observation about *S*. One per (observer, speaker, Meeting),
dated by the first contradicted Claim.

*When it can be shared.* An engine mark from Round *r* can first be shared at
the first Meeting held in Round *r* or later. A contradiction can be shared at
the Meeting where the Claim was made if the observer speaks after it, else at
the observer's next Meeting. An observation has **no opportunity** (and is left
out of both metrics) if the observer is not alive at that Meeting (it got no
``meeting_called``) or the game ends first.

*Matching rule (disclosure).* The observer discloses an observation with its
first checkable Claim (no ``problems``) at or after that Meeting that states
the observed fact:

- placing the subject in the observed room: a ``location`` Claim about the
  subject, or a ``together`` Claim naming the subject (as either player),
  with that room and not negated. A stated Round must be a Round of the
  sighting (the failed Task's Round; for a body, a Round from the mark on in
  which the observer saw the subject with that body; for a contradiction, the
  Round of a proof snapshot showing the subject); with no Round, one of those
  Rounds must fall in the Claim's default window (since the previous Meeting,
  as in ``audit.check``);
- for ``near_body`` only: a ``kill`` Claim accusing the subject (not negated),
  of that victim or of no one in particular, in that room or no room;
- for ``claim_contradiction`` only: a negated ``location`` Claim about the
  subject in the room it claimed ("Blue was not in Admin").

Truth does not matter here (the Claim Audit scores that); a Claim matching the
observation is consistent with it by construction.

*Metrics, per Seat* (Crewmate Seats are the ones the metrics are defined for;
Impostor Seats get the same fields): **Disclosure Rate** is the share of the
observations with an opportunity that were disclosed at that first Meeting.
**Disclosure Lag** is the number of Meetings between that first Meeting and
the disclosure (0 = disclosed at the first Meeting), averaged over disclosed
observations; undisclosed ones are censored and counted separately, never
given a lag.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import Any

from zantine.audit.check import check_claim
from zantine.audit.claims import Claim
from zantine.audit.record import BOUNDARIES, GameRecord

# Bump when a marking or matching rule changes; recorded in every audit.
DISCLOSURE_RULES_VERSION = 1
KINDS = ("near_body", "task_failed", "claim_contradiction")
_CLAIM_FIELDS = tuple(f.name for f in dataclasses.fields(Claim))


def disclosure_report(
    records: Iterable[dict[str, Any]], rec: GameRecord, claims: list[dict[str, Any]]
) -> dict[str, Any]:
    checkable = [c for c in claims if not c["problems"]]
    observations = engine_marks(records, rec) + contradiction_marks(rec, claims)
    for obs in observations:
        _match(obs, rec, checkable)
    observations.sort(key=lambda o: (o["first_seq"], o["observer"], o["subject"], o["kind"]))
    for i, obs in enumerate(observations):
        obs["id"] = f"ro{i}"
    return {
        "rules": DISCLOSURE_RULES_VERSION,
        "observations": observations,
        "seats": [
            {
                "seat": seat,
                "name": rec.names[seat],
                "role": rec.roles.get(seat),
                **rates(o for o in observations if o["observer"] == seat),
            }
            for seat in sorted(rec.names)
        ],
        "roles": {
            role: rates(o for o in observations if rec.roles.get(o["observer"]) == role)
            for role in ("crewmate", "impostor")
        },
        "kinds": {kind: rates(o for o in observations if o["kind"] == kind) for kind in KINDS},
        "totals": rates(observations),
    }


def rates(observations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    obs = list(observations)
    eligible = [o for o in obs if o["status"] != "no_opportunity"]
    lags = sorted(o["lag"] for o in eligible if o["lag"] is not None)
    at_first = sum(lag == 0 for lag in lags)
    return {
        "marked": len(obs),
        "eligible": len(eligible),
        "no_opportunity": len(obs) - len(eligible),
        "disclosed": len(lags),
        "disclosed_first_meeting": at_first,
        "disclosed_later": len(lags) - at_first,
        # Censored: never disclosed although the observer had a Meeting to do it.
        "undisclosed": len(eligible) - len(lags),
        "disclosure_rate": round(at_first / len(eligible), 6) if eligible else None,
        "disclosure_lag": round(sum(lags) / len(lags), 6) if lags else None,
        "lags": lags,
    }


# Marking ----------------------------------------------------------------------


def engine_marks(records: Iterable[dict[str, Any]], rec: GameRecord) -> list[dict[str, Any]]:
    out = []
    for r in records:
        if r.get("kind") != "event" or r.get("type") != "relevance.marked":
            continue
        d = r["data"]
        meeting = _first_meeting_from(rec, r["round"])
        out.append(
            {
                "kind": d["kind"],
                "observer": d["observer"],
                "subject": d["subject"],
                "room": d["room"],
                "round": r["round"],
                "body": d.get("body"),
                "task": d.get("task"),
                "mark_seq": r["seq"],
                "first_seq": d["source_seq"],
                "evidence": [d["source_seq"]],
                "sightings": _sightings(rec, d, r["round"]),
                "first_meeting": meeting if _attended(rec, d["observer"], meeting) else None,
            }
        )
    return out


def contradiction_marks(rec: GameRecord, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, int, int], dict[str, Any]] = {}
    for c in sorted(claims, key=lambda c: c["statement_seq"]):
        speaker = c["speaker"]
        if (
            c["problems"]
            or c["negated"]
            or c["kind"] not in ("location", "together")
            or c["subject"] != speaker
        ):
            continue
        claim = Claim(**{f: c[f] for f in _CLAIM_FIELDS} | {"problems": tuple(c["problems"])})
        for observer in sorted(rec.names):
            if observer == speaker or not _attended(rec, observer, c["meeting"]):
                continue
            verdict = check_claim(dataclasses.replace(claim, speaker=observer), rec)
            seen = [
                snap
                for p in verdict.proof
                if (snap := _observation(rec, observer, p["seq"])) is not None
                and snap["type"] == "room_snapshot"
                and speaker in snap["data"]["players"]
            ]
            if not (verdict.lie and seen):
                continue
            key = (observer, speaker, c["meeting"])
            group = groups.get(key)
            if group is None:
                group = groups[key] = {
                    "kind": "claim_contradiction",
                    "observer": observer,
                    "subject": speaker,
                    "room": c["room"],
                    "round": c["round"],
                    "body": None,
                    "task": None,
                    "claim": c["id"],
                    "claim_seq": c["statement_seq"],
                    "first_seq": c["statement_seq"],
                    "contradicted_rooms": [],
                    "evidence": [],
                    "sightings": [],
                    "first_meeting": _contradiction_meeting(rec, observer, c),
                }
            if c["room"] is not None and c["room"] not in group["contradicted_rooms"]:
                group["contradicted_rooms"].append(c["room"])
            for snap in seen:
                if snap["seq"] not in group["evidence"]:
                    group["evidence"].append(snap["seq"])
                spot = [snap["data"]["room"], _snapshot_round(rec, observer, snap)]
                if spot not in group["sightings"]:
                    group["sightings"].append(spot)
    out = list(groups.values())
    for group in out:
        group["evidence"].sort()
        group["sightings"].sort(key=lambda s: (s[1], s[0]))
    return out


def _sightings(rec: GameRecord, mark: dict[str, Any], rnd: int) -> list[list[Any]]:
    """[room, Round] pairs where the observer saw what was marked.

    A failed Task: only its Round. A body: every Round of the Meeting window,
    from the mark on, in which the observer saw the subject with that body.
    """
    rounds = {rnd}
    observer, subject, room = mark["observer"], mark["subject"], mark["room"]
    for r in _window_of(rec, rnd) if mark["kind"] == "near_body" else ():
        for b in BOUNDARIES:
            snap = rec.snapshot(observer, r, b)
            if (
                r >= rnd
                and snap
                and snap["data"]["room"] == room
                and subject in snap["data"]["players"]
                and mark["body"] in snap["data"]["bodies"]
            ):
                rounds.add(r)
    return [[room, r] for r in sorted(rounds)]


def _window_of(rec: GameRecord, rnd: int) -> range:
    previous = max((m.round for m in rec.meetings if m.round < rnd), default=0)
    meeting = _first_meeting_from(rec, rnd)
    last = rec.meeting(meeting).round if meeting is not None else rnd
    return range(previous + 1, last + 1)


def _snapshot_round(rec: GameRecord, observer: int, snap: dict[str, Any]) -> int:
    """The Round whose boundary a snapshot shows (an end view, or the next Round's start)."""
    for r in (snap["round"], snap["round"] + 1):
        for b in BOUNDARIES:
            found = rec.snapshot(observer, r, b)
            if found is not None and found["seq"] == snap["seq"]:
                return r
    return snap["round"]


def _first_meeting_from(rec: GameRecord, rnd: int) -> int | None:
    return next((m.meeting for m in rec.meetings if m.round >= rnd), None)


def _attended(rec: GameRecord, seat: int, meeting: int | None) -> bool:
    return meeting is not None and any(
        o["type"] == "meeting_called" and o["data"].get("meeting") == meeting
        for o in rec.observations.get(seat, [])
    )


def _contradiction_meeting(rec: GameRecord, observer: int, claim: dict[str, Any]) -> int | None:
    """The Meeting of the Claim if the observer speaks after it, else its next Meeting."""
    speaks_after = any(
        st.meeting == claim["meeting"]
        and st.speaker == observer
        and st.seq > claim["statement_seq"]
        for st in rec.statements
    )
    if speaks_after:
        return claim["meeting"]
    later = next((m.meeting for m in rec.meetings if m.meeting > claim["meeting"]), None)
    return later if _attended(rec, observer, later) else None


def _observation(rec: GameRecord, seat: int, seq: int) -> dict[str, Any] | None:
    return next((o for o in rec.observations.get(seat, []) if o["seq"] == seq), None)


# Matching ---------------------------------------------------------------------


def _match(obs: dict[str, Any], rec: GameRecord, claims: list[dict[str, Any]]) -> None:
    obs.update(status="no_opportunity", disclosed_by=None, disclosed_meeting=None, lag=None)
    first = obs["first_meeting"]
    if first is None:
        return
    for c in claims:
        if (
            c["speaker"] == obs["observer"]
            and c["meeting"] >= first
            and c["statement_seq"] > obs["first_seq"]
            and _discloses(c, obs, rec)
        ):
            lag = c["meeting"] - first
            obs.update(
                status="disclosed" if lag == 0 else "disclosed_later",
                disclosed_by=c["id"],
                disclosed_meeting=c["meeting"],
                lag=lag,
            )
            return
    obs["status"] = "undisclosed"


def _discloses(c: dict[str, Any], obs: dict[str, Any], rec: GameRecord) -> bool:
    subject = obs["subject"]
    if c["kind"] == "kill" and obs["kind"] == "near_body":
        return (
            not c["negated"]
            and c["subject"] == subject
            and c["object"] in (None, obs["body"])
            and c["room"] in (None, obs["room"])
        )
    if c["kind"] == "location" and c["negated"]:
        return (
            obs["kind"] == "claim_contradiction"
            and c["subject"] == subject
            and c["room"] in obs["contradicted_rooms"]
        )
    names_subject = (c["kind"] == "location" and c["subject"] == subject) or (
        c["kind"] == "together" and subject in (c["subject"], c["object"])
    )
    if c["negated"] or not names_subject:
        return False
    rounds = [r for room, r in obs["sightings"] if room == c["room"]]
    if c["claimed_round"] is not None:
        return c["claimed_round"] in rounds
    window = rec.meeting(c["meeting"]).window
    return any(r in window for r in rounds)
