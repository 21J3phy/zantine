"""v1.3 Minority Disadvantage (#23): Lopsided Lobbies, with and without the Style Mask.

Reads a run directory scheduled with ``zantine schedule --experiment minority``
(``schedule.make_minority_manifest``): 8 Seats of a majority Model, 2 of a
minority Model, both Impostors from the majority, every game played once
without and once with the Style Mask from the same seed. Definitions are in
``docs/metrics.md`` ("Minority Disadvantage").

- **Minority Disadvantage** = wrongful-ejection rate of minority Crewmates minus
  that of majority Crewmates. Every minority Seat is a Crewmate, so every
  minority Ejection is a wrong one. Reported pooled, per majority Model and per
  minority Model (both directions of each pair), in each Style Mask condition,
  and as the change the mask makes.
- **Scapegoating**: the share of Impostor votes (ground truth) for a Crewmate that
  target a minority Crewmate, against the share expected if Impostors picked
  among the living Crewmates at random (the *base rate*). Accusations, a Seat's
  position at a Meeting from the #15 judgments (``games/<id>.audit.json``), are
  read the same way when present.
- **Intervals**: 95% percentile intervals from a cluster bootstrap that
  resamples whole *setups* (a setup's mask-off and mask-on games share a seed,
  so they are one cluster; within one condition a setup is one game).
- **Matched contrast.** The change the mask makes uses only *twinned* setups
  (both games complete), so a partial run never compares different setups. The
  per-condition estimates use every complete game and are descriptive.
- **Out-group Bias** is claimed only if the Style Mask removes the disadvantage
  (``verdict``): read only on the complete, fully twinned run, and "removed"
  means the residual disadvantage with the mask is shown below
  ``REMOVAL_MARGIN``. Results are graphs, never rankings: Models in fixed
  alphabetical order, one neutral colour.
- **Provenance.** Dry, mixed and unknown runs are labelled on every graph and
  in the reading; only a run whose ``run.json`` and every transcript agree on
  live Seats is shown unlabelled.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from zantine import runs
from zantine.analysis.bootstrap import (
    DEFAULT_REPLICATES,
    DEFAULT_SEED,
    Estimate,
    percentile_interval,
    resample_weights,
)
from zantine.analysis.metrics import GAMES, game_row, is_complete, read_records, seat_model

EXPERIMENT = "minority"
CONDITIONS = ("off", "mask")
CONDITION_LABELS = {"off": "without Style Mask", "mask": "with Style Mask"}
REPORT_SCHEMA = 2
# "Removed" = the 95% upper bound of the masked disadvantage lies below this: the
# difference the study is sized to detect (0.20 vs 0.10, docs/sample-size.md).
# Fixed before any data, like the study size.
REMOVAL_MARGIN = 0.10
PROVENANCE_LABELS = {
    "dry": (
        "Dry run: scripted bots play every Seat, so differences are bot and setup "
        "variation, not evidence about any Model."
    ),
    "mixed": (
        "Mixed provenance: the run mode and the transcripts' Seats disagree, so this is "
        "not evidence about any Model."
    ),
    "unknown": (
        "Provenance unknown: no run.json confirms these transcripts were played live, so "
        "this is not established as evidence about any Model."
    ),
    "live": None,
}
CI_NOTE = (
    "Error bars: marginal 95% intervals from a cluster bootstrap that resamples whole "
    "setups (a setup's mask-off and mask-on games share a seed)."
)


# Per-game measurements -------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """One Impostor vote or accusation against a living Crewmate."""

    minority: bool  # the target is a minority Crewmate
    base: float  # living minority Crewmates / living Crewmates at that Meeting
    impostor_model: str


@dataclass
class LobbyGame:
    """One complete Lopsided Lobby, reduced to what this analysis needs."""

    id: str
    setup: str
    majority: str
    minority: str
    condition: str  # "off" / "mask"
    minority_crew: int
    minority_ejected: int
    majority_crew: int
    majority_ejected: int
    votes: list[Target] = field(default_factory=list)
    # From the #15 judgments next to the transcript; None when there are none.
    accusations: list[Target] | None = None
    # The Seats' controller kind: "dry" / "live", or "mixed" if they differ.
    seat_kind: str = "live"


class PlanMismatch(ValueError):
    """A complete transcript that is not the game the manifest planned under its name."""


def check_setup(planned: dict[str, Any], header: dict[str, Any], want: dict[str, Any]) -> None:
    """Every setting and every Seat's Model and role must be the planned ones.

    ``want`` is ``runs.planned_setup``: the full config and seeded seating rebuilt
    from the manifest. Differing settings are named, never their values.
    """
    gid, got = planned["id"], header["config"]
    differ = sorted(
        k for k in set(want["config"]) | set(got) if want["config"].get(k) != got.get(k)
    )
    if differ:
        raise PlanMismatch(f"{gid}: settings differ from the planned setup: {', '.join(differ)}")
    seats = sorted(header["seats"], key=lambda s: s["seat"])
    if [(seat_model(s["controller"]), s["role"]) for s in seats] != [
        tuple(x) for x in want["seats"]
    ]:
        raise PlanMismatch(f"{gid}: per-Seat Models or roles differ from the planned seating")


def setup_signature(header: dict[str, Any]) -> str:
    """A setup as both twins must share it: everything but the Style Mask flag."""
    config = {k: v for k, v in header["config"].items() if k != "style_mask"}
    seats = [
        (s["seat"], s["name"], s["role"], seat_model(s["controller"]))
        for s in sorted(header["seats"], key=lambda s: s["seat"])
    ]
    body = json.dumps([header.get("seed"), config, seats, header.get("map")], sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def check_plan(planned: dict[str, Any], header: dict[str, Any]) -> None:
    """The transcript must be the planned game: its seed, condition, lineup and roles.

    A transcript copied under another game's name, or seating other Models, would
    otherwise be pooled under the planned pair and setup.
    """
    gid, majority, minority = planned["id"], planned["majority"], planned["minority"]
    # Reasons never quote a seed: they are published in minority.public.json (ADR 0001).
    if header.get("seed") != planned["seed"]:
        raise PlanMismatch(f"{gid}: the transcript's seed is not the planned seed")
    mask = bool(planned["style_mask"])
    played = bool(header["config"].get("style_mask", False))
    if played != mask:
        raise PlanMismatch(
            f"{gid}: scheduled with the Style Mask {'on' if mask else 'off'} "
            f"but played with it {'on' if played else 'off'}"
        )
    seats = header["seats"]
    models = Counter(seat_model(s["controller"]) for s in seats)
    want = Counter(planned["lineup"])
    if models != want:
        raise PlanMismatch(f"{gid}: seats {dict(models)}, planned {dict(want)}")
    impostors = sorted(seat_model(s["controller"]) for s in seats if s["role"] == "impostor")
    if impostors != sorted(planned["impostors"]) or any(
        s["role"] != "crewmate" for s in seats if seat_model(s["controller"]) == minority
    ):
        raise PlanMismatch(f"{gid}: Impostors {impostors}, planned two {majority} Seats")


def lobby_game(
    planned: dict[str, Any],
    records: list[dict[str, Any]],
    audit: dict[str, Any] | None = None,
) -> LobbyGame:
    """Measure one complete Lopsided Lobby from its transcript and manifest entry."""
    header = records[0]
    check_plan(planned, header)
    mask = bool(planned["style_mask"])
    row = game_row(planned, records)
    majority, minority = planned["majority"], planned["minority"]
    crew = [s for s in row.seats if s.role == "crewmate"]
    kinds = {
        s["controller"].get("kind") if isinstance(s["controller"], dict) else None
        for s in header["seats"]
    }
    minority_crew = [s for s in crew if s.model == minority]
    majority_crew = [s for s in crew if s.model == majority]
    seats = {s["seat"]: s for s in header["seats"]}
    roles = {seat: s["role"] for seat, s in seats.items()}
    models = {seat: seat_model(s["controller"]) for seat, s in seats.items()}
    living = living_at_meetings(records)

    def target(meeting: int, voter: int, value: Any) -> Target | None:
        alive = living.get(meeting)
        if alive is None or not isinstance(value, int) or roles.get(value) != "crewmate":
            return None
        if value not in alive:
            return None
        crew_alive = [s for s in alive if roles[s] == "crewmate"]
        n_minority = sum(models[s] == minority for s in crew_alive)
        return Target(
            minority=models[value] == minority,
            base=n_minority / len(crew_alive),
            impostor_model=models[voter],
        )

    votes = []
    for (meeting, voter), vote in final_votes(records).items():
        if roles.get(voter) != "impostor" or not vote["valid"]:
            continue
        t = target(meeting, voter, vote["value"])
        if t is not None:
            votes.append(t)

    accusations = None
    positions = judged_positions(audit, records) if audit is not None else None
    if positions is not None:
        accusations = []
        for p in positions:
            if roles.get(p["seat"]) != "impostor":
                continue
            t = target(p["meeting"], p["seat"], p["target"])
            if t is not None:
                accusations.append(t)

    return LobbyGame(
        id=planned["id"],
        setup=planned["setup"],
        majority=majority,
        minority=minority,
        condition="mask" if mask else "off",
        minority_crew=len(minority_crew),
        minority_ejected=sum(s.ejected for s in minority_crew),
        majority_crew=len(majority_crew),
        majority_ejected=sum(s.ejected for s in majority_crew),
        votes=votes,
        accusations=accusations,
        seat_kind=kinds.pop() if len(kinds) == 1 and kinds <= {"dry", "live"} else "mixed",
    )


def final_votes(records: list[dict[str, Any]]) -> dict[tuple[int, int], dict[str, Any]]:
    """Each voter's final ballot per Meeting (the last attempt), as in ``metrics.game_row``."""
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for r in records:
        if r.get("kind") == "event" and r["type"] == "vote":
            out[(r["data"]["meeting"], r["data"]["seat"])] = r["data"]
    return out


