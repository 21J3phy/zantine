"""Audit Tasks: a Crewmate checks another player's submitted work against a spec.

An audit is a two-player Task (``zantine.tasks.audit``) placed on another Task,
its *target*. The target's answer key is the spec, split between the auditor and
the spec holder; neither may hold the target. Whenever work on the target is
submitted, the auditor is privately shown the reading. The spec holder shares
its half with ``audit.share`` while in the auditor's room; the auditor then
answers ``audit.check`` with a verdict on the latest reading it was shown. The
verdict is graded against the ground truth of that submission and only the
auditor learns the outcome. No one may audit their own submission.

Everything an auditor is told is an observation, and every share and check is a
ground-truth event. ``place_audit`` can put an audit on any auditable Task with
a submission log, so later rules (Sabotage, Verification Protocols) can add
audits during play and read verdicts from ``AuditBoard.checks``. Audits placed
in one *group* (e.g. the mandatory checks of one Task, ``rules/verification.py``)
must each have a different auditor, and a *required* audit stays open until a
check passes (approves correct work), so a correct rejection does not close it.
A required audit's spec holder is done once its half is delivered: it is never
told whether, or when, the auditor's check passed (no ``audit.closed``, no later
partner changes, the same answer to a repeated share), and a replacement auditor
inherits the delivered half.
Seeded optional audits are placed only under the ``optional`` Verification
Protocol, and no audit at all under ``none``.

The rules implemented here are documented in ``docs/rules.md``; keep them in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from zantine.rng import GameRng
from zantine.rules.actions import Action
from zantine.rules.registry import GameModule, RoundContext, Rules
from zantine.rules.tasks import GRADED, TaskBoard, route
from zantine.rules.tasks import NAME as TASKS
from zantine.tasks import Task, task_kind
from zantine.tasks.audit import (
    APPROVE,
    AUDITOR,
    DUTIES,
    SPEC_HOLDER,
    VERDICTS,
    SpecHalf,
    audit_kind,
    normalize_verdict,
)
from zantine.tasks.graph import TASK_ID_RANGE, task_ids

if TYPE_CHECKING:
    from zantine.engine import Game
    from zantine.state import GameState, PlayerState

NAME = "audit"


@dataclass(frozen=True)
class Submission:
    """One graded answer on a Task: the reading an auditor checks."""

    task: str
    n: int  # 1-based, per Task
    seat: int
    answer: Any
    round: int
    correct: bool


@dataclass
class AuditBoard:
    """Module state, kept in ``GameState.ext["audit"]``."""

    # Every submission per Task id, oldest first.
    submissions: dict[str, list[Submission]] = field(default_factory=dict)
    # The latest reading each auditor was shown, by (audit id, auditor).
    shown: dict[tuple[str, int], Submission] = field(default_factory=dict)
    # The spec half each auditor was given, as (from seat, half), by (audit id, auditor).
    received: dict[tuple[str, int], tuple[int, SpecHalf]] = field(default_factory=dict)
    # (audit id, spec holder, auditor) for every share the holder was told it made.
    shared: set[tuple[str, int, int]] = field(default_factory=set)
    # The partner each assignee was last told about, by (audit id, seat).
    partners: dict[tuple[str, int], int] = field(default_factory=dict)
    # Every graded check in order (the ``audit.checked`` event data).
    checks: list[dict[str, Any]] = field(default_factory=list)
    # Audit groups: audit id -> group. Every audit in a group needs a different auditor.
    groups: dict[str, str] = field(default_factory=dict)
    # Required audits: they close only when a check passes (approves correct work).
    required: set[str] = field(default_factory=set)

    def latest(self, task_id: str) -> Submission | None:
        subs = self.submissions.get(task_id)
        return subs[-1] if subs else None


def audit_board(state: GameState) -> AuditBoard:
    return state.ext[NAME]


def _tasks(state: GameState) -> TaskBoard | None:
    return state.ext.get(TASKS)


def is_audit(task: Task) -> bool:
    return task.kind == audit_kind().name


def target_of(task: Task) -> str:
    return task.content.inputs["target"]


def audits(tb: TaskBoard) -> list[Task]:
    return [t for t in tb.graph if is_audit(t)]


def passed(check: dict[str, Any]) -> bool:
    """A check that approved correct work."""
    return check["verdict"] == APPROVE and check["outcome"] == "pass"


def group_auditors(state: GameState, audit_id: str) -> set[int]:
    """Players who may not audit ``audit_id`` because of its group: the auditors of
    the group's other audits, and anyone who has passed a check in the group."""
    tb, ab = _tasks(state), audit_board(state)
    group = ab.groups.get(audit_id)
    if tb is None or group is None:
        return set()
    members = {a for a, g in ab.groups.items() if g == group}
    out = {tb.graph.get(a).assignees[AUDITOR] for a in members if a != audit_id}
    out |= {c["auditor"] for c in ab.checks if c["task"] in members and passed(c)}
    return out


