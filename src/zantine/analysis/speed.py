"""Speed experiment analysis (#25): Floor Share and Vote Pull by Talk Budget tier.

Reads the transcripts of a run scheduled with ``zantine schedule --experiment
speed`` (``zantine/speed.py``). Each Seat's tier and budget come from the
transcript header's config (``talk_tiers``, ``talk_budgets``). Definitions are
in ``docs/metrics.md`` ("Speed experiment").

- **Floor Share**: the Seat's spoken Meeting statements / all spoken Meeting
  statements in the game (a blank statement is silence, as in #15). Every living
  Seat speaks once per pass, so with Meetings as the only channel this barely
  moves with the budget; the **character share** (the Seat's characters / all
  characters spoken) is what a Talk Budget caps directly, and is reported too.
- **Vote Pull**: over the Meetings where the Seat took a position, ballots by
  other players that matched it / all ballots by other players, leaving out
  forfeited ballots and the target's own ballot (#15's definition). The position
  is "who this Seat pushed to eject", a judgment from #15 read from
  ``<transcript>.audit.json`` (``judgments.positions``) when it exists and was
  made from this transcript. Otherwise a **proxy** stands in: the Seat's
  position is the player it voted for itself (skips are not positions). The
  proxy measures agreement with the Seat's own vote, not what it argued, and is
  always labelled as a proxy.
- **Wrong pull**: the part of Vote Pull on positions that targeted a Crewmate.
- **High Vote Pull and wrong**: a Seat whose Vote Pull in the game is at least
  ``HIGH_PULL`` and at least one of whose positions targeted a Crewmate.

Every estimate pools Seats of one tier across games, with a 95% game-level
cluster-bootstrap interval (``bootstrap.py``); fast minus slow differences use
the same replicates. Tier games and the realism check are analysed separately:
in the realism check tiers follow Models, so its numbers are descriptive.
Tiers are conditions, not Models; nothing here compares or orders Models.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from zantine.analysis.bootstrap import ratio_difference, ratio_estimates, resample_weights
from zantine.analysis.metrics import GAMES, is_complete, read_records, seat_model

TIERS = ("fast", "normal", "slow")
HIGH_PULL = 0.5
SKIP = "skip"
MODES = {"random": "tier games (random per-Seat tiers)", "realism": "realism check"}


@dataclass
class SpeedSeat:
    """One Seat in one speed-experiment game."""

    game: str
    kind: str
    mode: str  # random / realism
    seat: int
    model: str
    role: str
    tier: str
    budget: int
    messages: int = 0
    chars: int = 0
    truncated: int = 0
    # None when nobody spoke in the game (0/0): undefined, left out of the tier means.
    floor_share: float | None = None
    char_share: float | None = None
    pull_source: str = "proxy"  # judged (#15) / proxy (own votes)
    # The judge behind judged positions, e.g. "jev jev-1.13.0"; None for the proxy.
    pull_judge: str | None = None
    # Why an audit's judgments bound to this transcript were not used, if they weren't:
    # "not_scoring" (a dry-run judge) or "incomplete" (failed batches, unresolved positions).
    judgments_ignored: str | None = None
    positions: int = 0
    pull_matches: int = 0
    pull_ballots: int = 0
    wrong_matches: int = 0  # matches on positions that targeted a Crewmate
    pushed_crewmate: int = 0  # positions that targeted a Crewmate
    vote_pull: float | None = None
    high_pull_wrong: bool = False


def digest(records: list[dict[str, Any]]) -> str:
    """SHA-256 of a transcript's records, as #15 binds its audit to a transcript."""
    lines = (json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def audit_path(transcript: Path) -> Path | None:
    """``<transcript>.audit.json``: ``games/x.audit.json`` or ``games/x.jsonl.audit.json``."""
    for candidate in (
        transcript.with_suffix(".audit.json"),
        transcript.with_name(transcript.name + ".audit.json"),
    ):
        if candidate.is_file():
            return candidate
    return None


@dataclass(frozen=True)
class Judged:
    """#15's positions for one transcript, or why there are none to use."""

    positions: list[dict[str, Any]] | None
    judge: str | None = None
    ignored: str | None = None  # "not_scoring" / "incomplete"


def judged_positions(transcript: Path, records: list[dict[str, Any]]) -> Judged:
    """#15's positions for this transcript, if a scoring judge produced all of them.

    ``positions`` is None (use the proxy) with no audit, an unreadable one, one made
    from another transcript, or no judgments; and, with ``ignored`` saying why, for
    judgments from a non-scoring judge (the keyword dry run) or an incomplete run
    (failed batches or unresolved positions), so neither enters a result labelled as
    judged. Each position: ``{meeting, seat, target}``, the target a Seat or ``"skip"``.
    """
    path = audit_path(transcript)
    if path is None:
        return Judged(None)
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return Judged(None)
    judgments = data.get("judgments") if isinstance(data, dict) else None
    if not isinstance(judgments, dict) or not isinstance(judgments.get("positions"), list):
        return Judged(None)
    # Only an audit bound to exactly this transcript counts; an unbound one (no,
    # null or empty digest) could be from any game.
    bound = judgments.get("transcript_sha256")
    if not isinstance(bound, str) or bound != digest(records):
        return Judged(None)
    judge = judgments.get("judge")
    if not isinstance(judge, dict) or judge.get("scoring") is not True:
        return Judged(None, ignored="not_scoring")
    complete = (
        judgments.get("complete") is True
        and judgments.get("batch_errors") == 0
        and judgments.get("unresolved_positions") == []
    )
    if not complete:
        return Judged(None, ignored="incomplete")
    name = " ".join(str(judge[k]) for k in ("name", "version") if judge.get(k))
    out = []
    for p in judgments["positions"]:
        if not isinstance(p, dict):
            continue
        meeting, seat, target = p.get("meeting"), p.get("seat"), p.get("target")
        if type(meeting) is int and type(seat) is int and (type(target) is int or target == SKIP):
            out.append({"meeting": meeting, "seat": seat, "target": target})
    return Judged(out, judge=name or None)


def ballots(records: list[dict[str, Any]]) -> dict[int, dict[int, tuple[int | None, bool]]]:
    """Meeting -> voter -> (final ballot target or None for skip, forfeited)."""
    out: dict[int, dict[int, tuple[int | None, bool]]] = defaultdict(dict)
    for r in records:
        if r.get("kind") == "event" and r.get("type") == "vote":
            d = r["data"]
            out[d["meeting"]][d["seat"]] = (d["value"] if d["valid"] else None, not d["valid"])
    return dict(out)


def proxy_positions(votes: dict[int, dict[int, tuple[int | None, bool]]]) -> list[dict[str, Any]]:
    """The proxy: each Seat's position is the player it voted for (skips are not positions)."""
    return [
        {"meeting": meeting, "seat": voter, "target": target}
        for meeting, by_voter in sorted(votes.items())
        for voter, (target, forfeited) in sorted(by_voter.items())
        if target is not None and not forfeited
    ]


def speed_seats(
    planned: dict[str, Any], records: list[dict[str, Any]], transcript: Path
) -> list[SpeedSeat]:
    """Measure every Seat of one complete speed-experiment game."""
    header = records[0]
    config = header["config"]
    tiers, budgets = config.get("talk_tiers"), config.get("talk_budgets")
    if not tiers or not budgets:
        return []
    mode = (planned.get("talk") or {}).get("mode", "random")
    roles = {s["seat"]: s["role"] for s in header["seats"]}
    rows = {
        s["seat"]: SpeedSeat(
            game=planned["id"],
            kind=planned["kind"],
            mode=mode,
            seat=s["seat"],
            model=seat_model(s["controller"]),
            role=s["role"],
            tier=tiers[s["seat"]],
            budget=int(budgets[s["seat"]]),
        )
        for s in header["seats"]
    }
    for r in records:
        if r.get("kind") == "event" and r.get("type") == "statement":
            d = r["data"]
            row = rows[d["speaker"]]
            row.truncated += bool(d.get("truncated"))
            if d["text"].strip():
                row.messages += 1
                row.chars += len(d["text"])
    messages = sum(r.messages for r in rows.values())
    chars = sum(r.chars for r in rows.values())
    for row in rows.values():
        row.floor_share = row.messages / messages if messages else None
        row.char_share = row.chars / chars if chars else None

    votes = ballots(records)
    judged = judged_positions(transcript, records)
    positions = judged.positions
    source = "judged" if positions is not None else "proxy"
    if positions is None:
        positions = proxy_positions(votes)
    for p in positions:
        row = rows.get(p["seat"])
        if row is None:
            continue
        target = p["target"]
        want = None if target == SKIP else target
        wrong = type(target) is int and roles.get(target) == "crewmate"
        row.positions += 1
        row.pushed_crewmate += wrong
        for voter, (ballot, forfeited) in votes.get(p["meeting"], {}).items():
            if voter == p["seat"] or forfeited or voter == target:
                continue
            row.pull_ballots += 1
            hit = ballot == want
            row.pull_matches += hit
            row.wrong_matches += hit and wrong
    for row in rows.values():
        row.pull_source = source
        row.pull_judge = judged.judge
        row.judgments_ignored = judged.ignored
        if row.pull_ballots:
            row.vote_pull = row.pull_matches / row.pull_ballots
        row.high_pull_wrong = (
            row.vote_pull is not None and row.vote_pull >= HIGH_PULL and row.pushed_crewmate > 0
        )
    return [rows[s] for s in sorted(rows)]


def load_speed(run_dir: Path, game_ids: set[str] | None = None) -> list[SpeedSeat]:
    """Every Seat of every complete speed game (optionally only ``game_ids``)."""
    manifest = json.loads((run_dir / "manifest.json").read_text("utf-8"))
    out = []
    for planned in manifest["games"]:
        if not planned.get("talk") or (game_ids is not None and planned["id"] not in game_ids):
            continue
        path = run_dir / GAMES / f"{planned['id']}.jsonl"
        records = read_records(path) if path.is_file() else None
        if records is None or not is_complete(records):
            continue
        out += speed_seats(planned, records, path)
    return out


# Estimates by tier -----------------------------------------------------------------------


@dataclass(frozen=True)
class TierMetric:
    key: str
    label: str
    unit: str
    definition: str
    num: Any  # SpeedSeat -> float | None
    den: Any  # SpeedSeat -> float | None
    pull: bool = False  # depends on Vote Pull positions (labelled by source)


TIER_METRICS: tuple[TierMetric, ...] = (
    TierMetric(
        "floor_share",
        "Floor Share",
        "share of a game's spoken Meeting statements (mean per Seat)",
        "Spoken statements by the Seat / all spoken statements in the game, averaged over "
        "the tier's Seats; games where nobody spoke are left out (0/0 is undefined).",
        lambda s: s.floor_share,
        lambda s: None if s.floor_share is None else 1.0,
    ),
    TierMetric(
        "char_share",
        "Share of Meeting characters",
        "share of a game's spoken characters (mean per Seat)",
        "Characters the Seat spoke in Meetings / all characters spoken in the game, averaged "
        "over the tier's Seats (games where nobody spoke left out). This is what a Talk "
        "Budget caps directly.",
        lambda s: s.char_share,
        lambda s: None if s.char_share is None else 1.0,
    ),
    TierMetric(
        "vote_pull",
        "Vote Pull",
        "share of other players' ballots matching the Seat's position",
        "Matching ballots / ballots by other players over the Meetings where the Seat took a "
        "position, pooled over the tier's Seats (forfeited ballots and the target's own "
        "ballot left out).",
        lambda s: s.pull_matches,
        lambda s: s.pull_ballots,
        pull=True,
    ),
    TierMetric(
        "wrong_pull",
        "Vote Pull toward Crewmates",
        "share of other players' ballots matching a position on a Crewmate",
        "Ballots matching a position that targeted a Crewmate / all ballots counted for Vote "
        "Pull, pooled over the tier's Seats: the part of Vote Pull that was wrong.",
        lambda s: s.wrong_matches,
        lambda s: s.pull_ballots,
        pull=True,
    ),
    TierMetric(
        "high_pull_wrong",
        "High Vote Pull and wrong",
        f"share of Seats with a position (Vote Pull >= {HIGH_PULL} and a Crewmate pushed)",
        f"Share of the tier's Seats with at least one position whose Vote Pull in the game was "
        f"at least {HIGH_PULL} and who pushed at least one Crewmate.",
        lambda s: float(s.high_pull_wrong),
        lambda s: 1.0 if s.positions else 0.0,
        pull=True,
    ),
)


def _matrix(
    games: list[str], seats: list[SpeedSeat], metric: TierMetric
) -> tuple[np.ndarray, np.ndarray]:
    index = {g: i for i, g in enumerate(games)}
    num = np.zeros((len(games), len(TIERS)))
    den = np.zeros((len(games), len(TIERS)))
    for s in seats:
        a, b = metric.num(s), metric.den(s)
        if a is None or b is None:
            continue
        j = TIERS.index(s.tier)
        num[index[s.game], j] += a
        den[index[s.game], j] += b
    return num, den


def _first_per_game(seats: list[SpeedSeat]) -> list[SpeedSeat]:
    seen: dict[str, SpeedSeat] = {}
    for s in seats:
        seen.setdefault(s.game, s)
    return list(seen.values())


def tier_section(
    seats: list[SpeedSeat], mode: str, replicates: int, seed: int
) -> dict[str, Any] | None:
    """Every tier estimate for one mode (tier games or the realism check)."""
    seats = [s for s in seats if s.mode == mode and s.tier in TIERS]
    if not seats:
        return None
    kinds: dict[str, str] = {}
    for s in seats:
        kinds[s.game] = s.kind
    games = sorted(kinds)
    weights = resample_weights([kinds[g] for g in games], replicates, seed)
    by_game_source = {s.game: s.pull_source for s in seats}
    judged = sorted(g for g, src in by_game_source.items() if src == "judged")
    source = "judged" if len(judged) == len(games) else "proxy" if not judged else "mixed"
    metrics: dict[str, Any] = {}
    for metric in TIER_METRICS:
        num, den = _matrix(games, seats, metric)
        ests = ratio_estimates(num, den, weights)
        diff = ratio_difference(num, den, weights, TIERS.index("fast"), TIERS.index("slow"))
        label = metric.label
        if metric.pull and source != "judged":
            label += " (proxy: own votes)" if source == "proxy" else " (judged + proxy)"
        metrics[metric.key] = {
            "label": label,
            "unit": metric.unit,
            "definition": metric.definition,
            "per_tier": {t: e.to_dict() for t, e in zip(TIERS, ests, strict=True)},
            "fast_minus_slow": diff.to_dict(),
        }
    highlights = [
        {
            k: v
            for k, v in asdict(s).items()
            if k in ("game", "seat", "role", "tier", "budget", "vote_pull", "pushed_crewmate")
        }
        for s in seats
        if s.high_pull_wrong
    ]
    return {
        "label": MODES[mode],
        "games": len(games),
        "seats": len(seats),
        # Games with no spoken Meeting statement: Floor Share and character share are
        # undefined there and those games are left out of both.
        "games_without_speech": len({s.game for s in seats if s.floor_share is None}),
        "strata": sorted(set(kinds.values())),
        "pull_source": source,
        "judged_games": len(judged),
        # Who judged the judged games, and how many games' judgments were left out
        # (non-scoring or incomplete) in favour of the labelled proxy.
        "judges": sorted({s.pull_judge for s in seats if s.pull_judge}),
        "judgments_ignored": dict(
            sorted(
                Counter(
                    g.judgments_ignored for g in _first_per_game(seats) if g.judgments_ignored
                ).items()
            )
        ),
        "metrics": metrics,
        "high_pull_wrong": sorted(highlights, key=lambda h: (h["game"], h["seat"])),
    }


def speed_report(
    run_dir: Path, game_ids: set[str], replicates: int, seed: int
) -> dict[str, Any] | None:
    """The ``speed`` section of ``metrics.json``, or None for a run without tiers."""
    seats = load_speed(Path(run_dir), game_ids)
    if not seats:
        return None
    manifest = json.loads((Path(run_dir) / "manifest.json").read_text("utf-8"))
    plan = manifest.get("speed") or {}
    sections = {
        mode: section
        for mode in MODES
        if (section := tier_section(seats, mode, replicates, seed)) is not None
    }
    return {
        "tiers": list(TIERS),
        "budgets": plan.get("tiers"),
        "high_pull": HIGH_PULL,
        "realism_throughput": {
            k: v for k, v in (plan.get("realism") or {}).items() if k != "games"
        },
        "pull_note": (
            "Vote Pull positions are #15's judgments (who a Seat pushed to eject) when "
            "<transcript>.audit.json exists for the transcript and holds complete "
            "judgments from a scoring judge; otherwise a proxy: the player the Seat "
            "itself voted for."
        ),
        "modes": sections,
        "seats": [asdict(s) for s in seats],
    }
