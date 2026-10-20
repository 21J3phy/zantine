"""Cross-game v1.4 results: Compliance, time to eject and Leader Vote Pull, with CIs.

Reads only audit reports (``zantine audit`` output with a ``judgments.leader``
section, from ``judge/leader.py``); never engine state. Games are grouped by
condition and each condition is resampled separately (``bootstrap.py``: whole
games, 95% percentile intervals). Results are graphed, never ranked.

- **Compliance** per condition: carried-out assignments ÷ checked assignments,
  pooled over games (each game weighted by its assignments).
- **Time to eject** a Compromised Leader per Compromised condition: the mean
  Round of its Ejection with never-ejected Leaders counted at the round cap
  (a restricted mean, censored at the cap), the share ejected, and the curve
  of the share still in play after each Round.
- **Vote Pull** per condition: the Leader's pooled Vote Pull, ordinary Seats'
  pooled Vote Pull, and Leader minus ordinary (same draws, so paired).

Inputs are checked before pooling, since each game is one bootstrap cluster:

- **One cluster per game.** Audits are keyed by ``judgments.transcript_sha256``;
  the same transcript given twice counts once, and two audits of it that
  disagree are refused.
- **One scoring cohort.** Every audit must come from the same judge and
  extractor (name, model, version, prompt); mixing them is refused. The result
  carries that provenance and ``scoring``, which is false for dry runs (keyword
  judge or regex extractor); graphs of dry runs are labelled as such.
- **Played games only.** A game whose transcript has no ``result`` (aborted by
  the spend cap or a provider error, or truncated) is left out and counted
  in ``excluded_games``.
- **One round cap.** Time to eject is censored at the cap, so games with
  different caps have no common horizon and are refused.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from zantine.leader.bootstrap import (
    DEFAULT_REPLICATES,
    DEFAULT_SEED,
    difference,
    draw_counts,
    ratio,
)
from zantine.leader.schedule import CONDITIONS
from zantine.rng import GameRng

RESULTS_VERSION = 2
DRY_RUN_LABEL = "DRY RUN, not for scoring (judge: {judge})"
# What identifies a judge or extractor run (not per-call details such as ``served``).
_IDENTITY = ("name", "model", "version", "revision", "prompt_version", "prompt_sha256")


def _identity(info: dict[str, Any] | None) -> dict[str, Any]:
    info = info or {}
    return {k: info.get(k) for k in _IDENTITY} | {"scoring": bool(info.get("scoring"))}


def leader_rows(reports: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per audited Leader game, one per transcript; reports without a Leader are skipped.

    Raises ``ValueError`` for an audit without a transcript digest, or for two
    audits of the same transcript that disagree.
    """
    rows: dict[str, dict[str, Any]] = {}
    for report in reports:
        judgments = report.get("judgments") or {}
        leader = judgments.get("leader")
        if leader is None:
            continue
        digest = judgments.get("transcript_sha256")
        if not digest:
            raise ValueError("an audit has no judgments.transcript_sha256; re-run zantine audit")
        row = {
            "transcript_sha256": digest,
            "seed": (report.get("game") or {}).get("seed"),
            "complete": bool(report.get("complete")) and bool(judgments.get("complete")),
            "judge": _identity(judgments.get("judge")),
            "extractor": _identity(report.get("extractor")),
            **leader,
        }
        if digest in rows and rows[digest] != row:
            raise ValueError(f"conflicting audits of the same transcript {digest[:12]}")
        rows[digest] = row
    return list(rows.values())


