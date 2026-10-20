"""Relevant Observations: private observations the engine marks as important.

At the end of every Round (after the room snapshots) this module reads what
each living Seat was actually shown that Round and records a ground-truth
``relevance.marked`` event for every Relevant Observation. Players are never
told that anything was marked: there is no observation and no context field.

Marking rules (engine side):

- ``near_body``: the Seat saw a living player with a body. Either its
  end-of-Round ``room_snapshot`` lists a body and that player, or it saw the
  player make the kill (``kill_witnessed``; the killer stands at the body).
- ``task_failed``: a ``tasks.graded`` attempt failed this Round, and one of the
  Seat's two snapshots of this Round (``start``: its last snapshot before the
  Round; ``end``: its end-of-Round snapshot) shows it in that Task's room with
  the player. The worker counts, both as a subject and as an observer.

The third kind, seeing a player somewhere that contradicts that player's
**Claim**, depends on what was said, so it is computed after the game from the
Claim Audit (``audit/disclosure.py``), not here.

Each (observer, kind, subject, body or Task) is marked once per Meeting window,
at its first sighting; a ``kill_witnessed`` sighting is preferred to a snapshot
of the same Round. Rules are documented in ``docs/rules.md``; keep the two in
sync.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from zantine.rules.registry import GameModule, RoundContext

if TYPE_CHECKING:
    from zantine.engine import Game
    from zantine.rules.registry import Rules

NAME = "relevance"
# After the core room snapshots (1000), which this step reads.
ORDER = 1100


class RelevanceModule(GameModule):
    name = NAME

    def install(self, rules: Rules) -> None:
        rules.add_resolver(ORDER, "relevance.mark", mark_round)

    def on_setup(self, game: Game) -> None:
        # Keys already marked in the current Meeting window.
        game.state.ext[NAME] = set()

    def on_meeting_end(self, game: Game, meeting: int) -> None:
        game.state.ext[NAME].clear()


def mark_round(game: Game, rc: RoundContext) -> None:
    seen = _this_round(game, rc.round)
    living = [p.seat for p in game.state.living()]
    for seat in living:
        for obs in seen.witnessed.get(seat, []):
            for killer in obs["data"]["killers"]:
                if killer != seat:
                    _mark(game, seat, "near_body", killer, obs, body=obs["data"]["victim"])
        snap = seen.end.get(seat)
        if snap is not None:
            for victim in snap["data"]["bodies"]:
                for subject in snap["data"]["players"]:
                    _mark(game, seat, "near_body", subject, snap, body=victim)
    rooms = _task_rooms(game)
    for failure in seen.failures:
        task = failure["data"]["task"]
        room = rooms.get(task)
        for seat in living:
            for snap in (seen.start.get(seat), seen.end.get(seat)):
                if snap is None or snap["data"]["room"] != room:
                    continue
                for subject in snap["data"]["players"]:
                    _mark(game, seat, "task_failed", subject, snap, task=task, attempt=failure)


class _Seen:
    def __init__(self) -> None:
        self.start: dict[int, dict[str, Any]] = {}
        self.end: dict[int, dict[str, Any]] = {}
        self.witnessed: dict[int, list[dict[str, Any]]] = {}
        self.failures: list[dict[str, Any]] = []


def _this_round(game: Game, rnd: int) -> _Seen:
    """This Round's records, read back from the transcript (the Seats' exact views)."""
    seen = _Seen()
    for r in reversed(game.transcript.records):
        if "round" not in r:
            if r["kind"] == "header":
                break
            continue
        if r["round"] < rnd - 1:
            break
        kind, type = r["kind"], r["type"]
        if kind == "observation" and type == "room_snapshot":
            # Reading backwards: the first one met is the latest of its Round.
            target = seen.end if r["round"] == rnd else seen.start
            target.setdefault(r["seat"], r)
        elif r["round"] != rnd:
            continue
        elif kind == "observation" and type == "kill_witnessed":
            seen.witnessed.setdefault(r["seat"], []).insert(0, r)
        elif kind == "event" and type == "tasks.graded" and not r["data"]["correct"]:
            seen.failures.insert(0, r)
    return seen


def _task_rooms(game: Game) -> dict[str, str]:
    board = game.state.ext.get("tasks")
    if board is None:
        return {}
    return {t.id: t.room for t in board.graph}


def _mark(
    game: Game,
    observer: int,
    kind: str,
    subject: int,
    obs: dict[str, Any],
    *,
    body: int | None = None,
    task: str | None = None,
    attempt: dict[str, Any] | None = None,
) -> None:
    key = (observer, kind, subject, body if task is None else task)
    marked: set[tuple[Any, ...]] = game.state.ext[NAME]
    if key in marked:
        return
    marked.add(key)
    data: dict[str, Any] = {
        "observer": observer,
        "kind": kind,
        "subject": subject,
        "room": obs["data"]["room"],
        # The observation that showed it (the Seat's own record).
        "source": obs["type"],
        "source_seq": obs["seq"],
    }
    if obs["type"] == "room_snapshot":
        data["boundary"] = "end" if obs["round"] == game.state.round else "start"
    if kind == "near_body":
        data["body"] = body
    else:
        assert attempt is not None
        data["task"] = task
        data["worker"] = attempt["data"]["seat"]
        data["attempt_seq"] = attempt["seq"]
    # Ground truth only: telling the Seat would change what it chooses to share.
    game.event("relevance.marked", **data)
