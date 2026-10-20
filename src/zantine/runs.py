"""Run directories: play a scheduled manifest, resumably, under one spend cap.

Layout of ``runs/<run-id>/`` (other tools read it; see ``docs/architecture.md``)::

    manifest.json            the plan (schedule.make_manifest); written once, never changed
    index.jsonl              one line per finished attempt (complete or aborted), append-only
    spend.jsonl              one line per paid call, written as it is made, append-only
    games/<game-id>.jsonl    the transcript of each complete game
    aborted/<game-id>.<attempt>.jsonl   transcripts of attempts stopped by the spend cap
                                        or a fatal API error (kept for the spend record)

A game is done when ``games/<game-id>.jsonl`` parses, has a ``result`` record
and ends with a complete ``cost`` record. Transcripts are written to a temporary
file and renamed, so a crash leaves either a complete transcript or none. Resuming
plays every game that is not done, in manifest order, so no game is duplicated
or skipped. The spend cap covers the whole run: spending already in
``spend.jsonl`` (including aborted and crashed attempts) counts against it, so a
run stopped by the cap continues when invoked again with a higher cap.

``--dry-run`` plays every Seat with ``dry:<model>`` scripted bots at $0; ``--live``
plays ``live:<model>`` Seats through ``harness.live.play_live`` and needs the
usual opt-ins (the flag plus ``ZANTINE_LIVE=1``). A run directory holds one mode.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from zantine.config import GameConfig
from zantine.harness.ledger import CallRecord, Ledger
from zantine.harness.live import LiveRun, play_live, require_live
from zantine.harness.pricing import ModelSpec
from zantine.harness.provider import ProviderAdapter
from zantine.rng import GameRng
from zantine.scenario import build_scenario
from zantine.schedule import engine_versions, load_pool, make_manifest, seat_lineup, summary

MANIFEST = "manifest.json"
INDEX = "index.jsonl"
SPEND = "spend.jsonl"
GAMES = "games"
ABORTED = "aborted"
MODES = ("dry", "live")
# Dry runs make no paid calls; the smallest possible cap makes the Ledger refuse any.
DRY_RUN_CAP_USD = 1e-6


# Building one game ----------------------------------------------------------------


def game_setup(manifest: dict[str, Any], game: dict[str, Any], mode: str) -> tuple[GameConfig, str]:
    """The ``GameConfig`` and bot spec that play one scheduled game.

    The scenario (names, seat order, map) comes from the game seed as usual; the
    Impostor Seats are then fixed to wherever the seed seats the lineup entries of
    the scheduled Impostor Models (``schedule.seat_lineup``).
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    entries, impostor_positions = seat_lineup(game)
    config = GameConfig(seed=game["seed"], **{**manifest.get("config", {}), **game["config"]})
    if impostor_positions:
        scenario = build_scenario(config, GameRng(config.seed).fork("scenario"))
        seats = tuple(s for s, pos in enumerate(scenario.seat_order) if pos in impostor_positions)
        config = replace(config, impostor_seats=seats)
    return config, ",".join(f"{mode}:{model}" for model in entries)


def check_seating(game: dict[str, Any], header: dict[str, Any]) -> None:
    """The transcript header must seat exactly the scheduled Models and Impostors."""
    seats = header["seats"]
    entries, _ = seat_lineup(game)
    models = Counter(s["controller"]["model"] for s in seats)
    impostors = sorted(s["controller"]["model"] for s in seats if s["role"] == "impostor")
    if models != Counter(entries) or impostors != sorted(game["impostors"]):
        raise RuntimeError(f"{game['id']}: seating does not match the schedule")


# Run directory ----------------------------------------------------------------------


def create_run(run_dir: Path, manifest: dict[str, Any]) -> Path:
    path = run_dir / MANIFEST
    if path.exists():
        raise ValueError(f"{path} already exists; a run's manifest is never replaced")
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return path


def load_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / MANIFEST
    if not path.is_file():
        raise ValueError(f"no manifest at {path}; create one with `zantine schedule --out`")
    return json.loads(path.read_text("utf-8"))


def transcript_path(run_dir: Path, game_id: str) -> Path:
    return run_dir / GAMES / f"{game_id}.jsonl"