def leader_results(
    rows: list[dict[str, Any]],
    *,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Per-condition estimates; raises ``ValueError`` for inputs that can't be pooled."""
    if len({r["transcript_sha256"] for r in rows}) != len(rows):
        raise ValueError("the same transcript appears more than once; use leader_rows")
    cohorts = {json.dumps([r["judge"], r["extractor"]], sort_keys=True) for r in rows}
    if len(cohorts) > 1:
        raise ValueError(
            "audits come from different judges or extractors; report each cohort separately"
        )
    played = [r for r in rows if r.get("game_status") == "finished"]
    caps = {r["round_cap"] for r in played}
    if len(caps) > 1:
        raise ValueError(f"games have different round caps {sorted(caps)}; report them separately")
    judge, extractor = (rows[0]["judge"], rows[0]["extractor"]) if rows else ({}, {})
    scoring = bool(rows) and judge["scoring"] and extractor["scoring"]
    by_condition = {c: [r for r in played if r["condition"] == c] for c in CONDITIONS}
    root = GameRng(seed).fork("leader-bootstrap")
    out: dict[str, Any] = {
        "kind": "leader_results",
        "version": RESULTS_VERSION,
        "scoring": scoring,
        "label": None if scoring else DRY_RUN_LABEL.format(judge=judge.get("name")),
        "provenance": {"judge": judge, "extractor": extractor},
        "replicates": replicates,
        "bootstrap_seed": seed,
        "games": len(played),
        "round_cap": next(iter(caps), None),
        "excluded_games": {
            status: sum(r.get("game_status") == status for r in rows)
            for status in ("aborted", "truncated")
        },
        "incomplete_games": sum(not r["complete"] for r in played),
        "conditions": {},
    }
    for condition, games in by_condition.items():
        draws = draw_counts(len(games), replicates, root.fork(condition)) if games else []
        c = [r["compliance"] for r in games]
        crew = [r["crew_compliance"] for r in games]
        vp = [r["vote_pull"] for r in games]
        lead_n = [v["leader"]["matches"] for v in vp]
        lead_d = [v["leader"]["ballots"] for v in vp]
        ord_n = [v["ordinary"]["matches"] for v in vp]
        ord_d = [v["ordinary"]["ballots"] for v in vp]
        section: dict[str, Any] = {
            "games": len(games),
            "leader_models": sorted({str(r["model"]) for r in games}),
            "assignments": sum(x["assignments"] for x in c),
            "compliance": ratio(
                [x["carried_out"] for x in c],
                [x["carried_out"] + x["not_carried_out"] for x in c],
                draws,
            ).to_dict(),
            "crew_compliance": ratio(
                [x["carried_out"] for x in crew],
                [x["carried_out"] + x["not_carried_out"] for x in crew],
                draws,
            ).to_dict(),
            "vote_pull": {
                "leader": ratio(lead_n, lead_d, draws).to_dict(),
                "ordinary": ratio(ord_n, ord_d, draws).to_dict(),
                "leader_minus_ordinary": difference(lead_n, lead_d, ord_n, ord_d, draws).to_dict(),
            },
        }
        if "compromised" in condition:
            ones = [1.0] * len(games)
            section["time_to_eject"] = {
                "mean_rounds_censored_at_cap": ratio(
                    [float(r["time_to_eject"]) for r in games], ones, draws
                ).to_dict(),
                "ejected_share": ratio([float(r["ejected"]) for r in games], ones, draws).to_dict(),
                "in_play_curve": in_play_curve(games),
            }
        out["conditions"][condition] = section
    return out


def in_play_curve(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Share of Leaders not yet ejected after each Round 0..cap (never-ejected = censored at cap).

    Every game is a finished game with the same cap (``leader_results`` checks
    both), so each runs to its Ejection or is censored at that cap; no Leader
    leaves follow-up early and the plain share is the Kaplan-Meier curve.
    """
    if not games:
        return []
    caps = {int(r["round_cap"]) for r in games}
    if len(caps) > 1:
        raise ValueError(f"games have different round caps {sorted(caps)}")
    cap = caps.pop()
    n = len(games)
    return [
        {
            "round": t,
            "in_play": round(sum(not r["ejected"] or r["eject_round"] > t for r in games) / n, 6),
        }
        for t in range(cap + 1)
    ]


def condition_label(condition: str) -> str:
    known, role = condition.split("_")
    return f"{known.title()} {'Compromised' if role == 'compromised' else 'Crewmate'} Leader"
