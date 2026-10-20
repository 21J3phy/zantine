"""Verification Protocols: the crew's rule for checking each other's work (v1.1, #37).

``GameConfig.verification_protocol`` sets one per game:

- ``none``: no audits at all (``rules/audit.py`` places none and ``place_audit``
  refuses).
- ``optional`` (the v1 default): the seeded optional audits of ``rules/audit.py``,
  which never gate anything.
- ``mandatory``: every auditable required Task (the solo steps) needs two
  independent checks before it counts. Correct work closes the Task, but it is
  done (unlocks its dependents, counts toward Productivity and the crew win)
  only once **two different players**, neither of whom submitted or holds it,
  have each passed a check of it: approved a correct submission. Wrong work
  never counts, whatever a checker says.

Each gated Task gets two required audits in one audit group, placed at setup in
the Task's own room with ``place_audit``. Two Crewmates cross-check: one audits
the first and holds the spec of the second, the other the reverse; the second
audit's halves are flipped, so neither checker holds the whole spec until its
partner shares. The audit module enforces independence (a group's audits need
different auditors, also after Meeting reassignment) and keeps a required audit
open after a correct rejection, so the worker can resubmit. As in every audit,
only a checker learns its own result; the worker is never told when its work
is verified, and nobody is told the outcome of anyone else's check.

Chain relays are not auditable (their grade is a hidden part), so they are not
gated. The rules implemented here are documented in ``docs/rules.md``; keep them
in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from zantine.rules.audit import NAME as AUDIT
from zantine.rules.audit import audit_board, auditable, passed, place_audit
from zantine.rules.registry import GameModule, RoundContext, Rules
from zantine.rules.tasks import NAME as TASKS
from zantine.rules.tasks import TaskBoard
from zantine.tasks import task_kind
from zantine.tasks.graph import TASK_ID_RANGE

if TYPE_CHECKING:
    from zantine.engine import Game
    from zantine.state import GameState
    from zantine.tasks import TaskGraph

NAME = "verification"
# Independent passing checks a gated Task needs under the mandatory protocol.
CHECKS = 2


@dataclass
class VerificationBoard:
    """Module state, kept in ``GameState.ext["verification"]``."""

    protocol: str
    # Gated Task id -> its mandatory audit ids, in placement order.
    checks: dict[str, list[str]] = field(default_factory=dict)
    # Gated Task id -> the Round it was verified.
    verified: dict[str, int] = field(default_factory=dict)


def verification_board(state: GameState) -> VerificationBoard:
    return state.ext[NAME]


def checkers(state: GameState, task_id: str) -> set[int]:
    """Players who have passed one of ``task_id``'s mandatory checks, excluding
    anyone who ever submitted work on it or holds it."""
    vb, ab, tb = verification_board(state), audit_board(state), state.ext[TASKS]
    audits = set(vb.checks.get(task_id, ()))
    excluded = {s.seat for s in ab.submissions.get(task_id, [])}
    excluded |= set(tb.graph.get(task_id).assignees)
    return {
        c["auditor"]
        for c in ab.checks
        if c["task"] in audits and passed(c) and c["auditor"] not in excluded
    }


class VerificationModule(GameModule):
    name = NAME

    def install(self, rules: Rules) -> None:
        # After the audit resolver (475), so this Round's checks are graded.
        rules.add_resolver(480, "verification", resolve_verification)

    def place_tasks(self, game: Game, graph: TaskGraph) -> None:
        if game.config.verification_protocol != "mandatory":
            return
        for task in graph:
            if task_kind(task.kind).required_for_completion and auditable(task):
                task.checks_required = CHECKS

    def on_setup(self, game: Game) -> None:
        state = game.state
        vb = VerificationBoard(game.config.verification_protocol)
        state.ext[NAME] = vb
        tb: TaskBoard | None = state.ext.get(TASKS)
        if tb is None or vb.protocol != "mandatory":
            return
        targets = [t for t in tb.graph if t.checks_required]
        free = len(TASK_ID_RANGE) - len(tb.graph)
        if CHECKS * len(targets) > free:
            raise ValueError(
                f"mandatory verification needs {CHECKS * len(targets)} audit ids, "
                f"only {free} Task ids are left"
            )
        rng = game.rng.fork(NAME)
        crew = [p.seat for p in state.living() if not p.is_impostor]
        load = dict.fromkeys(crew, 0)
        for target in targets:
            options = [s for s in crew if s not in target.assignees]
            if len(options) < CHECKS:
                raise ValueError(
                    f"mandatory verification needs {CHECKS} Crewmates besides {target.id}'s holder"
                )
            pair: list[int] = []
            for _ in range(CHECKS):
                left = [s for s in options if s not in pair]
                least = min(load[s] for s in left)
                pair.append(rng.choice([s for s in left if load[s] == least]))
            for seat in pair:
                load[seat] += CHECKS  # an audit duty and a spec duty per check
            # Cross-check: player i audits check i and holds the spec of check i+1.
            vb.checks[target.id] = [
                place_audit(
                    game,
                    target.id,
                    pair[i],
                    pair[(i + 1) % CHECKS],
                    target.room,
                    group=target.id,
                    required=CHECKS,
                    flip=i % 2 == 1,
                ).id
                for i in range(CHECKS)
            ]
        game.event(
            "verification.gated",
            tasks={t: list(a) for t, a in vb.checks.items()},
            checks=CHECKS,
        )

    def result_extra(self, game: Game) -> dict[str, Any] | None:
        vb = verification_board(game.state)
        checks = audit_board(game.state).checks if AUDIT in game.state.ext else []
        audits = {a for ids in vb.checks.values() for a in ids}
        return {
            "protocol": vb.protocol,
            "gated": len(vb.checks),
            "verified": len(vb.verified),
            "checks": sum(c["task"] in audits for c in checks),
            "passed": sum(c["task"] in audits and passed(c) for c in checks),
        }


def resolve_verification(game: Game, rc: RoundContext) -> None:
    state = game.state
    vb: VerificationBoard | None = state.ext.get(NAME)
    if vb is None or not vb.checks:
        return
    tb: TaskBoard = state.ext[TASKS]
    ab = audit_board(state)
    for task_id in vb.checks:
        task = tb.graph.get(task_id)
        if task.done or not task.closed:
            continue
        by = checkers(state, task_id)
        if len(by) < CHECKS:
            continue
        # The work that passed: a gated Task closes on its first correct submission.
        work = next(s for s in reversed(ab.submissions[task_id]) if s.correct)
        task.done, task.done_by, task.done_round = True, work.seat, rc.round
        tb.stats[work.seat].done += 1
        vb.verified[task_id] = rc.round
        # Ground truth only: the worker is not told, and each checker knows only
        # its own result.
        game.event(
            "verification.verified",
            task=task_id,
            submitter=work.seat,
            submission=work.n,
            checkers=sorted(by),
        )