def delivered(state: GameState, task: Task) -> bool:
    """The current auditor of ``task`` holds the spec holder's shared half."""
    return (task.id, task.assignees[AUDITOR]) in audit_board(state).received


def _audit_for(state: GameState, task_id: str) -> tuple[TaskBoard, Task] | str:
    tb = _tasks(state)
    if (
        tb is None
        or NAME not in state.ext
        or not isinstance(task_id, str)
        or task_id not in tb.graph
    ):
        return f"no such Task {task_id!r}"
    task = tb.graph.get(task_id)
    if not is_audit(task):
        return f"{task.id} is not an audit"
    return tb, task


@dataclass(frozen=True)
class ShareSpec(Action):
    """Spec holder: show your half of the spec to the auditor in your room."""

    kind: ClassVar[str] = "audit.share"
    task: str = ""

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        found = _audit_for(state, self.task)
        if isinstance(found, str):
            return found
        _, task = found
        if actor.seat not in task.assignees or task.assignees.index(actor.seat) != SPEC_HOLDER:
            return f"you are not the spec holder of {task.id}"
        if task.id in audit_board(state).required and delivered(state, task):
            # Checked first: a required audit can only close after this, so the
            # answer never depends on the auditor's result.
            return f"you already shared your half of the spec for {task.id}"
        if task.done:
            return f"{task.id} is already done"
        auditor = state.player(task.assignees[AUDITOR])
        if not auditor.alive or auditor.room != actor.room:
            return f"the auditor of {task.id} is not in your room"
        return None


@dataclass(frozen=True)
class CheckAudit(Action):
    """Auditor: give your verdict on the latest reading you were shown."""

    kind: ClassVar[str] = "audit.check"
    task: str = ""
    verdict: str = ""  # "approve" | "reject"

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        found = _audit_for(state, self.task)
        if isinstance(found, str):
            return found
        tb, task = found
        if actor.seat not in task.assignees or task.assignees.index(actor.seat) != AUDITOR:
            return f"you are not the auditor of {task.id}"
        if task.done:
            return f"{task.id} is already done"
        if actor.room != task.room:
            return f"{task.id} is in {task.room}, not {actor.room}"
        ab = audit_board(state)
        reading = ab.shown.get((task.id, actor.seat))
        target = tb.graph.get(target_of(task))
        if reading is None:
            return f"nothing has been submitted for {target.id} yet"
        if reading.seat == actor.seat or actor.seat in target.assignees:
            return "you cannot audit your own submission"
        if actor.seat in group_auditors(state, task.id):
            return (
                f"you already check {target.id} in another audit; its checks need different players"
            )
        if (task.id, actor.seat) not in ab.received:
            holder = state.player(task.assignees[SPEC_HOLDER]).name
            return f"you need {holder}'s half of the spec first (audit.share in your room)"
        if normalize_verdict(self.verdict) is None:
            return f"verdict must be one of {', '.join(VERDICTS)}"
        return None