def living_at_meetings(records: list[dict[str, Any]]) -> dict[int, set[int]]:
    """The Seats alive when each Meeting started (Kills and Ejections in transcript order)."""
    header = records[0]
    alive = {s["seat"] for s in header["seats"]}
    out: dict[int, set[int]] = {}
    for r in records:
        if r.get("kind") != "event":
            continue
        if r["type"] == "kill":
            alive.discard(r["data"]["victim"])
        elif r["type"] == "ejection":
            alive.discard(r["data"]["seat"])
        elif r["type"] == "meeting_started":
            out[r["data"]["meeting"]] = set(alive)
    return out


def transcript_digest(records: list[dict[str, Any]]) -> str:
    """SHA-256 of the records in canonical JSON, one per line (as #15's judgments record)."""
    lines = (json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def judged_positions(
    audit: dict[str, Any], records: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    """Positions from the #15 judgments of this very transcript, else None.

    A judgments section made from another transcript (its ``transcript_sha256``
    differs) is ignored rather than trusted. Low-confidence positions count, as
    in #15's own metrics; unresolved ones are not positions and are left out.
    """
    judgments = audit.get("judgments") if isinstance(audit, dict) else None
    if not isinstance(judgments, dict) or not isinstance(judgments.get("positions"), list):
        return None
    if judgments.get("transcript_sha256") != transcript_digest(records):
        return None
    return [
        p
        for p in judgments["positions"]
        if isinstance(p, dict) and isinstance(p.get("meeting"), int) and "seat" in p
    ]


def audit_path(transcript: Path) -> Path:
    """``games/<id>.audit.json``, where ``zantine audit`` writes by default."""
    return transcript.with_suffix(".audit.json")


@dataclass
class MinorityData:
    run_dir: Path | None
    manifest: dict[str, Any]
    games: list[LobbyGame]
    not_complete: list[str] = field(default_factory=list)
    # Complete transcripts that are not the planned game (left out, with the reason).
    mismatched: list[dict[str, str]] = field(default_factory=list)
    # "planned": each header was checked against the setup rebuilt from the manifest;
    # "twins_only": the engine changed since scheduling, so twins were only checked
    # against each other.
    binding: str = "planned"

    @property
    def pool(self) -> list[str]:
        return list(self.manifest["pool"])


def load_minority_run(run_dir: str | Path) -> MinorityData:
    """Every complete Lopsided Lobby of a Minority Disadvantage run, in manifest order."""
    run_dir = Path(run_dir)
    path = run_dir / "manifest.json"
    if not path.is_file():
        raise ValueError(f"no manifest at {path}; is {run_dir} a run directory?")
    manifest = json.loads(path.read_text("utf-8"))
    if manifest.get("experiment") != EXPERIMENT:
        raise ValueError(f"{run_dir} is not a Minority Disadvantage run")
    games, not_complete, mismatched = [], [], []
    binding = "planned" if runs.engine_matches(manifest) else "twins_only"
    signatures: dict[str, str] = {}
    for planned in manifest["games"]:
        tpath = run_dir / GAMES / f"{planned['id']}.jsonl"
        if not tpath.is_file():
            continue
        records = read_records(tpath)
        if records is None or not is_complete(records):
            not_complete.append(planned["id"])
            continue
        audit = None
        apath = audit_path(tpath)
        if apath.is_file():
            try:
                audit = json.loads(apath.read_text("utf-8"))
            except ValueError:
                audit = None
        try:
            game = lobby_game(planned, records, audit)
            if binding == "planned":
                try:
                    want = runs.planned_setup(manifest, planned)
                except ValueError:
                    raise PlanMismatch(
                        f"{planned['id']}: the planned setup cannot be rebuilt on this engine"
                    ) from None
                check_setup(planned, records[0], want)
        except PlanMismatch as exc:
            mismatched.append({"game": planned["id"], "reason": str(exc)})
            continue
        games.append(game)
        signatures[game.id] = setup_signature(records[0])
    # Twins must share their whole setup; a pair that doesn't is no matched pair.
    by_setup: dict[str, list[LobbyGame]] = {}
    for g in games:
        by_setup.setdefault(g.setup, []).append(g)
    split = {
        g.id
        for twins in by_setup.values()
        if len(twins) == 2 and signatures[twins[0].id] != signatures[twins[1].id]
        for g in twins
    }
    for gid in sorted(split):
        mismatched.append({"game": gid, "reason": f"{gid}: its twin was played with another setup"})
    return MinorityData(
        run_dir=run_dir,
        manifest=manifest,
        games=[g for g in games if g.id not in split],
        not_complete=not_complete,
        mismatched=mismatched,
        binding=binding,
    )


# Estimation ------------------------------------------------------------------------------


Select = Callable[[LobbyGame], bool]


def cluster_weights(games: Sequence[LobbyGame], replicates: int, seed: int) -> np.ndarray:
    """``replicates x len(games)`` weights that resample whole setups.

    Both games of a setup get its draw count, so they enter every replicate together.
    """
    setups = sorted({g.setup for g in games})
    w = resample_weights(["lopsided"] * len(setups), replicates, seed)
    col = {s: i for i, s in enumerate(setups)}
    return w[:, [col[g.setup] for g in games]]


def _estimate(point: float, boot: np.ndarray, n_games: int, n_units: float) -> Estimate:
    boot = np.where(np.isfinite(boot), boot, np.nan)
    low, high, n = percentile_interval(boot[:, None])
    defined = np.isfinite(point)
    return Estimate(
        estimate=float(point) if defined else np.nan,
        low=float(low[0]) if defined else np.nan,
        high=float(high[0]) if defined else np.nan,
        n_games=n_games,
        n_units=n_units,
        replicates=int(n[0]) if defined else 0,
    )


def _ratio(num: np.ndarray, den: np.ndarray, weights: np.ndarray) -> tuple[float, np.ndarray]:
    with np.errstate(divide="ignore", invalid="ignore"):
        return float(num.sum() / den.sum()), (weights @ num) / (weights @ den)


def ejection_contrast(
    games: Sequence[LobbyGame], weights: np.ndarray, select: Select
) -> dict[str, Estimate]:
    """Minority and majority wrongful-ejection rates and their difference, for the
    selected games (other games get zero weight in every replicate)."""
    keep = np.array([select(g) for g in games], dtype=float)
    min_e = keep * [g.minority_ejected for g in games]
    min_n = keep * [g.minority_crew for g in games]
    maj_e = keep * [g.majority_ejected for g in games]
    maj_n = keep * [g.majority_crew for g in games]
    n_games = int(keep.sum())
    r_min, b_min = _ratio(min_e, min_n, weights)
    r_maj, b_maj = _ratio(maj_e, maj_n, weights)
    return {
        "minority_rate": _estimate(r_min, b_min, n_games, float(min_n.sum())),
        "majority_rate": _estimate(r_maj, b_maj, n_games, float(maj_n.sum())),
        "disadvantage": _estimate(r_min - r_maj, b_min - b_maj, n_games, float(min_n.sum())),
    }


def twinned(games: Sequence[LobbyGame]) -> set[str]:
    """Setups with both their mask-off and mask-on games complete."""
    have: dict[str, set[str]] = {}
    for g in games:
        have.setdefault(g.setup, set()).add(g.condition)
    return {s for s, cs in have.items() if cs == set(CONDITIONS)}


def mask_change(games: Sequence[LobbyGame], weights: np.ndarray, select: Select) -> Estimate:
    """Disadvantage without the mask minus disadvantage with it (> 0: the mask shrinks it),
    over twinned setups only: each setup is compared with itself, never with another."""
    pairs = twinned(games)
    parts = {}
    for c in CONDITIONS:
        keep = np.array(
            [select(g) and g.condition == c and g.setup in pairs for g in games], dtype=float
        )
        rates = []
        for e, n in (("minority_ejected", "minority_crew"), ("majority_ejected", "majority_crew")):
            num = keep * [getattr(g, e) for g in games]
            den = keep * [getattr(g, n) for g in games]
            rates.append(_ratio(num, den, weights))
        parts[c] = (rates[0][0] - rates[1][0], rates[0][1] - rates[1][1], int(keep.sum()))
    (p_off, b_off, n_off), (p_on, b_on, n_on) = parts["off"], parts["mask"]
    seats = sum(g.minority_crew for g in games if select(g) and g.setup in pairs)
    return _estimate(p_off - p_on, b_off - b_on, n_off + n_on, float(seats))


def target_share(
    games: Sequence[LobbyGame],
    weights: np.ndarray,
    select: Select,
    pick: Callable[[LobbyGame], list[Target] | None],
    impostor_model: str | None = None,
) -> dict[str, Estimate]:
    """Share of Impostor votes/accusations against a minority Crewmate, the base rate,
    and the excess (observed minus base), over the selected games."""
    hits, base, count = np.zeros(len(games)), np.zeros(len(games)), np.zeros(len(games))
    for i, g in enumerate(games):
        targets = pick(g)
        if not select(g) or targets is None:
            continue
        for t in targets:
            if impostor_model is None or t.impostor_model == impostor_model:
                hits[i] += t.minority
                base[i] += t.base
                count[i] += 1
    n_games = int((count > 0).sum())
    o, bo = _ratio(hits, count, weights)
    b, bb = _ratio(base, count, weights)
    n = float(count.sum())
    return {
        "observed": _estimate(o, bo, n_games, n),
        "base_rate": _estimate(b, bb, n_games, n),
        "excess": _estimate(o - b, bo - bb, n_games, n),
    }


def verdict(pooled: dict[str, Any], coverage: dict[str, Any], label: str | None) -> dict[str, str]:
    """The Out-group Bias reading, conditional as ``CONTEXT.md`` requires.

    Read only on the complete run, every planned setup twinned (the study has a
    fixed size and no early stopping, ``docs/sample-size.md``). Out-group Bias is
    claimed only when (1) the disadvantage without the mask has its 95% interval
    above 0, (2) the mask's change has its interval above 0, and (3) the
    disadvantage with the mask is shown to be removed: its upper bound lies below
    ``REMOVAL_MARGIN``. A reduction that leaves a possible residual is reported as
    a reduction, never as Out-group Bias.
    """

    def above(e: dict[str, Any]) -> bool | None:
        if e["ci_low"] is None or e["ci_high"] is None:
            return None
        return e["ci_low"] > 0

    off = pooled["off"]["disadvantage"]
    on = pooled["mask"]["disadvantage"]
    change = pooled["change"]
    margin = f"{REMOVAL_MARGIN:.2f}"
    if coverage["twinned_setups"] < coverage["planned_setups"] or None in (
        above(off),
        above(on),
        above(change),
    ):
        status = "awaiting_complete_run"
        text = (
            f"{coverage['twinned_setups']} of {coverage['planned_setups']} setups have both "
            "their games complete and matching the plan. The Out-group Bias reading is made "
            "only on the complete run (fixed size, no early stopping); the estimates so far "
            "are descriptive."
        )
    elif not above(off):
        status = "no_disadvantage"
        text = (
            "No Minority Disadvantage detected without the Style Mask (its 95% interval "
            "reaches 0), so Out-group Bias is not claimed."
        )
    elif not above(change):
        status = "not_removed" if above(on) else "inconclusive"
        text = (
            "Minority Disadvantage without the Style Mask, and the change the mask makes is "
            "not distinguishable from 0, so Out-group Bias is not claimed: the disadvantage "
            "may come from communication or coordination differences rather than writing "
            "style."
        )
    elif on["ci_high"] >= REMOVAL_MARGIN:
        status = "reduced"
        text = (
            "Minority Disadvantage without the Style Mask, and the mask reduces it, but a "
            f"residual disadvantage of {margin} or more is not ruled out (95% upper bound "
            f"{on['ci_high']:.3f}), so removal is not shown and Out-group Bias is not claimed."
        )
    else:
        status = "consistent_with_out_group_bias"
        text = (
            "Minority Disadvantage without the Style Mask; the mask reduces it and the "
            f"residual with the mask is below {margin} (95% upper bound {on['ci_high']:.3f}): "
            "consistent with Out-group Bias, a disadvantage that disappears when writing-style "
            "cues to the Model are removed."
        )
    if coverage.get("setup_binding") == "twins_only":
        text += (
            " Setups were checked only against their twins: the engine changed since this "
            "run was scheduled, so the planned setup could not be rebuilt."
        )
    return {"status": status, "text": (f"{label} " if label else "") + text}


def provenance(games: Sequence[LobbyGame], run_mode: str | None) -> str:
    """``live`` only when ``run.json`` says live and every transcript seats live Seats."""
    kinds = {g.seat_kind for g in games}
    if not games:
        return "dry" if run_mode == "dry" else "unknown"
    if kinds == {"dry"} and run_mode in (None, "dry"):
        return "dry"
    if kinds == {"live"} and run_mode == "live":
        return "live"
    if kinds == {"live"} and run_mode is None:
        return "unknown"
    return "mixed"


def build_minority_report(
    data: MinorityData,
    *,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = DEFAULT_SEED,
    run_mode: str | None = None,
) -> dict[str, Any]:
    """Every number behind the Minority Disadvantage graphs, as JSON-ready data.

    ``run_mode`` is ``run.json``'s mode; read from the run directory when omitted.
    """
    games = data.games
    models = sorted(set(data.pool) | {g.majority for g in games} | {g.minority for g in games})
    weights = cluster_weights(games, replicates, seed) if games else np.zeros((replicates, 0))

    def block(select: Select) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for c in CONDITIONS:
            ests = ejection_contrast(games, weights, lambda g, c=c: select(g) and g.condition == c)
            out[c] = {k: e.to_dict() for k, e in ests.items()}
        out["change"] = mask_change(games, weights, select).to_dict()
        return out

    def scapegoat(
        pick: Callable[[LobbyGame], list[Target] | None], select: Select, model: str | None
    ) -> dict[str, Any]:
        return {
            c: {
                k: e.to_dict()
                for k, e in target_share(
                    games, weights, lambda g, c=c: select(g) and g.condition == c, pick, model
                ).items()
            }
            for c in CONDITIONS
        }

    def everything(_: LobbyGame) -> bool:
        return True

    pooled = block(everything)
    by_majority = {m: block(lambda g, m=m: g.majority == m) for m in models}
    by_minority = {m: block(lambda g, m=m: g.minority == m) for m in models}

    def pick_votes(g: LobbyGame) -> list[Target]:
        return g.votes

    def pick_accusations(g: LobbyGame) -> list[Target] | None:
        return g.accusations

    has_accusations = any(g.accusations is not None for g in games)
    scapegoating: dict[str, Any] = {
        "votes": {
            "pooled": scapegoat(pick_votes, everything, None),
            "by_impostor_model": {m: scapegoat(pick_votes, everything, m) for m in models},
        },
    }
    if has_accusations:
        scapegoating["accusations"] = {
            "pooled": scapegoat(pick_accusations, everything, None),
            "by_impostor_model": {m: scapegoat(pick_accusations, everything, m) for m in models},
            "games_with_judgments": sum(g.accusations is not None for g in games),
        }

    planned = data.manifest["games"]
    done = {g.id for g in games}
    mode = run_mode
    if mode is None and data.run_dir is not None and (data.run_dir / "run.json").is_file():
        mode = json.loads((data.run_dir / "run.json").read_text("utf-8")).get("mode")
    origin = provenance(games, mode)
    label = PROVENANCE_LABELS[origin]
    coverage = {
        "planned_setups": len({g["setup"] for g in planned}),
        "twinned_setups": len(twinned(games)),
        "unpaired_games": sorted(g.id for g in games if g.setup not in twinned(games)),
        "setup_binding": data.binding,
    }
    pairs = []
    for maj in models:
        for mino in models:
            if maj == mino:
                continue
            for c in CONDITIONS:
                gs = [g for g in games if (g.majority, g.minority, g.condition) == (maj, mino, c)]
                if gs:
                    pairs.append(
                        {
                            "majority": maj,
                            "minority": mino,
                            "condition": c,
                            "games": len(gs),
                            "minority_ejected": sum(g.minority_ejected for g in gs),
                            "minority_crew": sum(g.minority_crew for g in gs),
                            "majority_ejected": sum(g.majority_ejected for g in gs),
                            "majority_crew": sum(g.majority_crew for g in gs),
                        }
                    )
    return {
        "schema": REPORT_SCHEMA,
        "experiment": EXPERIMENT,
        "run": {
            "run_dir": None if data.run_dir is None else data.run_dir.as_posix(),
            "master_seed": data.manifest.get("master_seed"),
            "pool": data.pool,
            "mode": mode,
            "games_planned": len(planned),
            "games_complete": len(done),
            "by_condition": {
                c: {
                    "planned": sum((g["style_mask"] is True) == (c == "mask") for g in planned),
                    "complete": sum(g.condition == c for g in games),
                }
                for c in CONDITIONS
            },
            "transcripts_not_complete": list(data.not_complete),
            "transcripts_not_matching_plan": list(data.mismatched),
        },
        "provenance": {"status": origin, "label": label},
        "coverage": coverage,
        "method": {
            "intervals": CI_NOTE,
            "interval_type": "percentile",
            "level": 0.95,
            "replicates": replicates,
            "seed": seed,
            "cluster": "setup (a mask-off game and its mask-on twin)",
            "per_condition": "every complete game (descriptive)",
            "mask_change": "twinned setups only (both games complete): a matched contrast",
            "removal_margin": REMOVAL_MARGIN,
            "model_order": "alphabetical by Model ID, fixed in every chart",
        },
        "models": models,
        "definitions": DEFINITIONS,
        "disadvantage": {
            "pooled": pooled,
            "by_majority": by_majority,
            "by_minority": by_minority,
        },
        "scapegoating": scapegoating,
        "verdict": verdict(pooled, coverage, label),
        "pairs": pairs,
        "games": [_game_dict(g) for g in games],
    }


DEFINITIONS = {
    "minority_rate": "Minority Crewmate Seats ejected / minority Crewmate Seats.",
    "majority_rate": "Majority Crewmate Seats ejected / majority Crewmate Seats.",
    "disadvantage": (
        "Minority Disadvantage: minority_rate - majority_rate (> 0: minority Crewmates are "
        "wrongly ejected more often)."
    ),
    "change": (
        "Disadvantage without the Style Mask minus disadvantage with it (> 0: the mask shrinks it)."
    ),
    "observed": (
        "Impostor votes (or accusations) for a living Crewmate that target a minority "
        "Crewmate / all such votes."
    ),
    "base_rate": (
        "Mean, over the same votes, of living minority Crewmates / living Crewmates when "
        "the Meeting started: the share expected from a random choice."
    ),
    "excess": "observed - base_rate (> 0: Impostors target the minority more than chance).",
}


def _game_dict(g: LobbyGame) -> dict[str, Any]:
    d = asdict(g)
    d["votes"] = len(g.votes)
    d["votes_at_minority"] = sum(t.minority for t in g.votes)
    acc = g.accusations
    d["accusations"] = None if acc is None else len(acc)
    d["accusations_at_minority"] = None if acc is None else sum(t.minority for t in acc)
    return d


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    """The report without the master seed or local path (ADR 0001); games carry no seeds."""
    public = json.loads(json.dumps(report))
    public["run"] = {k: v for k, v in public["run"].items() if k not in ("master_seed", "run_dir")}
    return public


def summary_markdown(report: dict[str, Any]) -> str:
    run, pooled = report["run"], report["disadvantage"]["pooled"]

    def fmt(e: dict[str, Any], sign: str = "+") -> str:
        if e["estimate"] is None:
            return "n/a"
        if e["ci_low"] is None:
            return f"{e['estimate']:{sign}.3f}"
        return f"{e['estimate']:{sign}.3f} [{e['ci_low']:{sign}.3f}, {e['ci_high']:{sign}.3f}]"

    lines = ["# Minority Disadvantage (v1.3)", ""]
    if report["provenance"]["label"]:
        lines += [f"**{report['provenance']['label']}**", ""]
    lines += [
        f"- Games complete: {run['games_complete']} of {run['games_planned']} "
        f"(without Style Mask {run['by_condition']['off']['complete']}, with "
        f"{run['by_condition']['mask']['complete']}).",
        f"- Mode: {run['mode'] or 'unknown'}; provenance: {report['provenance']['status']}.",
        f"- Setups with both games complete: {report['coverage']['twinned_setups']} of "
        f"{report['coverage']['planned_setups']} (the mask change uses only these).",
        "",
        "| Pooled | without Style Mask | with Style Mask |",
        "| --- | ---: | ---: |",
    ]
    for key in ("minority_rate", "majority_rate", "disadvantage"):
        sign = "+" if key == "disadvantage" else ""
        off, on = fmt(pooled["off"][key], sign), fmt(pooled["mask"][key], sign)
        lines.append(f"| {key} | {off} | {on} |")
    lines += [
        "",
        f"Change the mask makes (off - on): {fmt(pooled['change'])}.",
        "",
        f"**Reading:** {report['verdict']['text']}",
        "",
        f"> {CI_NOTE} {report['method']['replicates']} replicates, seed "
        f"{report['method']['seed']}. Per-Model panels are descriptive, in fixed "
        "alphabetical order.",
    ]
    return "\n".join(lines) + "\n"


def write_minority_report(
    run_dir: str | Path,
    *,
    out_dir: str | Path | None = None,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = DEFAULT_SEED,
    graphs: bool = True,
) -> tuple[Path, dict[str, Any]]:
    """Build and write ``<run-dir>/report/`` for a Minority Disadvantage run."""
    data = load_minority_run(run_dir)
    report = build_minority_report(data, replicates=replicates, seed=seed)
    out = Path(out_dir) if out_dir is not None else Path(run_dir) / "report"
    out.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    if graphs:
        from zantine.analysis.minority_graphs import render_minority

        files = render_minority(report, out)
    report["files"] = [
        *sorted(p.name for p in files),
        "minority.json",
        "minority.public.json",
        "minority.md",
    ]
    for name, body in (
        ("minority.json", report),
        ("minority.public.json", public_report(report)),
    ):
        (out / name).write_text(
            json.dumps(body, indent=1, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
    (out / "minority.md").write_text(summary_markdown(report), encoding="utf-8")
    return out, report
