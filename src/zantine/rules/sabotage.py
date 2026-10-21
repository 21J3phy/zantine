"""Sabotage: Impostors' Fake Tasks, Visual Tasks, and damage to real work.

- **Fake Tasks.** At setup every Impostor is given as many solo steps as a
  Crewmate, drawn the same way (``Task.fake``). They are briefed, worked and
  graded exactly like real steps, so they occupy the Round and look the same in
  room snapshots, but never count toward Productivity or the crew win. An
  ejected Impostor's unfinished Fake Tasks are handed on like anyone's (and stay
  fake), so reassignment never reveals the ejected player's role.
- **Visual Tasks.** A seeded share of all solo steps (``visual_task_chance``),
  fake ones included, is marked Visual (``Task.visual``, shown in the brief).
  When a Crewmate completes one, every other living player in its room is told
  (``tasks.visual_done``): an alibi. An Impostor's Fake Task never produces one,
  even when answered correctly, so faking a Visual Task in company can expose it.
- **Shared Tasks.** Installing this module lets Impostors hold Shared Tasks
  (``GameModule.shares_tasks_with_impostors``): relay carriers, split-key holders
  and audit assignees are drawn from every Seat.
- **Sabotage** on every Task kind, each recorded as a ``sabotage.<kind>`` event:
  ``sabotage.jam`` (a solo step's station garbles the next answer),
  ``sabotage.tamper`` (a relay part, via ``rules.relay.tamper``),
  ``sabotage.false_spec`` (a wrong audit spec half), and, recorded from ordinary
  actions and speech of Impostors, ``false_verdict`` (an audit verdict that
  contradicts the truth, e.g. approving a bad reading), ``wrong_code`` (a wrong
  split-key entry) and ``false_fragment`` (a holder stating a wrong fragment).

Sabotage is never shown to anyone as such: the actor gets only a receipt that
does not say whether it took effect, and other players learn of it only through
the existing channels (audits, checksums, Visual Tasks, their own grades and
blocked steps). The rules implemented here are documented in ``docs/rules.md``;
keep them in sync.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from zantine.rules import relay as relay_rules
from zantine.rules import splitkey as splitkey_rules
from zantine.rules.actions import Action
from zantine.rules.audit import NAME as AUDIT
from zantine.rules.audit import CheckAudit, ShareSpec, audit_board, is_audit
from zantine.rules.registry import GameModule, RoundContext, Rules
from zantine.rules.tasks import GRADED, TaskBoard, WorkOnTask, board
from zantine.rules.tasks import NAME as TASKS
from zantine.tasks import Task, TaskGraph, task_kind
from zantine.tasks.audit import APPROVE, AUDITOR, SPEC_HOLDER, normalize_verdict
from zantine.tasks.base import change_one
from zantine.tasks.graph import MAX_CHAIN, task_ids
from zantine.tasks.splitkey import normalize_code

if TYPE_CHECKING:
    from zantine.engine import Game
    from zantine.state import GameState, PlayerState

NAME = "sabotage"
# Sabotage kinds, in the order the result lists them.
KINDS = ("jam", "tamper", "false_spec", "false_verdict", "wrong_code", "false_fragment")
# A split-key fragment claim is scored as ground truth only when the *whole
# message* is canonical: one or more statements "T47 part 2 is 315" (optionally
# prefixed by "my", with ":" or "=" allowed for "is"), separated or ended by
# ".", ";", "!" or line breaks, and nothing else. Any other text in the message
# (quotations, questions, denials, hedges, attributions, other remarks) leaves
# the whole message to the Claim Audit, so no claim is lifted out of its context.
_FRAGMENT_CLAIM = re.compile(
    r"(?:my\s+)?(T\d\d)\s+part\s+(\d)(?:\s+is\s+|\s*[:=]\s*)(\d{3})", re.IGNORECASE
)
_SEPARATORS = re.compile(r"[.;!\n]+")


def fragment_claims(text: str) -> list[tuple[str, int, str]]:
    """(Task id, part, digits) per claim if ``text`` is entirely canonical fragment claims."""
    clauses = [c.strip() for c in _SEPARATORS.split(text) if c.strip()]
    matches = [_FRAGMENT_CLAIM.fullmatch(c) for c in clauses]
    if not matches or any(m is None for m in matches):
        return []
    return [(m[1].upper(), int(m[2]), m[3]) for m in matches if m is not None]


@dataclass
class SabotageLog:
    """Module state, kept in ``GameState.ext["sabotage"]``."""

    # Armed jams: solo step id -> the Impostor who jammed its station.
    jams: dict[str, int] = field(default_factory=dict)
    # Every Sabotage, as recorded in its ``sabotage.<kind>`` event.
    records: list[dict[str, Any]] = field(default_factory=list)
    # Every Round spent working a Fake Task (the ``sabotage.fake_task`` event data).
    fake_work: list[dict[str, Any]] = field(default_factory=list)
    # Real Visual Task completions others saw (``tasks.visual_done`` event data).
    visual_done: list[dict[str, Any]] = field(default_factory=list)


def log(state: GameState) -> SabotageLog:
    return state.ext[NAME]


def _impostor_only(actor: PlayerState) -> str | None:
    # Checked first, so a Crewmate learns nothing about Task ids from the reason.
    return None if actor.is_impostor else "only Impostors can sabotage"


def _graph(state: GameState) -> TaskGraph | None:
    b: TaskBoard | None = state.ext.get(TASKS)
    return b.graph if b is not None else None


def _task_param(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"task": {"type": "string", "description": description}},
        "required": ["task"],
        "additionalProperties": False,
    }


# Actions --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Jam(Action):
    """Impostors only: tamper with a solo step's station in your room. The next answer
    anyone submits for that step is subtly garbled, so it fails. Nobody is told, you
    included, whether it took effect (it does nothing if the step is done or not here)."""

    kind: ClassVar[str] = "sabotage.jam"
    task: str = ""
    tool_parameters: ClassVar[dict[str, Any]] = _task_param("ID of a solo step Task.")

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        if reason := _impostor_only(actor):
            return reason
        graph = _graph(state)
        if graph is None or not isinstance(self.task, str) or self.task not in graph:
            return f"no such Task {self.task!r}"
        kind = task_kind(graph.get(self.task).kind)
        if kind.seats != 1 or WorkOnTask.kind not in kind.work_actions:
            return f"{self.task} is not a solo step"
        return None


@dataclass(frozen=True)
class Tamper(Action):
    """Impostors only: tamper with a chain relay's part that you hold or that is in your
    room (held by someone there, or lying there) at the start of the Round. A tampered
    part looks and travels like an intact one but fails on delivery. Nobody is told, you
    included, whether it took effect."""

    kind: ClassVar[str] = "sabotage.tamper"
    task: str = ""
    tool_parameters: ClassVar[dict[str, Any]] = _task_param("ID of a chain relay Task.")

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        if reason := _impostor_only(actor):
            return reason
        if not isinstance(self.task, str) or self.task not in relay_rules.parts(state):
            return f"no such relay {self.task!r}"
        return None


@dataclass(frozen=True)
class FalseSpec(Action):
    """Impostors only, as an audit's spec holder: show the auditor in your room a wrong
    half of the spec (one item changed). It arrives exactly like audit.share."""

    kind: ClassVar[str] = "sabotage.false_spec"
    task: str = ""
    tool_parameters: ClassVar[dict[str, Any]] = _task_param("ID of an audit you hold the spec for.")

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        if reason := _impostor_only(actor):
            return reason
        # The same visible conditions as an honest share.
        return ShareSpec(task=self.task).validate(state, actor)


# The module -------------------------------------------------------------------------------


class SabotageModule(GameModule):
    """Install right after ``TasksModule``: Fake Tasks are placed before the other
    Task modules balance their loads, and Meeting fragment claims are read before
    ``SplitKeyModule`` updates its holders."""

    name = NAME
    shares_tasks_with_impostors = True

    def install(self, rules: Rules) -> None:
        for cls in (Jam, Tamper, FalseSpec):
            rules.register_action(cls)
        # After tasks.work (400): Fake Task work, Visual Tasks, new jams.
        rules.add_resolver(405, "sabotage.work", resolve_work)
        # After Room Chat (410): fragments stated this Round.
        rules.add_resolver(415, "sabotage.chat", resolve_chat)
        # Before relays (450): a part tampered this Round fails a delivery this Round.
        rules.add_resolver(440, "sabotage.tamper", resolve_tamper)
        # Before split-key (460): entries judged against the start-of-Round code.
        rules.add_resolver(455, "sabotage.splitkey", resolve_entries)
        # Before audits (475): verdicts judged against the start-of-Round reading.
        rules.add_resolver(474, "sabotage.verdicts", resolve_verdicts)
        # After audits (475): a false half, like a true one, counts from next Round.
        rules.add_resolver(476, "sabotage.false_spec", resolve_false_specs)

    def place_tasks(self, game: Game, graph: TaskGraph) -> None:
        """Called by the Tasks module at setup: Fake Tasks, then Visual Task marks."""
        _place_fake_tasks(game, graph)
        rng = game.rng.fork(f"{NAME}/visual")
        chance = game.config.visual_task_chance
        for task in graph:
            if _solo(task) and rng.random() < chance:
                task.visual = True

    def on_setup(self, game: Game) -> None:
        game.state.ext[NAME] = SabotageLog()
        graph = _graph(game.state)
        if graph is None:
            return
        game.event(
            "sabotage.setup",
            fake_tasks=[t.id for t in graph if t.fake],
            visual_tasks=[t.id for t in graph if t.visual],
        )

    def on_meeting_end(self, game: Game, meeting: int) -> None:
        """Record false fragments Impostor holders stated in this Meeting."""
        for st in game.transcript.events("statement"):
            data = st["data"]
            if data["meeting"] == meeting:
                # ``Key.holders`` are still the holders during this Meeting.
                _fragment_claims(game, data["speaker"], data["text"], "meeting", holders=True)

    def submitted_answer(self, game: Game, seat: int, task: Task, answer: Any) -> Any:
        """A jammed station garbles the next answer submitted for its step."""
        sl: SabotageLog | None = game.state.ext.get(NAME)
        if sl is None or task.id not in sl.jams:
            return answer
        jammer = sl.jams.pop(task.id)
        rng = game.rng.fork(f"{NAME}/jam/{task.id}/{game.state.round}")
        garbled = task_kind(task.kind).garble(answer, rng)
        game.event(
            "sabotage.jam_triggered",
            task=task.id,
            seat=seat,
            jammer=jammer,
            answer=answer,
            garbled=garbled,
        )
        return garbled

    def result_extra(self, game: Game) -> dict[str, Any] | None:
        sl: SabotageLog | None = game.state.ext.get(NAME)
        graph = _graph(game.state)
        if sl is None or graph is None:
            return None
        effective = [r for r in sl.records if r["effective"]]
        impostors = [p.seat for p in game.state.players if p.is_impostor]

        def counts(records: list[dict[str, Any]]) -> dict[str, int]:
            return {k: sum(r["kind"] == k for r in records) for k in KINDS}

        return {
            "total": len(effective),
            "attempts": len(sl.records),
            "counts": counts(effective),
            "fake_tasks": {
                "assigned": sum(t.fake for t in graph),
                "worked": len(sl.fake_work),
                "done": sum(t.fake and t.done for t in graph),
            },
            "visual_tasks": sum(t.visual for t in graph),
            "visual_done": len(sl.visual_done),
            "seats": [
                {
                    "seat": seat,
                    "total": sum(r["seat"] == seat for r in effective),
                    "counts": counts([r for r in effective if r["seat"] == seat]),
                    "fake_task_rounds": sum(w["seat"] == seat for w in sl.fake_work),
                }
                for seat in impostors
            ],
            "records": list(sl.records),
        }


# Setup -----------------------------------------------------------------------------------


def _solo(task: Task) -> bool:
    kind = task_kind(task.kind)
    return kind.seats == 1 and WorkOnTask.kind in kind.work_actions


def _place_fake_tasks(game: Game, graph: TaskGraph) -> None:
    """``tasks_per_crewmate`` Fake Tasks per Impostor, drawn like real solo steps.

    Same kinds and rooms, and, with ``task_dependency_chance``, one real step
    held by another player as a prerequisite (keeping chains within
    ``MAX_CHAIN``). No Task ever requires a Fake Task, so Fake Tasks can never
    block the crew.
    """
    state, cfg = game.state, game.config
    impostors = [p.seat for p in state.players if p.is_impostor]
    if not impostors or cfg.tasks_per_crewmate <= 0:
        return
    rng = game.rng.fork(NAME)
    rooms = [r for r in state.map.rooms if r != state.map.meeting_room] or list(state.map.rooms)
    depth: dict[str, int] = {}
    for t in graph:
        depth[t.id] = 1 + max((depth[r] for r in t.requires), default=-1)
    slots = [seat for seat in impostors for _ in range(cfg.tasks_per_crewmate)]
    rng.shuffle(slots)
    ids = task_ids(rng.fork("ids"), len(slots), exclude=(t.id for t in graph))
    for task_id, seat in zip(ids, slots, strict=True):
        kind = task_kind(rng.choice(cfg.task_kinds))
        content = kind.generate(rng.fork(f"content/{task_id}"))
        candidates = [
            t.id
            for t in graph
            if _solo(t) and not t.fake and seat not in t.assignees and depth[t.id] < MAX_CHAIN - 1
        ]
        requires: tuple[str, ...] = ()
        if candidates and rng.random() < cfg.task_dependency_chance:
            requires = (rng.choice(candidates),)
        depth[task_id] = 1 + max((depth[r] for r in requires), default=-1)
        graph.add(Task(task_id, rng.choice(rooms), [seat], content, requires, fake=True))


# Resolution ------------------------------------------------------------------------------


def _record(game: Game, kind: str, seat: int, effective: bool = True, **data: Any) -> None:
    record = {"kind": kind, "round": game.state.round, "seat": seat, "effective": effective, **data}
    log(game.state).records.append(record)
    game.event(f"{NAME}.{kind}", seat=seat, effective=effective, **data)


def _receipt(game: Game, seat: int, action: Action, task: str) -> None:
    """The actor's only feedback: what it did, never whether it worked."""
    game.observe(seat, f"{NAME}.attempted", action=action.kind, task=task)