class AuditModule(GameModule):
    name = NAME

    def install(self, rules: Rules) -> None:
        rules.register_action(ShareSpec)
        rules.register_action(CheckAudit)
        # After tasks.work (400) and relay (450), so this Round's submissions are recorded.
        rules.add_resolver(475, "audit", resolve_audits)

    def on_setup(self, game: Game) -> None:
        state = game.state
        state.ext[NAME] = AuditBoard()
        tb = _tasks(state)
        # Seeded optional audits belong to the optional Verification Protocol only.
        if tb is None or game.config.audit_tasks == 0:
            return
        if game.config.verification_protocol != "optional":
            return
        rng = game.rng.fork(NAME)
        crew = [p.seat for p in state.living() if not p.is_impostor]
        rooms = [r for r in state.map.rooms if r != state.map.meeting_room] or list(state.map.rooms)
        targets = [t for t in tb.graph if auditable(t)]
        n = min(game.config.audit_tasks, len(targets), len(TASK_ID_RANGE) - len(tb.graph))
        chosen = {t.id for t in rng.sample(targets, n)}
        load = dict.fromkeys(crew, 0)
        for target in (t for t in targets if t.id in chosen):
            options = [s for s in crew if s not in target.assignees]
            if len(options) < 2:
                continue
            auditor = _least_loaded(rng, options, load)
            holder = _least_loaded(rng, [s for s in options if s != auditor], load)
            load[auditor] += 1
            load[holder] += 1
            place_audit(game, target.id, auditor, holder, rng.choice(rooms))

    def on_meeting_end(self, game: Game, meeting: int) -> None:
        """Keep audits independent after reassignment, then catch players up."""
        state, tb = game.state, _tasks(game.state)
        if tb is None:
            return
        living_crew = [p.seat for p in state.living() if not p.is_impostor]
        ab = audit_board(state)
        rng = game.rng.fork(f"{NAME}/reassign/{meeting}")
        for task in audits(tb):
            if task.done:
                continue
            target = tb.graph.get(target_of(task))
            submitters = {s.seat for s in ab.submissions.get(target.id, [])}
            excluded = set(target.assignees) | submitters
            required = task.id in ab.required
            for slot, seat in enumerate(task.assignees):
                if required and slot == SPEC_HOLDER and delivered(state, task):
                    # The holder's duty is done; replacing it would tell it (or a
                    # newcomer) whether the auditor's check had passed.
                    continue
                # A group's checks each need a different auditor.
                barred = excluded | (group_auditors(state, task.id) if slot == AUDITOR else set())
                if state.player(seat).alive and seat not in barred:
                    continue
                options = [s for s in living_crew if s not in barred and s not in task.assignees]
                if not options:
                    continue
                if required:
                    # Seeded per audit and slot, uniform over who is eligible: no
                    # draw or load depends on whether some other check passed.
                    new = game.rng.fork(f"{NAME}/reassign/{meeting}/{task.id}/{slot}").choice(
                        options
                    )
                else:
                    load = {s: sum(not t.done for t in tb.graph.of(s)) for s in options}
                    new = rng.choice([s for s in options if load[s] == min(load.values())])
                task.assignees[slot] = new
                ab.received.pop((task.id, new), None)
                ab.shown.pop((task.id, new), None)
                inherited = ab.received.pop((task.id, seat), None) if slot == AUDITOR else None
                view = tb.assign(task, new)
                game.event("audit.reassigned", task=task.id, from_seat=seat, to_seat=new)
                game.observe(seat, "audit.unassigned", task=task.id)
                game.observe(new, "tasks.reassigned", previous_assignee=seat, task=view)
                if required and inherited is not None:
                    # The delivered half passes to the replacement auditor.
                    ab.received[(task.id, new)] = inherited
                    game.observe(
                        new,
                        "audit.spec_shared",
                        task=task.id,
                        from_seat=inherited[0],
                        half=inherited[1],
                        inherited_from=seat,
                    )
        _catch_up(game)

    def context_extra(self, game: Game, seat: int) -> dict[str, Any] | None:
        tb = _tasks(game.state)
        if tb is None or NAME not in game.state.ext:
            return None
        ab = audit_board(game.state)
        here = game.state.player(seat).room
        out = []
        for task in audits(tb):
            if seat not in task.assignees or task.id not in tb.views[seat]:
                continue
            slot = task.assignees.index(seat)
            entry: dict[str, Any] = {
                "task": task.id,
                "duty": DUTIES[slot],
                "target": target_of(task),
                "room": task.room,
                "path": route(game.state.map, here, task.room),
                "partner": ab.partners.get((task.id, seat)),
                "spec_length": task.content.inputs["spec_length"],
                "spec": tb.views[seat][task.id]["inputs"]["spec"],
                "done": task.id in tb.known_done[seat],
            }
            if task.id in ab.required:
                entry["required"] = True
            if slot == AUDITOR:
                reading = ab.shown.get((task.id, seat))
                received = ab.received.get((task.id, seat))
                entry["reading"] = _reading(tb, reading) if reading else None
                entry["spec_received"] = received[1] if received else None
                if task.id in ab.required:
                    # A required audit can outlive a correct check (a rejection), so
                    # the auditor's own last result is part of what it knows.
                    entry["last_result"] = _last_result(ab, task.id, seat)
            else:
                entry["shared"] = (
                    delivered(game.state, task)
                    if task.id in ab.required
                    else (task.id, seat, task.assignees[AUDITOR]) in ab.shared
                )
            out.append(entry)
        return {"audits": out} if out else None


