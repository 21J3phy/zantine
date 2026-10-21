"""The game record, rebuilt from transcript records for the Claim Audit.

Reads ``header``, ``event`` and ``observation`` records only (never engine
internals). Two views of the same game:

- **Ground truth** (from ``event`` records): where every living player was at
  the two boundaries of each Round, every kill, every Task completion (solo
  Tasks, chain relays and Audits, each from its own module's events), and the
  Meetings with their statements.
- **A Seat's own record**: the observations it was given plus the actions it
  submitted itself (a Seat always knows what it did). A **Lie** must be proved
  from this view alone.

Boundaries of Round *r*: ``start`` is where a player stood when choosing its
action (after any Meeting in Round *r* - 1 sent everyone back to the meeting
room); ``end`` is where it stood after movement, before any Meeting that Round.
A player's start view of Round *r* is its last ``room_snapshot`` in Round
*r* - 1; its end view is its first ``room_snapshot`` in Round *r*.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

# Task kinds whose completion is reconstructed from their own module's events.
RELAY_KIND = "chain_relay"
AUDIT_KIND = "audit"
SPLITKEY_KIND = "split_key"

Boundary = Literal["start", "end"]
BOUNDARIES: tuple[Boundary, Boundary] = ("start", "end")


@dataclass(frozen=True)
class Kill:
    seq: int
    round: int
    room: str
    victim: int
    killers: tuple[int, ...]


@dataclass(frozen=True)
class TaskDone:
    """One Seat's credit for a completed Task.

    - Solo Tasks: a correct ``tasks.graded``, in the Task's room.
    - Chain relays: the correct delivery, credited to the deliverer (delivery room,
      delivery Round) and to each carrier who handed the part off (handoff room and
      Round); ``seq`` is the delivery's for all of them.
    - Audits: a correct ``audit.checked``, credited to the auditor (the Audit's room)
      and to the spec holder who shared the spec (room unknown: None).
    - Split-key Shared Tasks: the correct entry, credited to the enterer (the Task's
      room) and, at ``splitkey.completed``, to every other holder who read a fragment
      of the successful attempt (as in the Task stats; where they stood: None). A
      read of an earlier, reset attempt earns nothing.
    """

    seq: int
    round: int
    seat: int
    task: str
    room: str | None


@dataclass(frozen=True)
class StatementRecord:
    seq: int
    round: int
    meeting: int
    pass_index: int
    order: int
    speaker: int
    text: str


@dataclass(frozen=True)
class MeetingRecord:
    meeting: int
    round: int
    # Round of the previous Meeting (0 if none): "since the last Meeting" is
    # Rounds ``previous_round + 1 .. round``.
    previous_round: int
    seq: int

    @property
    def window(self) -> range:
        return range(self.previous_round + 1, self.round + 1)


@dataclass
class GameRecord:
    seed: int | None
    names: dict[int, str]
    roles: dict[int, str]
    rooms: tuple[str, ...]
    meeting_room: str | None
    task_rooms: dict[str, str]
    # Every Task's kind (``tasks.generated`` and Audits placed later), by id.
    task_kinds: dict[str, str]
    # positions[(round, boundary)][seat] = room; only living players are listed.
    positions: dict[tuple[int, Boundary], dict[int, str]]
    kills: list[Kill]
    tasks_done: list[TaskDone]
    meetings: list[MeetingRecord]
    statements: list[StatementRecord]
    # Every observation record, per seat, in seq order.
    observations: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    # Every valid action a seat submitted (``action`` events), per seat.
    own_actions: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    header: dict[str, Any] = field(default_factory=dict)

    # Ground truth -------------------------------------------------------------

    def room_at(self, seat: int, round: int, boundary: Boundary) -> str | None:
        """Where a living player was; None if dead, ejected or not yet seen."""
        return self.positions.get((round, boundary), {}).get(seat)

    def meeting(self, number: int) -> MeetingRecord:
        return next(m for m in self.meetings if m.meeting == number)

    # A Seat's own record ------------------------------------------------------

    def snapshot(self, seat: int, round: int, boundary: Boundary) -> dict[str, Any] | None:
        """The ``room_snapshot`` observation giving ``seat``'s view at a boundary."""
        snaps = [o for o in self.observations.get(seat, []) if o["type"] == "room_snapshot"]
        if boundary == "start":
            # Every living Seat gets a snapshot every Round, so a gap means it was dead.
            before = [o for o in snaps if o["round"] == round - 1]
            return before[-1] if before else None
        return next((o for o in snaps if o["round"] == round), None)

    def observed(self, seat: int, type: str, before_seq: int) -> list[dict[str, Any]]:
        return [
            o
            for o in self.observations.get(seat, [])
            if o["type"] == type and o["seq"] < before_seq
        ]

    def actions(self, seat: int, kind: str, before_seq: int) -> list[dict[str, Any]]:
        return [
            a
            for a in self.own_actions.get(seat, [])
            if a["data"]["value"].get("kind") == kind and a["seq"] < before_seq
        ]

    def role_record(self, seat: int) -> dict[str, Any] | None:
        """The seat's own ``role_assigned`` observation."""
        return next(
            (o for o in self.observations.get(seat, []) if o["type"] == "role_assigned"), None
        )


def build_record(records: Iterable[dict[str, Any]]) -> GameRecord:
    records = sorted(
        (r for r in records if isinstance(r, dict) and "kind" in r), key=lambda r: r.get("seq", 0)
    )
    header = next((r for r in records if r["kind"] == "header"), {})
    seats = header.get("seats", [])
    names = {s["seat"]: s["name"] for s in seats}
    roles = {s["seat"]: s["role"] for s in seats}
    game_map = header.get("map", {})
    meeting_room = game_map.get("meeting_room")
    rec = GameRecord(
        seed=header.get("seed"),
        names=names,
        roles=roles,
        rooms=tuple(game_map.get("rooms", ())),
        meeting_room=meeting_room,
        task_rooms={},
        task_kinds={},
        positions={},
        kills=[],
        tasks_done=[],
        meetings=[],
        statements=[],
        observations={s: [] for s in names},
        own_actions={s: [] for s in names},
        header=header,
    )
    pos: dict[int, str] = {s: meeting_room for s in names} if meeting_room else {}
    relay_rooms: dict[str, dict[str, str]] = {}
    # Relay legs handed off so far: (seat, round, room) per Task.
    handoffs: dict[str, list[tuple[int, int, str]]] = {}
    # (split-key Task, attempt) -> Seats that read a fragment of that attempt's code.
    key_reads: dict[tuple[str, int], list[int]] = {}
    # split-key Task -> passing grades this Round, (seq, seat): only the completing
    # enterer (named by splitkey.completed) earns the credit, as in the Task stats.
    key_grades: dict[str, list[tuple[int, int]]] = {}

    def add_task(task: dict[str, Any]) -> None:
        rec.task_rooms[task["id"]] = task["room"]
        rec.task_kinds[task["id"]] = task["kind"]
        if task["kind"] == RELAY_KIND:
            relay_rooms[task["id"]] = dict(task["inputs"]["rooms"])

    current = 0
    ended: set[int] = set()

    def close_round(r: int) -> None:
        if r >= 1 and r not in ended:
            ended.add(r)
            rec.positions[(r, "end")] = dict(pos)

    for r in records:
        if r["kind"] == "observation":
            rec.observations.setdefault(r["seat"], []).append(r)
            continue
        if r["kind"] != "event":
            continue
        rnd, type, data = r["round"], r["type"], r["data"]
        if rnd > current:
            close_round(current)
            current = rnd
            rec.positions[(rnd, "start")] = dict(pos)
        match type:
            case "action" if data.get("valid") and isinstance(data.get("value"), dict):
                rec.own_actions.setdefault(data["seat"], []).append(r)
            case "kill":
                rec.kills.append(
                    Kill(r["seq"], rnd, data["room"], data["victim"], tuple(data["killers"]))
                )
                pos.pop(data["victim"], None)
            case "move" if data["seat"] in pos:
                pos[data["seat"]] = data["to_room"]
            case "tasks.generated":
                for task in data["tasks"]:
                    add_task(task)
            case "audit.placed":
                add_task(data["task"])
            case "relay.handed_off":
                handoffs.setdefault(data["task"], []).append((data["seat"], rnd, data["room"]))
            case "splitkey.read":
                readers = key_reads.setdefault((data["task"], data["attempt"]), [])
                if data["seat"] not in readers:
                    readers.append(data["seat"])
            case "splitkey.completed":
                task, enterer = data["task"], data["seat"]
                graded = [seq for seq, seat in key_grades.pop(task, []) if seat == enterer]
                rec.tasks_done.append(
                    TaskDone(graded[0], rnd, enterer, task, rec.task_rooms.get(task))
                )
                for seat in key_reads.get((task, data["attempt"]), []):
                    if seat != enterer:
                        rec.tasks_done.append(TaskDone(r["seq"], rnd, seat, task, None))
            case "tasks.graded" if (
                data.get("correct") and rec.task_kinds.get(data["task"]) == SPLITKEY_KIND
            ):
                key_grades.setdefault(data["task"], []).append((r["seq"], data["seat"]))
            case "tasks.graded" if data.get("correct"):
                task = data["task"]
                if task in relay_rooms:
                    # Delivered in the delivery room; every carrier who finished a
                    # leg shares the completion (as in the Task stats).
                    rec.tasks_done.append(
                        TaskDone(r["seq"], rnd, data["seat"], task, relay_rooms[task]["delivery"])
                    )
                    for seat, leg_round, room in handoffs.get(task, []):
                        if seat != data["seat"]:
                            rec.tasks_done.append(TaskDone(r["seq"], leg_round, seat, task, room))
                else:
                    rec.tasks_done.append(
                        TaskDone(r["seq"], rnd, data["seat"], task, rec.task_rooms.get(task))
                    )
            case "audit.checked" if data.get("correct"):
                # A correct check completes the Audit (in its room) for the auditor
                # and the spec holder who shared the spec, wherever it stood then.
                task = data["task"]
                auditor = data["auditor"]
                rec.tasks_done.append(
                    TaskDone(r["seq"], rnd, auditor, task, rec.task_rooms.get(task))
                )
                if data.get("spec_from") is not None and data["spec_from"] != auditor:
                    rec.tasks_done.append(TaskDone(r["seq"], rnd, data["spec_from"], task, None))
            case "meeting_started":
                close_round(rnd)
                previous = rec.meetings[-1].round if rec.meetings else 0
                rec.meetings.append(MeetingRecord(data["meeting"], rnd, previous, r["seq"]))
            case "statement":
                rec.statements.append(
                    StatementRecord(
                        r["seq"],
                        rnd,
                        data["meeting"],
                        data["pass_index"],
                        data["order"],
                        data["speaker"],
                        data["text"],
                    )
                )
            case "ejection":
                pos.pop(data["seat"], None)
            case "meeting_ended" if meeting_room:
                for seat in pos:
                    pos[seat] = meeting_room
    close_round(current)
    return rec