def resolve_work(game: Game, rc: RoundContext) -> None:
    state, sl = game.state, log(game.state)
    b: TaskBoard | None = state.ext.get(TASKS)
    if b is None:
        return
    graded = {(seat, task): correct for seat, task, _, correct in rc.scratch.get(GRADED, [])}
    for seat, action in rc.acting(WorkOnTask):
        task = b.graph.get(action.task)
        if task.fake:
            work = {
                "seat": seat,
                "task": task.id,
                "room": task.room,
                "visual": task.visual,
                "graded": (seat, task.id) in graded,
                "correct": graded.get((seat, task.id), False),
            }
            sl.fake_work.append({"round": rc.round, **work})
            game.event(f"{NAME}.fake_task", **work)
    # Visual Tasks: a real completion is seen by everyone else in the room.
    for seat, task_id, _, correct in rc.scratch.get(GRADED, []):
        task = b.graph.get(task_id)
        # Only a Crewmate's work shows: an Impostor's Fake Task never does. (A
        # Crewmate who inherited an ejected Impostor's Fake Task really does it.)
        if not (correct and task.visual and task.done_round == rc.round):
            continue
        if state.player(seat).is_impostor:
            continue
        room = state.player(seat).room
        witnesses = [p.seat for p in state.living_in(room) if p.seat != seat]
        done = {"seat": seat, "task": task.id, "title": task.content.title, "room": room}
        sl.visual_done.append({"round": rc.round, **done, "witnesses": witnesses})
        game.event("tasks.visual_done", **done, witnesses=witnesses)
        for w in witnesses:
            game.observe(w, "tasks.visual_done", **done)
    # Jams armed this Round garble the next submission, from the next Round on.
    for seat, action in rc.acting(Jam):
        task = b.graph.get(action.task)
        effective = not task.fake and not task.done and task.room == rc.start_rooms[seat]
        if effective:
            sl.jams[task.id] = seat
        _record(game, "jam", seat, effective, task=task.id, room=rc.start_rooms[seat])
        _receipt(game, seat, action, task.id)


