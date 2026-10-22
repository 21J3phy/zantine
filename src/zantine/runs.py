"""Run directories: play a scheduled manifest, resumably, under one spend cap.

Layout of ``runs/<run-id>/`` (other tools read it; see ``docs/architecture.md``)::

    manifest.json            the plan (schedule.make_manifest); written once, never changed
    run.json                 the run's mode (dry or live), written before its first game
    run.lock                 held (flock) by the one ``zantine run`` using the directory
    index.jsonl              one line per finished attempt (complete or aborted), append-only
    spend.jsonl              a reservation before every paid call and its settled cost
                             after it, plus a hold for every call that failed without a
                             reply but may still have been billed; append-only
    games/<game-id>.jsonl    the transcript of each complete game
    aborted/<game-id>.<attempt>.jsonl   transcripts of attempts stopped by the spend cap
                                        or a fatal API error (kept for the spend record)
    commitment.json          the Seed Commitment, published before results (``repro.py``)
    commitment.secret.json   its nonce; private until the version is retired

A game is done when ``games/<game-id>.jsonl`` parses, has a ``result`` record
and ends with a complete ``cost`` record. Transcripts are written to a temporary
file and renamed, so a crash leaves either a complete transcript or none. Resuming
plays every game that is not done, in manifest order, so no game is duplicated
or skipped. The spend cap covers the whole run: spending already in
``spend.jsonl`` (including aborted and crashed attempts) counts against it, so a
run stopped by the cap continues when invoked again with a higher cap. Each
paid call's worst-case cost is reserved in the log before it is sent and settled
at its real cost afterwards; a reservation never settled (the process died
mid-call) counts at its worst case. Only one invocation may use a run directory
at a time, and a torn last line in either log is fenced off before appending.

``--dry-run`` plays every Seat with ``dry:<model>`` scripted bots at $0; ``--live``
plays ``live:<model>`` Seats through ``harness.live.play_live`` and needs the
usual opt-ins (the flag plus ``ZANTINE_LIVE=1``). A run directory holds one mode.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zantine import speed
from zantine.config import GameConfig
from zantine.harness.ledger import Ledger
from zantine.harness.live import LiveRun, play_live, require_live
from zantine.harness.pricing import ModelSpec
from zantine.harness.provider import ProviderAdapter
from zantine.rng import GameRng
from zantine.scenario import build_scenario
from zantine.schedule import (
    EFFORT,
    EFFORT_PER_LEVEL,
    MINORITY,
    VERIFICATION,
    VERIFICATION_BLOCKS,
    VERIFICATION_NEW_PROTOCOLS,
    engine_versions,
    load_pool,
    make_effort_manifest,
    make_manifest,
    make_minority_manifest,
    make_verification_manifest,
    seat_lineup,
    summary,
)

if TYPE_CHECKING:
    from zantine.stylemask import StyleMask

MANIFEST = "manifest.json"
RUN = "run.json"
LOCK = "run.lock"
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
    the scheduled Impostor Models (``schedule.seat_lineup``). A speed-experiment
    game also gets its per-Seat Talk Budgets (``speed.talk_config``). A game with
    an ``effort`` (the effort sweep) seats every Model at that level.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    entries, impostor_positions = seat_lineup(game)
    overrides = {**manifest.get("config", {}), **game["config"]}
    if overrides.get("style_mask") and not hasattr(GameConfig, "style_mask"):
        # Never play a mask-on game without the mask: it would silently become mask-off.
        raise ValueError(
            f"{game['id']} is a Style Mask game, but this engine has no Style Mask "
            "(GameConfig.style_mask arrives with #66)"
        )
    config = GameConfig(seed=game["seed"], **overrides)
    if impostor_positions or game.get("talk"):
        scenario = build_scenario(config, GameRng(config.seed).fork("scenario"))
        seats = tuple(s for s, pos in enumerate(scenario.seat_order) if pos in impostor_positions)
        config = replace(
            config,
            impostor_seats=seats if impostor_positions else config.impostor_seats,
            **speed.talk_config(manifest, game, scenario.seat_order),
        )
    option = f"?effort={game['effort']}" if game.get("effort") else ""
    return config, ",".join(f"{mode}:{model}{option}" for model in entries)


def check_seating(game: dict[str, Any], header: dict[str, Any]) -> None:
    """The transcript header must seat exactly the scheduled Models, Impostors and effort."""
    seats = header["seats"]
    entries, _ = seat_lineup(game)
    models = Counter(s["controller"]["model"] for s in seats)
    impostors = sorted(s["controller"]["model"] for s in seats if s["role"] == "impostor")
    if models != Counter(entries) or impostors != sorted(game["impostors"]):
        raise RuntimeError(f"{game['id']}: seating does not match the schedule")
    effort = game.get("effort")
    if effort is not None and any(s["controller"].get("effort") != effort for s in seats):
        raise RuntimeError(f"{game['id']}: Seats did not play at effort {effort!r}")


def planned_setup(manifest: dict[str, Any], game: dict[str, Any]) -> dict[str, Any]:
    """What the header of the planned game must say, rebuilt on this engine.

    ``config``: the full ``GameConfig`` as the header records it (JSON-normalized);
    ``seats``: each Seat's ``(model, role)`` in Seat order, from the seeded seat
    order. Only meaningful while the engine still matches the manifest
    (``engine_matches``); raises ``ValueError`` if the game cannot be set up here.
    """
    config, _ = game_setup(manifest, game, "dry")
    scenario = build_scenario(config, GameRng(config.seed).fork("scenario"))
    entries, _ = seat_lineup(game)
    impostors = set(config.impostor_seats or ())
    seats = [
        (model, "impostor" if seat in impostors else "crewmate")
        for seat, model in enumerate(scenario.seat(entries))
    ]
    return {"config": json.loads(json.dumps(config.to_dict())), "seats": seats}


def engine_matches(manifest: dict[str, Any]) -> bool:
    """The scenario and transcript versions are still the ones the run was scheduled with."""
    planned, current = manifest["engine"], engine_versions()
    return all(planned.get(k) == current[k] for k in ("scenario_version", "transcript_schema"))


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
    """Everything the run has spent so far, from the spend log: every settled call,
    the worst case of every uncertain call (failed without an HTTP status, so the
    provider may have billed it) and every other hold, plus the worst case of any
    reservation never settled (a crash mid-call)."""
    lines = read_jsonl(run_dir / SPEND)
    settled = {r["reservation"] for r in lines if r["kind"] == "call"}
    total = sum(r["cost_usd"] for r in lines if r["kind"] == "call")
    total += sum(r["held_usd"] for r in lines if r["kind"] == "call" and r.get("uncertain"))
    total += sum(r["hold_usd"] for r in lines if r["kind"] == "hold")
    total += sum(
        r["reserved_usd"]
        for r in lines
        if r["kind"] == "reserve" and r["reservation"] not in settled
    )
    return round(total, 8)


class RunLedger(Ledger):
    """A Ledger that journals every paid call to ``spend.jsonl``.

    Before a call is sent, a ``reserve`` line records its worst-case cost; after
    it, a ``call`` line settles it at its real cost. A call that failed without an
    HTTP status (lost in transit, untrustworthy usage) may still have been billed:
    its ``call`` line is ``uncertain`` and keeps its worst case as ``held_usd``,
    in the same single append that settles it, so no crash can free that charge.
    A caller's ``hold`` of that same call adds nothing more; any other ``hold`` is
    its own ``hold`` line. The log survives a crash at any point, so the run-level
    cap stays honest. ``game_attempt`` is the attempt
    at the scheduled game; ``call_attempt`` is the Seat's retry of one decision.
    """

    def __init__(self, spend_cap_usd: float, path: Path) -> None:
        super().__init__(spend_cap_usd)
        self.path = path
        self.game: str | None = None
        self.attempt = 0
        self.spent_usd = spent_usd(path.parent)
        self._calls = 0
        self._pending: str | None = None
        self._pending_usd = 0.0
        # Worst case of the last uncertain call, already counted, until its caller holds it.
        self._held_uncertain: float | None = None

    def _reserve(self, model: ModelSpec, needed: float, attempt: int) -> None:
        self._held_uncertain = None
        self._pending_usd = needed
        self._calls += 1
        self._pending = f"{self.game}/{self.attempt}/{self._calls}"
        line = {
            "kind": "reserve",
            "reservation": self._pending,
            "game": self.game,
            "game_attempt": self.attempt,
            "call_attempt": attempt,
            "model": model.id,
            "reserved_usd": needed,
        }
        _append_line(self.path, line)

    def hold(self, amount_usd: float) -> None:
        if self._held_uncertain is not None and amount_usd == self._held_uncertain:
            # The uncertain call's own line already holds this; don't charge it twice.
            self._held_uncertain = None
            return
        super().hold(amount_usd)
        line = {
            "kind": "hold",
            "game": self.game,
            "game_attempt": self.attempt,
            "hold_usd": amount_usd,
        }
        _append_line(self.path, line)

    def _record(self, *args: Any, uncertain: bool = False, **kwargs: Any) -> None:
        super()._record(*args, **kwargs)
        record = self.records[-1].to_dict()
        line = {
            "kind": "call",
            "reservation": self._pending,
            "game": self.game,
            "game_attempt": self.attempt,
            "call_attempt": record.pop("attempt"),
            **record,
        }
        if uncertain:
            line |= {"uncertain": True, "held_usd": self._pending_usd}
        _append_line(self.path, line)
        if uncertain:
            self.spent_usd += self._pending_usd
            self._held_uncertain = self._pending_usd
        self._pending = None


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
    style_mask: Callable[[Ledger], StyleMask] | None = None,
) -> RunReport:
    """Play (or resume) every game of the run that is not done yet.

    Live Style Mask games need ``style_mask``: a factory that builds the run's
    masker and meaning check on the given (run) Ledger, called once per mask-on
    game (``stylemask.build_style_mask``). Its pinned identity is recorded in
    ``run.json`` before the first game, and a resume with a different one is
    refused. Dry runs use the free dry-run mask and take no factory.

    Raises ``RunLocked`` if another invocation is using the run directory.
    """
    manifest = load_manifest(run_dir)
    _check_versions(manifest, mode)
    masked = [g["id"] for g in manifest["games"] if g["config"].get("style_mask")]
    if masked and not hasattr(GameConfig, "style_mask"):
        raise ValueError(
            f"{len(masked)} games need the Style Mask, which this engine lacks "
            "(GameConfig.style_mask arrives with #66); nothing was played"
        )
    if mode == "live":
        speed.check_live(manifest)
        if spend_cap_usd is None:
            raise ValueError("live runs need an explicit --spend-cap (USD for the whole run)")
        if masked and style_mask is None:
            # play_live would fall back to the free dry-run mask, not the pinned masker.
            raise ValueError(
                f"{len(masked)} live Style Mask games need the pinned masker: "
                "pass --style-mask <masker id> (stylemask/maskers.toml)"
            )
        if style_mask is not None and not masked:
            raise ValueError("--style-mask was given, but no game in this run uses the Style Mask")
        if adapter_factory is None:
            require_live(live)
        cap = spend_cap_usd
    elif mode == "dry":
        if style_mask is not None:
            raise ValueError("dry runs use the free dry-run Style Mask; --style-mask is for --live")
        cap = DRY_RUN_CAP_USD
    else:
        raise ValueError(f"mode must be one of {MODES}")
    play = {"live": live, "adapter_factory": adapter_factory, "models": models}
    with _locked(run_dir):
        return _execute(run_dir, manifest, mode, cap, max_games, progress, play, style_mask)


def _execute(
    run_dir: Path,
    manifest: dict[str, Any],
    mode: str,
    cap: float,
    max_games: int | None,
    progress: Callable[[dict[str, Any]], None] | None,
    play: dict[str, Any],
    style_mask: Callable[[Ledger], StyleMask] | None = None,
) -> RunReport:
    _claim_mode(run_dir, mode)
    ledger = RunLedger(cap, run_dir / SPEND)
    if style_mask is not None:
        _claim_style_mask(run_dir, style_mask(ledger).info())
    games = manifest["games"]
    done = {g["id"] for g in games if is_complete(transcript_path(run_dir, g["id"]))}
    _reconcile(run_dir, manifest, done, mode)
    report = RunReport(planned=len(games), complete=len(done))
    attempts = _attempts(run_dir)

    for game in games:
        if game["id"] in done:
            continue
        if max_games is not None and len(report.played) >= max_games:
            break
        if mode == "live" and ledger.spent_usd >= cap:
            report.stopped, report.stopped_game = "spend_cap", game["id"]
            break
        attempt = max(attempts.get(game["id"], {0})) + 1
        ledger.game, ledger.attempt = game["id"], attempt
        config, spec = game_setup(manifest, game, mode)
        mask = style_mask(ledger) if style_mask is not None and config.style_mask else None
        run = play_live(config, spec, spend_cap_usd=cap, ledger=ledger, style_mask=mask, **play)
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
        "effort": game.get("effort"),
        "seed": game["seed"],
        "cost_usd": cost["game"]["cost_usd"],
        "calls": cost["game"]["calls"],
        "winner": result["winner"] if result else None,
        "win_reason": result["reason"] if result else None,
        "rounds": result["rounds"] if result else None,
        "transcript": transcript.as_posix(),
    }


def _attempts(run_dir: Path) -> dict[str, set[int]]:
    """Every game attempt on record: in the index, the spend log or ``aborted/``."""
    out: dict[str, set[int]] = {}
    for r in read_jsonl(run_dir / INDEX):
        out.setdefault(r["game"], set()).add(r["attempt"])
    for r in read_jsonl(run_dir / SPEND):
        out.setdefault(r["game"], set()).add(r["game_attempt"])
    for game_id, attempt, _ in _aborted_files(run_dir):
        out.setdefault(game_id, set()).add(attempt)
    return out


def _aborted_files(run_dir: Path) -> list[tuple[str, int, Path]]:
    found = []
    for path in sorted((run_dir / ABORTED).glob("*.jsonl")):
        game_id, _, attempt = path.stem.rpartition(".")
        if attempt.isdigit():
            found.append((game_id, int(attempt), path))
    return sorted(found)


def _transcript_mode(path: Path, records: list[dict[str, Any]]) -> str:
    kinds = {s["controller"]["kind"] for s in records[0]["seats"]} if records else set()
    if len(kinds) != 1 or not kinds <= set(MODES):
        raise ValueError(f"{path}: Seats are {sorted(kinds)}, not one run mode")
    return kinds.pop()


def _claim_mode(run_dir: Path, mode: str) -> None:
    """Record the run's mode before its first game, and refuse any other mode.

    A directory without ``run.json`` is checked against everything already in it
    (index lines and the Seats of every transcript) before it is claimed.
    """
    path = run_dir / RUN
    if path.is_file():
        held = {json.loads(path.read_text("utf-8"))["mode"]}
    else:
        held = {r["mode"] for r in read_jsonl(run_dir / INDEX)}
        transcripts = sorted((run_dir / GAMES).glob("*.jsonl"))
        transcripts += [p for _, _, p in _aborted_files(run_dir)]
        for t in transcripts:
            records = read_jsonl(t)
            if records and records[0].get("kind") == "header":
                held.add(_transcript_mode(t, records))
    if held - {mode}:
        raise ValueError(f"{run_dir} holds {', '.join(sorted(held))} games; cannot add {mode}")
    if not path.is_file():
        _write_atomic(path, json.dumps({"mode": mode}, sort_keys=True) + "\n")


_RUNTIME_KEYS = frozenset({"served", "usage_estimated", "held"})


def _pinned(info: Any) -> Any:
    """A Style Mask identity without what changes while it runs (served models, holds)."""
    if isinstance(info, dict):
        return {k: _pinned(v) for k, v in sorted(info.items()) if k not in _RUNTIME_KEYS}
    if isinstance(info, list):
        return [_pinned(v) for v in info]
    return info


def _claim_style_mask(run_dir: Path, info: dict[str, Any]) -> None:
    """Record the run's Style Mask identity in ``run.json``; refuse a different one later."""
    path = run_dir / RUN
    held = json.loads(path.read_text("utf-8"))
    pin = _pinned(info)
    if "style_mask" in held and held["style_mask"] != pin:
        raise ValueError(
            f"{run_dir} was played with a different Style Mask (masker, meaning check or "
            "their pinned versions); resume it with the same one"
        )
    if "style_mask" not in held:
        _write_atomic(path, json.dumps({**held, "style_mask": pin}, sort_keys=True) + "\n")


