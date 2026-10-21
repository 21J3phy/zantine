"""Transcript → replay view model.

Reads transcript records only (never engine internals) and turns them into a
plain-JSON structure the HTML viewer steps through Round by Round. Built to
survive transcripts it doesn't fully understand: unknown event or observation
types become generic log lines, missing headers and malformed records are
tolerated, and anything that looks like speech (a speaker plus text) is shown
as speech, so later modules (Room Chat, Tasks, Crisis, ...) replay without
changes here.

Roles are always in the view model; the viewer hides them by default. Every log
item has a full ``text`` and a ``public`` text (``None`` = hide the item) for
when roles are hidden, so kills don't name the killer and the Impostors'
private observations stay out of sight.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Data keys whose int (or list of int) values are seat numbers.
SEAT_KEYS = frozenset(
    {
        "seat",
        "seats",
        "speaker",
        "sender",
        "caller",
        "victim",
        "victims",
        "body_victim",
        "target",
        "killer",
        "killers",
        "reporter",
        "voter",
        "partner",
        "players",
        "dead",
        "requested_by",
        "fellow_impostors",
        "survivors",
        "killed",
        "ejected",
        "members",
        "witnesses",
        "triggered_by",
        "fixers",
    }
)
# Data keys (at any depth) that reveal a role, directly or by naming an Impostor.
SPOILER_KEYS = frozenset(
    {"role", "roles", "killer", "killers", "fellow_impostors", "impostors", "triggered_by"}
)
# Observation types only Impostors, victims or witnesses get; they reveal roles.
SPOILER_OBSERVATIONS = frozenset({"role_assigned", "killed", "kill_witnessed"})
# Data keys (at any depth) left out of the public text: whether a Task is a Fake
# Task names its holder as an Impostor.
REDACTED_KEYS = frozenset({"fake"})
# Record types hidden entirely with roles hidden: Sabotage records name the
# saboteur or a Fake Task (whose holder is an Impostor) by seat or Task id.
HIDDEN_PREFIXES = ("sabotage.",)
# Words in an action kind or event type that suggest only an Impostor could do it.
IMPOSTOR_HINTS = ("kill", "sabotage", "crisis", "vent", "impostor", "tamper")
# Keys a generic record may use for who is speaking and what they said.
SPEAKER_KEYS = ("speaker", "sender", "seat", "from_seat")
TEXT_KEYS = ("text", "message", "content")
# Engine bookkeeping hidden unless the viewer asks for details.
DETAIL_EVENTS = frozenset(
    {"meeting_requested", "speaking_order", "statement", "vote", "bodies_cleared", "meeting_ended"}
)

NAMED_COLORS = {
    "red": "#e5393a",
    "blue": "#1e63d6",
    "green": "#1f9d4a",
    "pink": "#ee6fb4",
    "orange": "#f08c1a",
    "yellow": "#e8c51c",
    "black": "#3d3d46",
    "white": "#e9edf2",
    "purple": "#7b3fc4",
    "brown": "#8a5a2b",
    "cyan": "#2ec4d6",
    "lime": "#8fd93a",
    "maroon": "#7a1f2b",
    "rose": "#f2a7c3",
    "banana": "#f3e7a1",
    "gray": "#8a94a0",
    "grey": "#8a94a0",
    "tan": "#b59f7a",
    "coral": "#f27866",
}
PALETTE = (
    "#e5393a",
    "#1e63d6",
    "#1f9d4a",
    "#ee6fb4",
    "#f08c1a",
    "#e8c51c",
    "#3d3d46",
    "#e9edf2",
    "#7b3fc4",
    "#8a5a2b",
    "#2ec4d6",
    "#8fd93a",
)


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Read a JSONL transcript, skipping blank or unparseable lines."""
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def build_view(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Turn transcript records into the viewer's Round-by-Round view model."""
    return _Builder([r for r in records if isinstance(r, dict)]).build()


class _Builder:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.header = next((r for r in records if r.get("kind") == "header"), {})
        self.result = next((r for r in records if r.get("kind") == "result"), None)
        self.seats = self._seats()
        self.names = {s["seat"]: s["name"] for s in self.seats}
        rooms, edges, meeting_room, coords = self._map()
        self.rooms, self.edges, self.meeting_room, self.coords = rooms, edges, meeting_room, coords
        # Live state while walking the records.
        start = {s["seat"]: meeting_room for s in self.seats} if meeting_room else {}
        self.pos: dict[int, str] = start
        self.status: dict[int, str] = {s["seat"]: "alive" for s in self.seats}
        self.bodies: list[dict[str, Any]] = []
        self.meetings: dict[int, dict[str, Any]] = {}

    # Setup ------------------------------------------------------------------

    def _seats(self) -> list[dict[str, Any]]:
        seats: dict[int, dict[str, Any]] = {}
        for raw in _as_list(self.header.get("seats")):
            if isinstance(raw, dict) and _is_seat(raw.get("seat")):
                seat = raw["seat"]
                seats[seat] = {
                    "seat": seat,
                    "name": str(raw.get("name") or f"Seat {seat}"),
                    "role": raw.get("role") if isinstance(raw.get("role"), str) else None,
                    "controller": _describe_controller(raw.get("controller")),
                }
        # No header (or a partial one): find seats mentioned anywhere.
        for r in self.records:
            for seat in _seats_in(r):
                seats.setdefault(
                    seat, {"seat": seat, "name": f"Seat {seat}", "role": None, "controller": ""}
                )
        for r in self.records:
            data = r.get("data") if isinstance(r.get("data"), dict) else {}
            role = data.get("role")
            if not isinstance(role, str):
                continue
            if r.get("type") == "role_assigned" and _is_seat(r.get("seat")):
                seats[r["seat"]]["role"] = seats[r["seat"]]["role"] or role
            elif r.get("type") == "ejection" and _is_seat(data.get("seat")):
                seats[data["seat"]]["role"] = seats[data["seat"]]["role"] or role
        out = [seats[k] for k in sorted(seats)]
        for s in out:
            s["color"] = NAMED_COLORS.get(s["name"].lower(), PALETTE[s["seat"] % len(PALETTE)])
        return out

    def _map(self) -> tuple[list[str], list[list[str]], str | None, dict[str, list[float]]]:
        m = self.header.get("map") if isinstance(self.header.get("map"), dict) else {}
        rooms = [str(r) for r in _as_list(m.get("rooms")) if isinstance(r, str | int)]
        edges = [
            [str(e[0]), str(e[1])]
            for e in _as_list(m.get("edges"))
            if isinstance(e, list | tuple) and len(e) >= 2
        ]
        meeting_room = m.get("meeting_room") if isinstance(m.get("meeting_room"), str) else None
        # Rooms the map didn't list but the game mentions.
        for r in self.records:
            data = r.get("data") if isinstance(r.get("data"), dict) else {}
            for key in ("room", "to_room", "from_room", "body_room"):
                room = data.get(key)
                if isinstance(room, str) and room not in rooms:
                    rooms.append(room)
        if meeting_room and meeting_room not in rooms:
            rooms.insert(0, meeting_room)
        for a, b in edges:
            for room in (a, b):
                if room not in rooms:
                    rooms.append(room)
        return rooms, edges, meeting_room, _layout(rooms, m)

    # Walk -------------------------------------------------------------------

    def build(self) -> dict[str, Any]:
        # Round 0 is setup: everyone at their starting rooms before any action.
        frames: dict[int, dict[str, Any]] = {0: self._new_frame(0)}
        current = 0
        for r in self.records:
            if r.get("kind") not in ("event", "observation"):
                continue
            rnd = r.get("round")
            rnd = rnd if isinstance(rnd, int) and not isinstance(rnd, bool) else current
            if rnd != current:
                self._close(frames[current])
            current = rnd
            frame = frames.get(rnd) or frames.setdefault(rnd, self._new_frame(rnd))
            if frame.get("_closed"):
                frame.pop("_closed")
            try:
                item = self._apply(r, frame)
            except Exception:  # a malformed record must not break the replay
                item = _generic_item(r, self.names)
            if item is not None:
                frame["items"].append(item)
        self._close(frames[current])
        lo, hi = min(frames), max(frames)
        rounds = []
        last = frames[lo]
        for rnd in range(lo, hi + 1):
            # A Round with no records keeps the previous picture.
            frame = frames.get(rnd) or {
                **last,
                "round": rnd,
                "items": [],
                "moves": [],
                "meetings": [],
            }
            frame.pop("_closed", None)
            frame.pop("_frozen", None)
            rounds.append(frame)
            last = frame
        return {
            "title": self._title(),
            "seed": self.header.get("seed"),
            "schema": self.header.get("schema"),
            "modules": [str(m) for m in _as_list(self.header.get("modules"))],
            "map": {
                "rooms": self.rooms,
                "edges": self.edges,
                "meeting_room": self.meeting_room,
                "layout": self.coords,
            },
            "seats": self.seats,
            "rounds": rounds,
            "meetings": [self.meetings[k] for k in sorted(self.meetings)],
            "result": self._result(),
        }

    def _title(self) -> str:
        seed = self.header.get("seed")
        return f"Zantine replay: seed {seed}" if seed is not None else "Zantine replay"

    def _result(self) -> dict[str, Any] | None:
        if not self.result:
            return None
        winner = self.result.get("winner")
        reason = self.result.get("reason")
        rounds = self.result.get("rounds")
        text = _outcome(winner)
        if reason:
            text += f" by {reason}"
        if rounds is not None:
            text += f" after {rounds} Rounds"
        return {"winner": winner, "reason": reason, "rounds": rounds, "text": text}

    def _new_frame(self, rnd: int) -> dict[str, Any]:
        return {"round": rnd, "items": [], "moves": [], "meetings": []}

    def _freeze_map(self, frame: dict[str, Any]) -> None:
        """Capture the map as it stood after the Round's resolution."""
        if frame.get("_frozen"):
            return
        frame["_frozen"] = True
        frame["positions"] = [
            {"seat": s, "room": room}
            for s, room in sorted(self.pos.items())
            if self.status.get(s) == "alive"
        ]
        frame["bodies"] = [dict(b) for b in self.bodies]

    def _close(self, frame: dict[str, Any]) -> None:
        self._freeze_map(frame)
        frame["status"] = [{"seat": s, "status": st} for s, st in sorted(self.status.items())]
        frame["_closed"] = True

    # Records ----------------------------------------------------------------

    def _apply(self, r: dict[str, Any], frame: dict[str, Any]) -> dict[str, Any] | None:
        data = r.get("data") if isinstance(r.get("data"), dict) else {}
        type_ = str(r.get("type"))
        if r["kind"] == "observation":
            return self._observation(r, data, type_)
        n = self._name
        item = _item(r, type_)
        match type_:
            case "move":
                seat = data["seat"]
                frame["moves"].append(
                    {"seat": seat, "from": data.get("from_room"), "to": data["to_room"]}
                )
                self.pos[seat] = data["to_room"]
                item.update(category="move")
                _text(item, f"{n(seat)} moved {data.get('from_room')} → {data['to_room']}")
            case "kill":
                victim, room = data["victim"], data.get("room")
                self.status[victim] = "killed"
                self.pos.pop(victim, None)
                self.bodies.append({"victim": victim, "room": room})
                killers = " & ".join(n(k) for k in _as_list(data.get("killers")))
                item.update(category="kill")
                _text(item, f"{killers} killed {n(victim)} in {room}")
                item["public"] = f"{n(victim)} was killed in {room}"
            case "action":
                return self._action(item, data)
            case "vote":
                self._meeting(data)["votes"].append(
                    {
                        "seat": data.get("seat"),
                        "value": data.get("value"),
                        "valid": data.get("valid", True),
                        "attempt": data.get("attempt"),
                        "reason": data.get("reason"),
                    }
                )
                target = data.get("value")
                what = "skipped" if target is None else f"voted for {n(target)}"
                item.update(category="vote", detail=data.get("valid", True))
                _text(item, f"{n(data.get('seat'))} {what}")
                if not data.get("valid", True):
                    item.update(category="invalid")
                    _text(item, f"{n(data.get('seat'))}'s vote was invalid: {data.get('reason')}")
            case "meeting_requested":
                item["detail"] = True
                _text(item, f"Meeting requested ({data.get('reason')}) by {n(data.get('caller'))}")
            case "meeting_started":
                self._freeze_map(frame)
                m = self._meeting(data)
                m.update(
                    round=frame["round"],
                    reason=data.get("reason"),
                    caller=data.get("caller"),
                    body_victim=data.get("body_victim"),
                    body_room=data.get("body_room"),
                    requested_by=_as_list(data.get("requested_by")),
                    title=self._meeting_title(data),
                )
                if m["meeting"] not in frame["meetings"]:
                    frame["meetings"].append(m["meeting"])
                item.update(category="meeting")
                _text(item, m["title"])
            case "bodies_cleared":
                victims = set(_as_list(data.get("victims")))
                self.bodies = [b for b in self.bodies if victims and b["victim"] not in victims]
                item["detail"] = True
                _text(item, "Bodies cleared")
            case "speaking_order":
                self._meeting(data)["orders"].append(
                    {"pass_index": data.get("pass_index"), "order": _as_list(data.get("order"))}
                )
                item["detail"] = True
                order = ", ".join(n(s) for s in _as_list(data.get("order")))
                _text(item, f"Pass {data.get('pass_index')} speaking order: {order}")
            case "statement":
                self._meeting(data)["statements"].append(
                    {
                        "pass_index": data.get("pass_index"),
                        "order": data.get("order"),
                        "speaker": data.get("speaker"),
                        "text": str(data.get("text", "")),
                        "truncated": bool(data.get("truncated")),
                    }
                )
                item.update(category="speech", detail=True, speaker=data.get("speaker"))
                _text(item, f"{n(data.get('speaker'))}: {data.get('text', '')}")
            case "vote_tally":
                m = self._meeting(data)
                m["tally"] = {
                    "counts": _as_list(data.get("counts")),
                    "skips": data.get("skips"),
                    "ejected": data.get("ejected"),
                }
                item.update(category="vote")
                _text(item, self._tally_text(data))
            case "ejection":
                seat = data["seat"]
                self.status[seat] = "ejected"
                self.pos.pop(seat, None)
                self._meeting(data)["ejection"] = {"seat": seat, "role": data.get("role")}
                item.update(category="ejection")
                _text(item, f"{n(seat)} was ejected ({data.get('role')})")
                item["public"] = f"{n(seat)} was ejected"
            case "meeting_ended":
                if self.meeting_room:
                    for s, st in self.status.items():
                        if st == "alive":
                            self.pos[s] = self.meeting_room
                item["detail"] = True
                _text(item, f"Meeting {data.get('meeting')} ended; everyone returns")
            case "harness.notes":
                # A Seat's private notepad (agent-loop Harness): never speech, and it
                # may name roles, so only shown with roles revealed and details on.
                item.update(category="notes", detail=True)
                item["text"] = f"{n(data.get('seat'))}'s private notes: {data.get('text', '')}"
                item["public"] = None
            case "containment":
                # Saying so would tell viewers the ejected players were Impostors.
                item.update(category="game")
                item["text"] = "Containment: every Impostor has been ejected; play continues"
                item["public"] = None
            case "game_over":
                item.update(category="game")
                _text(item, f"Game over: {_outcome(data.get('winner'))} ({data.get('reason')})")
            case _:
                return _generic_item(r, self.names)
        return item

    def _action(self, item: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        n = self._name
        value = data.get("value")
        kind = value.get("kind") if isinstance(value, dict) else None
        desc = _describe_action(value, self.names)
        valid = data.get("valid", True)
        if valid:
            item.update(category="action", detail=True)
            _text(item, f"{n(data.get('seat'))}: {desc}")
        else:
            item.update(category="invalid")
            forfeit = " (Round forfeited)" if data.get("attempt") == 2 else ""
            _text(
                item, f"{n(data.get('seat'))} tried {desc}: invalid, {data.get('reason')}{forfeit}"
            )
        if _impostor_hint(kind) or _has_spoiler(value):
            item["public"] = None
        return item

    def _observation(self, r: dict[str, Any], data: dict[str, Any], type_: str) -> dict[str, Any]:
        seat = r.get("seat")
        if (
            _is_seat(seat)
            and self.status.get(seat) == "alive"
            and type_ in ("room_snapshot", "returned_to_meeting_room")
            and isinstance(data.get("room"), str)
        ):
            self.pos[seat] = data["room"]
            for other in _as_list(data.get("players")):
                if _is_seat(other) and self.status.get(other) == "alive":
                    self.pos[other] = data["room"]
        item = _generic_item(r, self.names)
        item["detail"] = True
        item["text"] = f"→ {self._name(seat)}: {item['text']}"
        if type_ in SPOILER_OBSERVATIONS or item["public"] is None:
            item["public"] = None
        else:
            item["public"] = item["text"]
        return item

    def _meeting(self, data: dict[str, Any]) -> dict[str, Any]:
        num = data.get("meeting")
        num = num if isinstance(num, int) else max(self.meetings, default=0)
        return self.meetings.setdefault(
            num,
            {
                "meeting": num,
                "round": None,
                "title": f"Meeting {num}",
                "orders": [],
                "statements": [],
                "votes": [],
                "tally": None,
                "ejection": None,
            },
        )

    def _meeting_title(self, d: dict[str, Any]) -> str:
        n = self._name
        num = d.get("meeting")
        if d.get("reason") == "report":
            return (
                f"Meeting {num}: {n(d.get('caller'))} reported {n(d.get('body_victim'))}'s body"
                f" in {d.get('body_room')}"
            )
        if d.get("reason") == "emergency":
            return f"Meeting {num}: {n(d.get('caller'))} called an emergency Meeting"
        return f"Meeting {num}: called by {n(d.get('caller'))} ({d.get('reason')})"

    def _tally_text(self, d: dict[str, Any]) -> str:
        counts = ", ".join(
            f"{self._name(c.get('seat'))} {c.get('votes')}"
            for c in _as_list(d.get("counts"))
            if isinstance(c, dict)
        )
        votes = f"votes: {counts}; " if counts else ""
        if d.get("ejected") is None:
            return f"Vote: no one ejected ({votes}skips: {d.get('skips')})"
        return f"Vote: {self._name(d['ejected'])} ejected ({votes}skips: {d.get('skips')})"

    def _name(self, seat: Any) -> str:
        if _is_seat(seat):
            return self.names.get(seat, f"Seat {seat}")
        return "nobody" if seat is None else str(seat)


# Helpers ---------------------------------------------------------------------


def _item(r: dict[str, Any], type_: str) -> dict[str, Any]:
    return {
        "seq": r.get("seq"),
        "kind": r.get("kind"),
        "type": type_,
        "seat": r.get("seat") if _is_seat(r.get("seat")) else None,
        "category": "other",
        "detail": False,
        "text": "",
        "public": "",
    }


def _text(item: dict[str, Any], text: str) -> None:
    item["text"] = item["public"] = text


def _generic_item(r: dict[str, Any], names: dict[int, str]) -> dict[str, Any]:
    """Describe any record without knowing its type."""
    type_ = str(r.get("type", r.get("kind", "record")))
    data = r.get("data")
    item = _item(r, type_)
    if not isinstance(data, dict):
        _text(item, f"{type_}: {_fmt(None, data, names)}" if data is not None else type_)
        return item
    speaker = next((data[k] for k in SPEAKER_KEYS if _is_seat(data.get(k))), None)
    text = next((data[k] for k in TEXT_KEYS if isinstance(data.get(k), str)), None)
    if speaker is not None and text is not None:
        where = f" in {data['room']}" if isinstance(data.get("room"), str) else ""
        item.update(category="speech", speaker=speaker)
        _text(item, f"{names.get(speaker, f'Seat {speaker}')}{where} ({type_}): {text}")
    else:
        fields = ", ".join(f"{k}={_fmt(k, v, names)}" for k, v in sorted(data.items()))
        _text(item, f"{type_}: {fields}" if fields else type_)
    hint = _impostor_hint(type_)
    if _redacts(data):
        public = _redacted(data)
        fields = ", ".join(f"{k}={_fmt(k, v, names)}" for k, v in sorted(public.items()))
        item["public"] = f"{type_}: {fields}" if fields else type_
    if type_.startswith(HIDDEN_PREFIXES):
        item["public"] = None
    elif r.get("kind") == "observation" and _has_spoiler(data):
        item["public"] = None  # a private view that gives a role away
    elif hint or _has_spoiler(data):
        # Keep the event but drop who did it.
        hidden = SPOILER_KEYS | SEAT_KEYS if hint else SPOILER_KEYS
        rest = {k: v for k, v in _redacted(data).items() if k not in hidden}
        fields = ", ".join(f"{k}={_fmt(k, v, names)}" for k, v in sorted(rest.items()))
        item["public"] = None if _has_spoiler(rest) else f"{type_}: {fields}" if fields else type_
    return item


def _outcome(winner: Any) -> str:
    return "No winner" if winner == "none" else f"{str(winner).capitalize()} win"


def _impostor_hint(kind: Any) -> bool:
    return isinstance(kind, str) and any(h in kind.lower() for h in IMPOSTOR_HINTS)


def _fmt(key: str | None, value: Any, names: dict[int, str]) -> str:
    if key in SEAT_KEYS:
        if _is_seat(value):
            return names.get(value, f"Seat {value}")
        if isinstance(value, list) and all(_is_seat(v) for v in value):
            return "[" + ", ".join(names.get(v, f"Seat {v}") for v in value) + "]"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


def _describe_action(value: Any, names: dict[int, str]) -> str:
    if not isinstance(value, dict):
        return "an unreadable action" if value is None else f"{value!r}"
    kind = str(value.get("kind", "?"))
    args = {k: v for k, v in sorted(value.items()) if k != "kind"}
    match kind, args:
        case "move", {"to": to}:
            return f"move to {to}"
        case "kill", {"target": target}:
            return f"kill {_fmt('target', target, names)}"
        case "call_meeting", _:
            return "call an emergency Meeting"
    if not args:
        return kind
    return kind + " " + ", ".join(f"{k}={_fmt(k, v, names)}" for k, v in args.items())


def _describe_controller(raw: Any) -> str:
    if isinstance(raw, dict):
        model = raw.get("model") or raw.get("model_id")
        kind = raw.get("kind")
        parts = [str(p) for p in (kind, model) if p]
        return " / ".join(parts) if parts else json.dumps(raw, sort_keys=True)
    return "" if raw is None else str(raw)


def _redacts(value: Any) -> bool:
    if isinstance(value, dict):
        return any(k in REDACTED_KEYS or _redacts(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_redacts(v) for v in value)
    return False


def _redacted(value: Any) -> Any:
    """``value`` without ``REDACTED_KEYS``, at any depth."""
    if isinstance(value, dict):
        return {k: _redacted(v) for k, v in value.items() if k not in REDACTED_KEYS}
    if isinstance(value, list):
        return [_redacted(v) for v in value]
    return value


def _has_spoiler(value: Any) -> bool:
    if isinstance(value, dict):
        return any(k in SPOILER_KEYS or _has_spoiler(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_spoiler(v) for v in value)
    return False


def _seats_in(r: dict[str, Any]) -> list[int]:
    out = [r["seat"]] if _is_seat(r.get("seat")) else []
    data = r.get("data") if isinstance(r.get("data"), dict) else {}
    for key in ("seat", "speaker", "victim", "caller"):
        if _is_seat(data.get(key)):
            out.append(data[key])
    return out


def _is_seat(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


def _layout(rooms: list[str], m: dict[str, Any]) -> dict[str, list[float]]:
    """Room centres in [0, 1]². Uses map-provided coordinates when present,
    otherwise places rooms on a circle in map order."""
    given = next(
        (m[k] for k in ("layout", "coords", "positions") if isinstance(m.get(k), dict)), {}
    )
    pts = {
        room: [float(xy[0]), float(xy[1])]
        for room, xy in given.items()
        if room in rooms
        and isinstance(xy, list | tuple)
        and len(xy) >= 2
        and all(isinstance(v, int | float) for v in xy[:2])
    }
    if pts and len(pts) == len(rooms):
        xs = [p[0] for p in pts.values()]
        ys = [p[1] for p in pts.values()]
        w, h = (max(xs) - min(xs)) or 1.0, (max(ys) - min(ys)) or 1.0
        return {
            room: [round((x - min(xs)) / w, 4), round((y - min(ys)) / h, 4)]
            for room, (x, y) in pts.items()
        }
    count = max(len(rooms), 1)
    return {
        room: [
            round(0.5 + 0.5 * math.cos(2 * math.pi * i / count - math.pi / 2), 4),
            round(0.5 + 0.5 * math.sin(2 * math.pi * i / count - math.pi / 2), 4),
        ]
        for i, room in enumerate(rooms)
    }
