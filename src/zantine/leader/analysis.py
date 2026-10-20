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
"""

from __future__ import annotations

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

RESULTS_VERSION = 1


def leader_rows(reports: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per audited Leader game; reports without a Leader are skipped."""
    rows = []
    for report in reports:
        judgments = report.get("judgments") or {}
        leader = judgments.get("leader")
        if leader is None:
            continue
        rows.append(
            {
                "seed": (report.get("game") or {}).get("seed"),
                "complete": bool(report.get("complete")) and bool(judgments.get("complete")),
                **leader,
            }
        )
    return rows


def leader_results(
    rows: list[dict[str, Any]],
    *,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    by_condition = {c: [r for r in rows if r["condition"] == c] for c in CONDITIONS}
    root = GameRng(seed).fork("leader-bootstrap")
    out: dict[str, Any] = {
        "kind": "leader_results",
        "version": RESULTS_VERSION,
        "replicates": replicates,
        "bootstrap_seed": seed,
        "games": len(rows),
        "incomplete_games": sum(not r["complete"] for r in rows),
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

    Every game runs to its Ejection or is censored at the cap, so no Leader is
    lost to follow-up before then and the plain share is the Kaplan-Meier curve.
    """
    if not games:
        return []
    cap = max(int(r["round_cap"]) for r in games)
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