def resolve_tamper(game: Game, rc: RoundContext) -> None:
    state = game.state
    for seat, action in rc.acting(Tamper):
        p = relay_rules.part(state, action.task)
        # Where the part was at the start of the Round (its holder may have moved).
        where = rc.start_rooms[p.holder] if p.holder is not None else p.location
        effective = not p.delivered and (p.holder == seat or where == rc.start_rooms[seat])
        if effective:
            relay_rules.tamper(game, p.task, seat)
        _record(
            game,
            "tamper",
            seat,
            effective,
            task=p.task,
            room=rc.start_rooms[seat],
            holding=p.holder == seat,
        )
        _receipt(game, seat, action, p.task)


def resolve_entries(game: Game, rc: RoundContext) -> None:
    state = game.state
    keys = splitkey_rules.keys(state)
    if not keys:
        return
    b = board(state)
    done_before = rc.scratch.get("tasks.done_before", set())
    entries = [
        (seat, action, b.graph.get(action.task))
        for seat, action in rc.acting(splitkey_rules.EnterCode)
        if all(r in done_before for r in b.graph.get(action.task).requires)
    ]
    # A simultaneous correct entry completes the Task, so a wrong one then fails nothing.
    opened = {
        task.id
        for _, action, task in entries
        if task_kind(task.kind).grade(keys[task.id].content, action.code)
    }
    for seat, action, task in entries:
        if not state.player(seat).is_impostor:
            continue
        k = keys[task.id]
        if not task_kind(task.kind).grade(k.content, action.code):
            _record(
                game,
                "wrong_code",
                seat,
                task.id not in opened,
                task=task.id,
                attempt=k.attempt,
                code=normalize_code(action.code),
                expected=k.content.answer,
            )


