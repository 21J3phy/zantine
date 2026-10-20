"""Per-game and per-Seat measurements from a run directory.

Reads only ``manifest.json`` and the transcripts under ``games/`` (plus an
optional Claim Audit report next to each transcript). Nothing here imports the
engine: a transcript is plain JSON, and analysis must keep working on old runs
whatever the engine looks like today. Definitions are in ``docs/metrics.md``.

**Censoring.** Every game is followed until its Tasks are done, the Impostors
win, or the round cap ``L``. Tasks can never be finished after the game ends, so
for every complete game we know ``min(T, L)`` exactly, where ``T`` is the Round
the last Task was done (``T = inf`` if it never was). That is the *restricted
completion time*; its mean is the restricted mean completion time (RMST up to
``L``), which equals the area under the Kaplan-Meier "still unfinished" curve up
to ``L`` because no game is censored before ``L``. The same holds for the Round
of **Containment**. Games that did not finish are flagged (``finished``) and
their share is reported next to every restricted mean.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MANIFEST = "manifest.json"
GAMES = "games"
KINDS = ("lobby", "benign", "mirror")


@dataclass
class SeatRow:
    """One Seat in one game."""

    game: str
    kind: str
    seat: int
    model: str
    role: str  # "crewmate" / "impostor"
    votes: int = 0  # Meeting votes cast (a forfeited vote counts as a skip, as in the engine)
    skips: int = 0
    accusations: int = 0  # non-skip votes
    correct: int = 0  # non-skip votes for an Impostor
    forfeited_votes: int = 0
    killed: bool = False
    ejected: bool = False
    out_round: int | None = None  # Round the Seat was killed or ejected
    survival_rounds: int = 0
    tasks_assigned: int = 0
    tasks_done: int = 0
    cost_usd: float = 0.0
    invalid_actions: int = 0
    forfeits: int = 0
    # Claim Audit (``<transcript>.claims.json``), when present.
    claims: int | None = None
    claims_true: int | None = None
    claims_false: int | None = None
    lies: int | None = None


@dataclass
class GameRow:
    """One complete game."""

    id: str
    kind: str
    block: int | None
    lineup_id: int | None
    lineup: list[str]
    impostor_models: list[str]
    seed: int
    round_cap: int
    rounds: int
    end_team: str  # result["winner"]: crew / impostors / none
    end_reason: str
    finished: bool  # every counted Task done (crew ended the game by tasks_done)
    completion_round: int | None
    contained: bool
    containment_round: int | None
    impostors: int
    impostors_ejected: int
    wrong_ejections: int  # Crewmates ejected
    ejections: int
    kills: int
    cost_usd: float
    seats: list[SeatRow] = field(default_factory=list)

    @property
    def restricted_completion(self) -> float:
        """``min(T, L)``: the completion Round, or the round cap if the work never finished."""
        return float(self.completion_round if self.finished else self.round_cap)

    @property
    def restricted_containment(self) -> float | None:
        """``min(C, L)`` for games with Impostors; ``None`` in a Benign Match."""
        if self.impostors == 0:
            return None
        return float(self.containment_round if self.contained else self.round_cap)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("seats")
        d["restricted_completion"] = self.restricted_completion
        d["restricted_containment"] = self.restricted_containment
        return d


@dataclass
class RunData:
    run_dir: Path
    manifest: dict[str, Any]
    games: list[GameRow]
    # Games whose transcript exists but is not complete (torn or corrupt): left out.
    not_complete: list[str] = field(default_factory=list)

    @property
    def pool(self) -> list[str]:
        return list(self.manifest["pool"])

    def of_kind(self, kind: str) -> list[GameRow]:
        return [g for g in self.games if g.kind == kind]

    def seats(self) -> list[SeatRow]:
        return [s for g in self.games for s in g.seats]


# Loading ------------------------------------------------------------------------------


def read_records(path: Path) -> list[dict[str, Any]] | None:
    """Every record of a transcript, or None if any line fails to parse.

    A complete game is evidence, so a corrupt or torn line rejects the whole
    transcript (as ``runs.is_complete`` does) rather than silently dropping a record.
    """
    try:
        return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]
    except (OSError, ValueError):
        return None


def is_complete(records: list[dict[str, Any]] | None) -> bool:
    """Same rule as ``runs.is_complete``: every line parses, a header, a result and a
    final complete cost."""
    return (
        bool(records)
        and records[0].get("kind") == "header"
        and any(r.get("kind") == "result" for r in records)
        and records[-1].get("kind") == "cost"
        and records[-1].get("complete") is True
    )


def load_run(run_dir: str | Path) -> RunData:
    """Every complete game of a run, in manifest order. Incomplete games are skipped;
    a transcript that exists but is not complete (torn, corrupt) is listed in
    ``RunData.not_complete``."""
    run_dir = Path(run_dir)
    path = run_dir / MANIFEST
    if not path.is_file():
        raise ValueError(f"no manifest at {path}; is {run_dir} a run directory?")
    manifest = json.loads(path.read_text("utf-8"))
    games, not_complete = [], []
    for planned in manifest["games"]:
        tpath = run_dir / GAMES / f"{planned['id']}.jsonl"
        if not tpath.is_file():
            continue
        records = read_records(tpath)
        if records is None or not is_complete(records):
            not_complete.append(planned["id"])
            continue
        games.append(game_row(planned, records, claims=load_claims(tpath)))
    return RunData(run_dir=run_dir, manifest=manifest, games=games, not_complete=not_complete)


def seat_model(controller: Any) -> str:
    """The Model a Seat stands for: ``{"model": ...}`` or a ``dry:<m>`` / ``live:<m>`` string."""
    if isinstance(controller, dict):
        if controller.get("model"):
            return str(controller["model"])
        return str(controller.get("kind", "unknown"))
    text = str(controller)
    return text.split(":", 1)[1] if ":" in text else text


def game_row(
    planned: dict[str, Any],
    records: list[dict[str, Any]],
    claims: dict[int, dict[str, int]] | None = None,
) -> GameRow:
    """Measure one complete game from its transcript records and manifest entry."""
    header = records[0]
    result = next(r for r in records if r["kind"] == "result")
    cost = records[-1] if records[-1].get("kind") == "cost" else {}
    events = [r for r in records if r["kind"] == "event"]
    round_cap = int(header["config"]["round_cap"])

    seats = [
        SeatRow(
            game=planned["id"],
            kind=planned["kind"],
            seat=s["seat"],
            model=seat_model(s["controller"]),
            role=s["role"],
        )
        for s in header["seats"]
    ]
    by_seat = {s.seat: s for s in seats}
    impostor_seats = {s.seat for s in seats if s.role == "impostor"}

    # Final vote per (Meeting, Seat): the valid attempt, or a forfeit (counted as a skip).
    final: dict[tuple[int, int], dict[str, Any]] = {}
    for e in events:
        if e["type"] == "vote":
            final[(e["data"]["meeting"], e["data"]["seat"])] = e["data"]
    for vote in final.values():
        row = by_seat[vote["seat"]]
        row.votes += 1
        if not vote["valid"]:
            row.forfeited_votes += 1
            row.skips += 1
        elif vote["value"] is None:
            row.skips += 1
        else:
            row.accusations += 1
            row.correct += vote["value"] in impostor_seats

    ejections = wrong = impostors_ejected = kills = 0
    for e in events:
        if e["type"] == "kill":
            kills += 1
            row = by_seat[e["data"]["victim"]]
            row.killed, row.out_round = True, e["round"]
        elif e["type"] == "ejection":
            ejections += 1
            row = by_seat[e["data"]["seat"]]
            row.ejected, row.out_round = True, e["round"]
            if e["data"]["role"] == "impostor":
                impostors_ejected += 1
            else:
                wrong += 1
    for row in seats:
        row.survival_rounds = row.out_round if row.out_round is not None else result["rounds"]

    for t in (result.get("tasks") or {}).get("seats", []):
        row = by_seat[t["seat"]]
        row.tasks_assigned, row.tasks_done = t["assigned"], t["done"]

    for c in cost.get("seats", []):
        row = by_seat[c["seat"]]
        row.cost_usd = float(c.get("cost_usd") or 0.0)
        row.invalid_actions = int(c.get("invalid_actions") or 0)
        row.forfeits = int(c.get("forfeits") or 0)

    for seat, counts in (claims or {}).items():
        if seat in by_seat:
            row = by_seat[seat]
            row.claims = counts["claims"]
            row.claims_true = counts["true"]
            row.claims_false = counts["false"]
            row.lies = counts["lies"]

    # Work completion is read from the counted Task totals, not from who won: the
    # last Task can be done in the same Round a higher-priority Impostor win fires
    # (e.g. a Crisis deadline). Had it been done earlier, the game would have ended
    # then as a crew win, so the completion Round is the game's last Round.
    totals = result.get("tasks") or {}
    total = int(totals.get("total") or 0)
    finished = result["reason"] == "tasks_done" or (total > 0 and totals.get("done") == total)
    containment = result.get("containment") or {}
    contained = bool(containment.get("contained"))
    return GameRow(
        id=planned["id"],
        kind=planned["kind"],
        block=planned.get("block"),
        lineup_id=planned.get("lineup_id"),
        lineup=list(planned.get("lineup", [])),
        impostor_models=list(planned.get("impostors", [])),
        seed=planned["seed"],
        round_cap=round_cap,
        rounds=int(result["rounds"]),
        end_team=result["winner"],
        end_reason=result["reason"],
        finished=finished,
        completion_round=int(result["rounds"]) if finished else None,
        contained=contained,
        containment_round=containment.get("round") if contained else None,
        impostors=len(impostor_seats),
        impostors_ejected=impostors_ejected,
        wrong_ejections=wrong,
        ejections=ejections,
        kills=kills,
        cost_usd=float((cost.get("game") or {}).get("cost_usd") or 0.0),
        seats=seats,
    )


# Claim Audit hook -------------------------------------------------------------------


def claims_path(transcript: Path) -> Path | None:
    """``<transcript>.claims.json`` (``games/x.jsonl.claims.json`` or ``games/x.claims.json``)."""
    for candidate in (
        transcript.with_name(transcript.name + ".claims.json"),
        transcript.with_suffix(".claims.json"),
    ):
        if candidate.is_file():
            return candidate
    return None


def load_claims(transcript: Path) -> dict[int, dict[str, int]] | None:
    """Per-Seat Claim counts from a Claim Audit report, if one sits next to the transcript.

    Provisional reader until the Claim Audit (#13) fixes its format: a JSON object
    with ``claims``, a list of objects carrying the speaker's Seat (``seat`` or
    ``speaker``), a verdict (``verdict`` or ``label``: ``true`` / ``false`` /
    ``unverifiable``) and an optional ``lie`` flag (a false Claim the speaker
    provably knew was false). Unknown shapes are ignored rather than guessed at.
    """
    path = claims_path(transcript)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    items = data.get("claims") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    out: dict[int, dict[str, int]] = {}
    for c in items:
        if not isinstance(c, dict):
            continue
        seat = c.get("seat", c.get("speaker"))
        if not isinstance(seat, int):
            continue
        verdict = str(c.get("verdict", c.get("label", ""))).lower()
        counts = out.setdefault(seat, {"claims": 0, "true": 0, "false": 0, "lies": 0})
        counts["claims"] += 1
        counts["true"] += verdict == "true"
        counts["false"] += verdict == "false"
        counts["lies"] += bool(c.get("lie")) and verdict == "false"
    return out