def _reconcile(run_dir: Path, manifest: dict[str, Any], done: set[str], mode: str) -> None:
    """Index finished attempts whose index line was lost to a crash.

    Each recovered line takes its mode from the transcript's own Seats, which must
    match the run's mode, and its attempt from the run's records.
    """
    index = read_jsonl(run_dir / INDEX)
    indexed = {(r["game"], r["attempt"]) for r in index}
    plan = {g["id"]: g for g in manifest["games"]}
    for game_id, attempt, path in _aborted_files(run_dir):
        if game_id in plan and (game_id, attempt) not in indexed:
            _recover(run_dir, plan[game_id], attempt, path, mode)
    attempts = _attempts(run_dir)
    complete = {r["game"] for r in read_jsonl(run_dir / INDEX) if r["status"] == "complete"}
    for game in manifest["games"]:
        if game["id"] in done and game["id"] not in complete:
            # The complete attempt is the one with spend on record but nothing else,
            # or else the next one after every attempt on record.
            known = {a for g, a in indexed if g == game["id"]}
            known |= {a for g, a, _ in _aborted_files(run_dir) if g == game["id"]}
            unaccounted = attempts.get(game["id"], set()) - known
            attempt = max(unaccounted) if unaccounted else max(known, default=0) + 1
            _recover(run_dir, game, attempt, transcript_path(run_dir, game["id"]), mode)