def resolve_chat(game: Game, rc: RoundContext) -> None:
    for seat in sorted(rc.speech):
        if seat in rc.killed:
            continue
        # What Room Chat delivered (cut to the Seat's Talk Budget).
        text = rc.speech[seat][: game.config.room_chat_budget(seat)]
        if text.strip():
            _fragment_claims(game, seat, text, "chat", holders=False)


def _fragment_claims(game: Game, seat: int, text: str, channel: str, *, holders: bool) -> None:
    """Record each fragment an Impostor holder states for its own part, if wrong.

    ``holders`` reads the holders as of the last Meeting (``Key.holders``),
    else the current assignees.
    """
    state = game.state
    keys = splitkey_rules.keys(state)
    if not keys or not state.player(seat).is_impostor:
        return
    graph = board(state).graph
    for task_id, part, digits in fragment_claims(text):
        k = keys.get(task_id)
        if k is None or graph.get(task_id).done:
            continue
        seats = k.holders if holders else graph.get(task_id).assignees
        if seat not in seats or part != seats.index(seat) + 1:
            continue
        true = k.content.inputs["fragments"][part - 1]
        # One record per lie: repeating the same wrong digits for the same code is
        # the same Sabotage.
        told = any(
            r["kind"] == "false_fragment"
            and (r["seat"], r["task"], r["attempt"], r["stated"])
            == (seat, task_id, k.attempt, digits)
            for r in log(state).records
        )
        if digits != true and not told:
            _record(
                game,
                "false_fragment",
                seat,
                task=task_id,
                attempt=k.attempt,
                part=int(part),
                stated=digits,
                fragment=true,
                channel=channel,
            )