def is_complete(path: Path) -> bool:
    """A finished, fully written transcript: a ``result`` and a complete final ``cost``."""
    try:
        records = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]
    except (OSError, ValueError):
        return False
    return (
        bool(records)
        and records[0].get("kind") == "header"
        and any(r.get("kind") == "result" for r in records)
        and records[-1].get("kind") == "cost"
        and records[-1].get("complete") is True
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every parseable line (a crash can leave a torn last line, which is skipped)."""
    if not path.is_file():
        return []
    out = []
    for line in path.read_text("utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def spent_usd(run_dir: Path) -> float:
    """Everything the run has spent so far, from the per-call spend log."""
    return round(sum(r["cost_usd"] for r in read_jsonl(run_dir / SPEND)), 8)


class RunLedger(Ledger):
    """A Ledger that also appends every call to ``spend.jsonl`` as it is made.

    The log survives a crash mid-game, so the run-level cap stays honest.
    """

    def __init__(self, spend_cap_usd: float, path: Path) -> None:
        super().__init__(spend_cap_usd)
        self.path = path
        self.game: str | None = None
        self.attempt = 0
        self.spent_usd = spent_usd(path.parent)

    def _record(self, *args: Any, **kwargs: Any) -> None:
        super()._record(*args, **kwargs)
        record: CallRecord = self.records[-1]
        line = {"game": self.game, "attempt": self.attempt, **record.to_dict()}
        _append_line(self.path, line)


# Executing ----------------------------------------------------------------------------


@dataclass
class RunReport:
    planned: int
    complete: int  # complete games after this invocation
    played: list[str] = field(default_factory=list)  # games completed by this invocation
    stopped: str | None = None  # abort reason that stopped the run, if any
    stopped_game: str | None = None
    spent_usd: float = 0.0

    @property
    def finished(self) -> bool:
        return self.complete == self.planned


def execute(
    run_dir: Path,
    *,
    mode: str,
    spend_cap_usd: float | None = None,
    live: bool = False,
    max_games: int | None = None,
    adapter_factory: Callable[[ModelSpec], ProviderAdapter] | None = None,
    models: dict[str, ModelSpec] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> RunReport:
    """Play (or resume) every game of the run that is not done yet."""
    manifest = load_manifest(run_dir)
    _check_versions(manifest, mode)
    index = read_jsonl(run_dir / INDEX)
    modes = {r["mode"] for r in index}
    if modes - {mode}:
        raise ValueError(f"{run_dir} holds {', '.join(sorted(modes))} games; cannot add {mode}")
    if mode == "live":
        if spend_cap_usd is None:
            raise ValueError("live runs need an explicit --spend-cap (USD for the whole run)")
        if adapter_factory is None:
            require_live(live)
        cap = spend_cap_usd
    else:
        cap = DRY_RUN_CAP_USD
    ledger = RunLedger(cap, run_dir / SPEND)

    games = manifest["games"]
    done = {g["id"] for g in games if is_complete(transcript_path(run_dir, g["id"]))}
    _reconcile(run_dir, manifest, index, done, mode)
    report = RunReport(planned=len(games), complete=len(done))
    attempts = Counter(r["game"] for r in index)
    for r in read_jsonl(run_dir / SPEND):
        attempts[r["game"]] = max(attempts[r["game"]], r["attempt"])

    for game in games:
        if game["id"] in done:
            continue
        if max_games is not None and len(report.played) >= max_games:
            break
        if mode == "live" and ledger.spent_usd >= cap:
            report.stopped, report.stopped_game = "spend_cap", game["id"]
            break
        attempt = attempts[game["id"]] + 1
        ledger.game, ledger.attempt = game["id"], attempt
        config, spec = game_setup(manifest, game, mode)
        run = play_live(
            config,
            spec,
            spend_cap_usd=cap,
            live=live,
            adapter_factory=adapter_factory,
            models=models,
            ledger=ledger,
        )
        line = _finish(run_dir, game, attempt, mode, run)
        if progress is not None:
            progress(line)
        if run.aborted:
            report.stopped, report.stopped_game = run.aborted, game["id"]
            break
        report.played.append(game["id"])
        report.complete += 1
    report.spent_usd = round(ledger.spent_usd, 6)
    return report


def _finish(
    run_dir: Path, game: dict[str, Any], attempt: int, mode: str, run: LiveRun
) -> dict[str, Any]:
    records = run.game.transcript.records
    check_seating(game, records[0])
    if run.aborted:
        path = run_dir / ABORTED / f"{game['id']}.{attempt}.jsonl"
    else:
        path = transcript_path(run_dir, game["id"])
    _write_atomic(path, run.game.transcript.to_jsonl())
    result = next((r for r in records if r["kind"] == "result"), None)
    line = _index_line(game, attempt, mode, run.cost, result, path.relative_to(run_dir))
    line["abort_reason"] = run.aborted
    _append_line(run_dir / INDEX, line)
    return line


def _index_line(
    game: dict[str, Any],
    attempt: int,
    mode: str,
    cost: dict[str, Any],
    result: dict[str, Any] | None,
    transcript: Path,
) -> dict[str, Any]:
    return {
        "game": game["id"],
        "attempt": attempt,
        "mode": mode,
        "status": "complete" if cost["complete"] else "aborted",
        "abort_reason": None,
        "kind": game["kind"],
        "block": game["block"],
        "lineup_id": game["lineup_id"],
        "seed": game["seed"],
        "cost_usd": cost["game"]["cost_usd"],
        "calls": cost["game"]["calls"],
        "winner": result["winner"] if result else None,
        "win_reason": result["reason"] if result else None,
        "rounds": result["rounds"] if result else None,
        "transcript": transcript.as_posix(),
    }


def _reconcile(
    run_dir: Path,
    manifest: dict[str, Any],
    index: list[dict[str, Any]],
    done: set[str],
    mode: str,
) -> None:
    """Index a complete transcript whose index line was lost to a crash."""
    indexed = {r["game"] for r in index if r["status"] == "complete"}
    for game in manifest["games"]:
        if game["id"] in done and game["id"] not in indexed:
            path = transcript_path(run_dir, game["id"])
            records = read_jsonl(path)
            result = next(r for r in records if r["kind"] == "result")
            attempt = 1 + sum(r["game"] == game["id"] for r in index)
            line = _index_line(game, attempt, mode, records[-1], result, path.relative_to(run_dir))
            _append_line(run_dir / INDEX, line | {"recovered": True})


def _check_versions(manifest: dict[str, Any], mode: str) -> None:
    planned, current = manifest["engine"], engine_versions()
    keys = planned.keys() if mode == "live" else planned.keys() - {"harness"}
    changed = [k for k in sorted(keys) if planned[k] != current.get(k)]
    if changed:
        raise ValueError(
            f"engine changed since this run was scheduled ({', '.join(changed)}): "
            f"{ {k: planned[k] for k in changed} } -> { {k: current.get(k) for k in changed} }; "
            "schedule a new run"
        )


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _append_line(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


# CLI ----------------------------------------------------------------------------------


def add_commands(sub: Any) -> None:
    """``zantine schedule`` and ``zantine run``."""
    s = sub.add_parser("schedule", help="plan a run: Balanced Lobby blocks, Benign, Mirror")
    s.add_argument("--blocks", type=int, default=12, help="blocks of 28 games (default 12)")
    s.add_argument("--mirror", action="store_true", help="add 6 Mirror Matches per Model")
    s.add_argument(
        "--master-seed",
        type=int,
        help="seed every game seed is derived from (default: drawn at random and recorded)",
    )
    s.add_argument("--round-cap", type=int, help="override the round cap for every game")
    s.add_argument("--out", type=Path, help="run directory to create (default: print the JSON)")
    r = sub.add_parser("run", help="play or resume a scheduled run directory")
    r.add_argument("run_dir", type=Path)
    how = r.add_mutually_exclusive_group(required=True)
    how.add_argument("--dry-run", action="store_true", help="scripted bots stand in, $0")
    how.add_argument(
        "--live", action="store_true", help="call the Models' APIs (also needs ZANTINE_LIVE=1)"
    )
    r.add_argument("--spend-cap", type=float, help="USD limit for the whole run (live only)")
    r.add_argument("--max-games", type=int, help="play at most this many games now")
    r.add_argument("--quiet", action="store_true", help="print only the summary")


def command(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        if args.command == "schedule":
            return _schedule(args)
        return _run(args)
    except ValueError as exc:
        parser.error(str(exc))


def _schedule(args: argparse.Namespace) -> int:
    master_seed = args.master_seed if args.master_seed is not None else secrets.randbits(63)
    config = {"round_cap": args.round_cap} if args.round_cap is not None else {}
    manifest = make_manifest(
        load_pool(), master_seed, blocks=args.blocks, mirror=args.mirror, config=config
    )
    if args.out is None:
        print(json.dumps(manifest, indent=1, sort_keys=True))
        return 0
    path = create_run(args.out, manifest)
    print(f"{summary(manifest)}\nmanifest: {path}")
    return 0


def _run(args: argparse.Namespace) -> int:
    def show(line: dict[str, Any]) -> None:
        if args.quiet:
            return
        outcome = (
            f"ABORTED ({line['abort_reason']})"
            if line["status"] != "complete"
            else f"{line['winner']} by {line['win_reason']} after {line['rounds']} rounds"
        )
        print(f"  {line['game']:<16} {outcome}, ${line['cost_usd']:.4f}")

    report = execute(
        args.run_dir,
        mode="dry" if args.dry_run else "live",
        spend_cap_usd=args.spend_cap,
        live=args.live,
        max_games=args.max_games,
        progress=show,
    )
    print(
        f"{report.complete}/{report.planned} games complete ({len(report.played)} this time); "
        f"run spend ${report.spent_usd:.4f}"
    )
    if report.stopped:
        print(f"stopped by {report.stopped} at {report.stopped_game}; resumable")
        return 1
    return 0