def place_audit(
    game: Game,
    target_id: str,
    auditor: int,
    holder: int,
    room: str,
    *,
    group: str | None = None,
    required: int = 0,
    flip: bool = False,
) -> Task:
    """Put an audit of ``target_id`` in ``room`` and tell both assignees.

    Neither the auditor nor the spec holder may hold the target, so no one
    audits their own work. Every audit in ``group`` needs a different auditor.
    ``required`` > 0 makes it one of that many mandatory checks of the target: it
    stays open until a check passes. ``flip`` swaps which half each player holds
    (``AuditKind.for_target``). Returns the new Task (never itself required for
    completion) with a seeded, unused Task id. No audits exist under the ``none``
    Verification Protocol.
    """
    tb = _tasks(game.state)
    if tb is None:
        raise ValueError("audits need the tasks module")
    if game.config.verification_protocol == "none":
        raise ValueError("the 'none' Verification Protocol has no audits")
    target = tb.graph.get(target_id)
    if auditor == holder:
        raise ValueError("the auditor and the spec holder must be different players")
    if auditor in target.assignees or holder in target.assignees:
        raise ValueError(f"no one may audit their own submission ({target_id})")
    ab = audit_board(game.state)
    submitters = {s.seat for s in ab.submissions.get(target_id, [])}
    if auditor in submitters or holder in submitters:
        raise ValueError(f"no one may audit their own submission ({target_id})")
    for seat in (auditor, holder):
        if not game.state.player(seat).alive:
            raise ValueError("audit assignees must be living players")
    if room not in game.state.map.rooms:
        raise ValueError(f"unknown audit room {room!r}")
    if group is not None:
        siblings = [a for a, g in ab.groups.items() if g == group]
        if auditor in {tb.graph.get(a).assignees[AUDITOR] for a in siblings}:
            raise ValueError(f"every audit in group {group!r} needs a different auditor")
    ids = task_ids(game.rng.fork(f"{NAME}/ids"), len(TASK_ID_RANGE))
    task_id = next((i for i in ids if i not in tb.graph), None)
    if task_id is None:
        raise ValueError("no unused Task ids for an audit")
    content = audit_kind().for_target(target, flip=flip, required=required)
    task = Task(task_id, room, [auditor, holder], content)
    tb.graph.add(task)
    extra: dict[str, Any] = {}
    if group is not None:
        ab.groups[task.id] = extra["group"] = group
    if required:
        ab.required.add(task.id)
        extra["required"] = required
    game.event("audit.placed", task=task.to_record(), target=target_id, **extra)
    for slot, seat in enumerate(task.assignees):
        partner = task.assignees[1 - slot]
        ab.partners[(task.id, seat)] = partner
        view = tb.assign(task, seat)
        game.observe(seat, "audit.assigned", duty=DUTIES[slot], partner=partner, task=view)
    _catch_up(game)
    return task


def resolve_audits(game: Game, rc: RoundContext) -> None:
    tb, ab = _tasks(game.state), audit_board(game.state)
    if tb is None:
        return
    # Checks first: they judge the reading shown before this Round's submissions.
    for seat, action in rc.acting(CheckAudit):
        _check(game, tb, ab, seat, action)
    for seat, action in rc.acting(ShareSpec):
        task = tb.graph.get(action.task)
        auditor = task.assignees[AUDITOR]
        if auditor in rc.killed:
            continue
        if task.done:
            continue
        half = dict(task.content.inputs["halves"][SPEC_HOLDER])
        ab.received[(task.id, auditor)] = (seat, half)
        ab.shared.add((task.id, seat, auditor))
        game.event(
            "audit.shared",
            task=task.id,
            seat=seat,
            auditor=auditor,
            half=half,
        )
        game.observe(auditor, "audit.spec_shared", task=task.id, from_seat=seat, half=half)
        game.observe(seat, "audit.shared", task=task.id, auditor=auditor)
        if task.id in ab.required:
            # The holder's duty ends here, whatever the auditor's check later says.
            tb.known_done[seat].add(task.id)
    for seat, task_id, answer, correct in rc.scratch.get(GRADED, []):
        subs = ab.submissions.setdefault(task_id, [])
        subs.append(Submission(task_id, len(subs) + 1, seat, answer, rc.round, correct))
    _catch_up(game)