def resolve_verdicts(game: Game, rc: RoundContext) -> None:
    state = game.state
    if AUDIT not in state.ext:
        return
    ab = audit_board(state)
    for seat, action in rc.acting(CheckAudit):
        if not state.player(seat).is_impostor:
            continue
        reading = ab.shown[(action.task, seat)]
        verdict = normalize_verdict(action.verdict)
        if (verdict == APPROVE) != reading.correct:
            _record(
                game,
                "false_verdict",
                seat,
                task=action.task,
                target=reading.task,
                submission=reading.n,
                submitter=reading.seat,
                verdict=verdict,
                outcome="pass" if reading.correct else "fail",
            )


def resolve_false_specs(game: Game, rc: RoundContext) -> None:
    state = game.state
    graph = _graph(state)
    if graph is None or AUDIT not in state.ext:
        return
    ab = audit_board(state)
    for seat, action in rc.acting(FalseSpec):
        task = graph.get(action.task)
        auditor = task.assignees[AUDITOR]
        if auditor in rc.killed or task.done or not is_audit(task):
            continue
        genuine = dict(task.content.inputs["halves"][SPEC_HOLDER])
        rng = game.rng.fork(f"{NAME}/false_spec/{task.id}/{rc.round}")
        pos = rng.choice(sorted(genuine, key=int))
        half = {**genuine, pos: change_one(genuine[pos], rng)}
        ab.received[(task.id, auditor)] = (seat, half)
        ab.shared.add((task.id, seat, auditor))
        # The audit records it as the share it is; the auditor and the holder are
        # told exactly what an honest share tells them.
        game.event("audit.shared", task=task.id, seat=seat, auditor=auditor, half=half)
        _record(game, "false_spec", seat, task=task.id, auditor=auditor, half=half, genuine=genuine)
        game.observe(auditor, "audit.spec_shared", task=task.id, from_seat=seat, half=half)
        game.observe(seat, "audit.shared", task=task.id, auditor=auditor)