def _recover(run_dir: Path, game: dict[str, Any], attempt: int, path: Path, mode: str) -> None:
    records = read_jsonl(path)
    if _transcript_mode(path, records) != mode:
        raise ValueError(f"{path} was not played as a {mode} game; this run directory is {mode}")
    result = next((r for r in records if r["kind"] == "result"), None)
    aborted = next((r for r in records if r["kind"] == "aborted"), None)
    line = _index_line(game, attempt, mode, records[-1], result, path.relative_to(run_dir))
    line["abort_reason"] = aborted["reason"] if aborted else None
    _append_line(run_dir / INDEX, line | {"recovered": True})


def _check_versions(manifest: dict[str, Any], mode: str) -> None:
    planned, current = manifest["engine"], engine_versions(manifest.get("experiment"))
    # A version the current engine pins but the manifest lacks counts as changed:
    # the run was scheduled before those rules were versioned.
    keys = planned.keys() | current.keys()
    keys = keys if mode == "live" else keys - {"harness"}
    changed = [k for k in sorted(keys) if planned.get(k) != current.get(k)]
    if changed:
        was = {k: planned.get(k) for k in changed}
        now = {k: current.get(k) for k in changed}
        raise ValueError(
            f"engine changed since this run was scheduled ({', '.join(changed)}): "
            f"{was} -> {now}; schedule a new run"
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
    """Append one JSON line. A torn last line (a crash mid-append) is first ended
    with a newline, so it stays a single unparseable line that ``read_jsonl``
    skips instead of swallowing this record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    with path.open("a+b") as f:
        if f.seek(0, os.SEEK_END):
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                data = b"\n" + data
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


class RunLocked(ValueError):
    """Another invocation is using the run directory."""


@contextmanager
def _locked(run_dir: Path) -> Iterator[None]:
    """Hold the run directory exclusively for one invocation (released on exit,
    including when the process dies)."""
    with (run_dir / LOCK).open("a") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunLocked(f"{run_dir} is in use by another `zantine run`") from None
        yield


# CLI ----------------------------------------------------------------------------------


def add_commands(sub: Any) -> None:
    """``zantine schedule`` and ``zantine run``."""
    s = sub.add_parser("schedule", help="plan a run: Balanced Lobby blocks, Benign, Mirror")
    s.add_argument(
        "--experiment",
        choices=("v1", speed.EXPERIMENT, EFFORT, MINORITY, VERIFICATION),
        default="v1",
        help="v1 (default); speed: random per-Seat Talk Budget tiers plus a realism check; "
        "effort: the v1.6 effort sweep, Mirror Matches at low and high effort; "
        "minority: v1.3 Lopsided Lobbies, 42 ordered pairs x 2 games x Style Mask off/on "
        f"(168 games); {VERIFICATION}: the v1.1 Verification Protocol experiment "
        "(docs/sample-size.md)",
    )
    s.add_argument(
        "--blocks",
        type=int,
        help=f"blocks of 28 games (default 12; {speed.TIER_BLOCKS} for --experiment speed; "
        f"{VERIFICATION_BLOCKS} per protocol for --experiment {VERIFICATION}; not for minority)",
    )
    s.add_argument("--mirror", action="store_true", help="add 6 Mirror Matches per Model (v1)")
    s.add_argument(
        "--per-level",
        type=int,
        help=f"Mirror Matches per Model per effort level (effort; default {EFFORT_PER_LEVEL})",
    )
    s.add_argument(
        "--realism-games",
        type=int,
        default=speed.REALISM_GAMES,
        help=f"speed only: games whose budgets follow Model throughput "
        f"(default {speed.REALISM_GAMES})",
    )
    s.add_argument(
        "--protocols",
        help=f"comma-separated Verification Protocols to schedule with --experiment "
        f"{VERIFICATION} (default {','.join(VERIFICATION_NEW_PROTOCOLS)}; the optional "
        "baseline is the v1 run)",
    )
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
    r.add_argument(
        "--style-mask",
        metavar="MASKER",
        help="live runs with Style Mask games: 'neutral' or an id from stylemask/maskers.toml",
    )
    r.add_argument(
        "--mask-extractor",
        default="pattern",
        help="Claim extractor for the Style Mask meaning check (default pattern)",
    )
    r.add_argument(
        "--mask-judge",
        default="keyword",
        choices=("keyword", "jev"),
        help="judge for the Style Mask meaning check (default keyword)",
    )


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
    if args.per_level is not None and args.experiment != EFFORT:
        raise ValueError("--per-level is for --experiment effort")
    if args.protocols and args.experiment != VERIFICATION:
        raise ValueError(f"--protocols needs --experiment {VERIFICATION}")
    if args.experiment == EFFORT:
        if args.blocks is not None or args.mirror:
            raise ValueError("--blocks and --mirror are not for the effort sweep")
        per_level = EFFORT_PER_LEVEL if args.per_level is None else args.per_level
        manifest = make_effort_manifest(
            load_pool(), master_seed, per_level=per_level, config=config
        )
        text = summary(manifest)
    elif args.experiment == MINORITY:
        if args.blocks is not None or args.mirror:
            raise ValueError("--blocks and --mirror do not apply to --experiment minority")
        manifest = make_minority_manifest(load_pool(), master_seed, config=config)
        text = summary(manifest)
    elif args.experiment == speed.EXPERIMENT:
        if args.mirror:
            raise ValueError("the speed experiment has no Mirror Matches")
        manifest = speed.make_speed_manifest(
            load_pool(),
            master_seed,
            blocks=speed.TIER_BLOCKS if args.blocks is None else args.blocks,
            realism_games=args.realism_games,
            config=config,
        )
        text = speed.summary(manifest)
    elif args.experiment == VERIFICATION:
        if args.mirror:
            raise ValueError("the Verification Protocol experiment has no Mirror Matches")
        protocols = (
            [p.strip() for p in args.protocols.split(",") if p.strip()]
            if args.protocols
            else list(VERIFICATION_NEW_PROTOCOLS)
        )
        manifest = make_verification_manifest(
            load_pool(),
            master_seed,
            blocks=VERIFICATION_BLOCKS if args.blocks is None else args.blocks,
            protocols=protocols,
            config=config,
        )
        text = summary(manifest)
    else:
        blocks = 12 if args.blocks is None else args.blocks
        manifest = make_manifest(
            load_pool(), master_seed, blocks=blocks, mirror=args.mirror, config=config
        )
        text = summary(manifest)
    if args.out is None:
        print(json.dumps(manifest, indent=1, sort_keys=True))
        return 0
    path = create_run(args.out, manifest)
    print(f"{text}\nmanifest: {path}")
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

    style_mask = None
    if args.style_mask is not None:
        from zantine.stylemask import build_style_mask

        def style_mask(ledger: Ledger) -> StyleMask:
            return build_style_mask(
                args.style_mask,
                args.mask_extractor,
                args.mask_judge,
                ledger=ledger,
                live=args.live,
            )

    report = execute(
        args.run_dir,
        mode="dry" if args.dry_run else "live",
        spend_cap_usd=args.spend_cap,
        live=args.live,
        max_games=args.max_games,
        progress=show,
        style_mask=style_mask,
    )
    print(
        f"{report.complete}/{report.planned} games complete ({len(report.played)} this time); "
        f"run spend ${report.spent_usd:.4f}"
    )
    if report.stopped:
        print(f"stopped by {report.stopped} at {report.stopped_game}; resumable")
        return 1
    return 0