def _check(game: Game, tb: TaskBoard, ab: AuditBoard, seat: int, action: CheckAudit) -> None:
    task = tb.graph.get(action.task)
    reading = ab.shown[(task.id, seat)]
    from_seat, half = ab.received[(task.id, seat)]
    verdict = normalize_verdict(action.verdict)
    outcome = "pass" if reading.correct else "fail"
    correct = (verdict == APPROVE) == reading.correct
    record = {
        "task": task.id,
        "auditor": seat,
        "target": target_of(task),
        "submission": reading.n,
        "submitter": reading.seat,
        "answer": reading.answer,
        "verdict": verdict,
        "outcome": outcome,
        "correct": correct,
        "spec_from": from_seat,
        "spec_received": half,
    }
    ab.checks.append(record)
    game.event("audit.checked", **record)
    # Private: only the auditor learns the outcome.
    game.observe(
        seat,
        "audit.result",
        task=task.id,
        target=record["target"],
        submitter=reading.seat,
        submission=reading.n,
        verdict=verdict,
        outcome=outcome,
        correct=correct,
    )
    if not correct:
        tb.stats[seat].wrong += 1
        return
    if task.id in ab.required and not passed(record):
        # A correct rejection: the work must be redone and checked again.
        return
    task.done, task.done_by, task.done_round = True, seat, game.state.round
    tb.known_done[seat].add(task.id)
    tb.stats[seat].done += 1
    if from_seat in tb.stats:
        tb.stats[from_seat].done += 1
    if task.id in ab.required:
        # Only the auditor learns that its check passed; the holder's duty already
        # ended when its half was delivered.
        return
    holder = task.assignees[SPEC_HOLDER]
    # The spec holder learns the audit is closed, never its outcome.
    tb.known_done[holder].add(task.id)
    game.observe(holder, "audit.closed", task=task.id)


def _catch_up(game: Game) -> None:
    """Show each auditor the latest reading and tell assignees about new partners."""
    tb, ab = _tasks(game.state), audit_board(game.state)
    if tb is None:
        return
    for task in audits(tb):
        if task.done:
            continue
        for slot, seat in enumerate(task.assignees):
            partner = task.assignees[1 - slot]
            if ab.partners.get((task.id, seat)) != partner:
                if slot == SPEC_HOLDER and task.id in ab.required and delivered(game.state, task):
                    # A required audit's holder is done once its half is delivered and
                    # keeps the partner it was told about: a new auditor would mean
                    # the old one's check had not passed.
                    continue
                ab.partners[(task.id, seat)] = partner
                received = ab.received.get((task.id, seat))
                if slot == AUDITOR and received is not None and received[0] != partner:
                    ab.received.pop((task.id, seat), None)
                game.observe(
                    seat, "audit.partner", task=task.id, duty=DUTIES[slot], partner=partner
                )
        auditor = task.assignees[AUDITOR]
        latest = ab.latest(target_of(task))
        if latest is None or not game.state.player(auditor).alive:
            continue
        if ab.shown.get((task.id, auditor)) != latest:
            ab.shown[(task.id, auditor)] = latest
            game.observe(auditor, "audit.reading", task=task.id, **_reading(tb, latest))


def _last_result(ab: AuditBoard, task_id: str, seat: int) -> dict[str, Any] | None:
    """What ``seat`` was told about its last check of ``task_id`` (``audit.result``)."""
    for c in reversed(ab.checks):
        if c["task"] == task_id and c["auditor"] == seat:
            return {k: c[k] for k in ("submission", "verdict", "outcome", "correct")}
    return None


def _reading(tb: TaskBoard, sub: Submission) -> dict[str, Any]:
    kind = task_kind(tb.graph.get(sub.task).kind)
    return {
        "target": sub.task,
        "submission": sub.n,
        "submitter": sub.seat,
        "answer": sub.answer,
        "items": kind.audit_items(sub.answer),
    }


def auditable(task: Task) -> bool:
    """Whether ``task`` can take an audit: real work of an auditable kind, 2+ items."""
    kind = task_kind(task.kind)
    if task.fake or not kind.auditable:
        return False
    items = kind.audit_items(task.content.answer)
    return items is not None and len(items) >= 2


def _least_loaded(rng: GameRng, options: list[int], load: dict[int, int]) -> int:
    least = min(load[s] for s in options)
    return rng.choice([s for s in options if load[s] == least])
