"""Tasks: Crewmates work on seeded Tasks linked by the crew's Task Graph.

At setup every Crewmate is privately assigned solo steps (``zantine.tasks``).
Working a Task is an action taken in the Task's room with an answer. The answer
is graded automatically and privately: only the worker is told the result. A
step whose prerequisites are not truly complete at the start of the Round is
blocked, and the worker is told so. All counted Tasks done is a crew win.

The rules implemented here are documented in ``docs/rules.md``; keep them in sync.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from zantine.map import GameMap
from zantine.roles import Team
from zantine.rules.actions import Action
from zantine.rules.registry import GameModule, RoundContext, Rules
from zantine.tasks import Task, TaskGraph, build_task_graph, task_kind

if TYPE_CHECKING:
    from zantine.engine import Game
    from zantine.state import GameState, PlayerState

NAME = "tasks"
# Round scratch key: every answer graded this Round, as (seat, task id, answer,
# correct), for modules that check work (audits; later Sabotage and Verification
# Protocols). Kinds worked through their own action may append to it too.
GRADED = "tasks.graded"


@dataclass
class SeatTaskStats:
    # Tasks ever assigned to the seat (including reassigned ones), by id.
    assigned: list[str] = field(default_factory=list)
    done: int = 0
    wrong: int = 0
    blocked: int = 0

    def productivity(self) -> float | None:
        return self.done / len(self.assigned) if self.assigned else None


@dataclass
class TaskBoard:
    """Module state, kept in ``GameState.ext["tasks"]``."""

    graph: TaskGraph
    stats: dict[int, SeatTaskStats]
    # What each seat has been told about each of its Tasks, by Task id.
    views: dict[int, dict[str, dict[str, Any]]]
    # Tasks each seat has been told it completed.
    known_done: dict[int, set[str]]

    def view(self, task: Task, seat: int) -> dict[str, Any]:
        """A seat's private brief for one of its Tasks (never the answer key)."""
        brief = task_kind(task.kind).brief(task.content, task.assignees.index(seat))
        view = {
            "task": task.id,
            "room": task.room,
            **brief,
            "requires": [self._ref(r) for r in task.requires],
            "unlocks": [self._ref(d) for d in self.graph.dependents(task.id)],
        }
        if len(task.assignees) > 1:
            # Everyone on a multi-player Task, in slot order.
            view["assignees"] = list(task.assignees)
        return view

    def _ref(self, task_id: str) -> dict[str, Any]:
        t = self.graph.get(task_id)
        return {
            "task": t.id,
            "title": t.content.title,
            "room": t.room,
            "assignees": list(t.assignees),
        }

    def assign(self, task: Task, seat: int) -> dict[str, Any]:
        self.stats[seat].assigned.append(task.id)
        view = self.view(task, seat)
        self.views[seat][task.id] = view
        return view

    def summary(self) -> dict[str, Any]:
        counted = self.graph.counted()
        done = sum(t.done for t in counted)
        return {
            "productivity": done / len(counted) if counted else None,
            "done": done,
            "total": len(counted),
            "seats": [
                {
                    "seat": seat,
                    "assigned": len(s.assigned),
                    "done": s.done,
                    "wrong": s.wrong,
                    "blocked": s.blocked,
                    "productivity": s.productivity(),
                }
                for seat, s in sorted(self.stats.items())
            ],
        }


def board(state: GameState) -> TaskBoard:
    return state.ext[NAME]


@dataclass(frozen=True)
class WorkOnTask(Action):
    """Work on one of your Tasks in its room, submitting an answer."""

    kind: ClassVar[str] = "tasks.work"
    task: str = ""
    answer: Any = None

    def validate(self, state: GameState, actor: PlayerState) -> str | None:
        b: TaskBoard | None = state.ext.get(NAME)
        if b is None or self.task not in b.graph:
            return f"no such Task {self.task!r}"
        task = b.graph.get(self.task)
        if actor.seat not in task.assignees:
            return f"{task.id} is not one of your Tasks"
        actions = task_kind(task.kind).work_actions
        if self.kind not in actions:
            return f"{task.id} is worked with {', '.join(actions)}, not {self.kind}"
        if task.done:
            return f"{task.id} is already done"
        if actor.room != task.room:
            return f"{task.id} is in {task.room}, not {actor.room}"
        if not _answer_shape_ok(self.answer):
            return "answer must be text, a number, or a list of those"
        return None


class TasksModule(GameModule):
    name = NAME

    def install(self, rules: Rules) -> None:
        rules.register_action(WorkOnTask)
        rules.add_resolver(400, "tasks.work", resolve_work)
        rules.add_win_condition(400, "tasks.all_done", all_tasks_done)

    def on_setup(self, game: Game) -> None:
        state, cfg = game.state, game.config
        crew = [p.seat for p in state.players if not p.is_impostor]
        rooms = [r for r in state.map.rooms if r != state.map.meeting_room] or list(state.map.rooms)
        graph = build_task_graph(
            game.rng.fork(NAME),
            crew,
            rooms,
            cfg.task_kinds,
            cfg.tasks_per_crewmate,
            cfg.task_dependency_chance,
        )
        # Modules with a ``place_tasks(game, graph)`` method add their own
        # (multi-player) Tasks before anything is announced.
        for module in game.rules.modules:
            module.place_tasks(game, graph)
        b = TaskBoard(
            graph,
            stats={p.seat: SeatTaskStats() for p in state.players},
            views={p.seat: {} for p in state.players},
            known_done={p.seat: set() for p in state.players},
        )
        state.ext[NAME] = b
        game.event("tasks.generated", tasks=[t.to_record() for t in graph])
        for seat in crew:
            views = [b.assign(task, seat) for task in graph.of(seat)]
            game.observe(seat, "tasks.assigned", tasks=views)

    def on_meeting_end(self, game: Game, meeting: int) -> None:
        """Hand unfinished Tasks of dead or ejected players to living Crewmates."""
        state, b = game.state, board(game.state)
        living_crew = [p.seat for p in state.living() if not p.is_impostor]
        rng = game.rng.fork(f"{NAME}/reassign/{meeting}")
        for task in b.graph:
            if task.done or task.fake or task.closed or task_kind(task.kind).manages_reassignment:
                continue
            for slot, seat in enumerate(task.assignees):
                options = [s for s in living_crew if s not in task.assignees]
                if state.player(seat).alive or not options:
                    continue
                load = {s: sum(not t.done for t in b.graph.of(s)) for s in options}
                least = min(load.values())
                new = rng.choice([s for s in options if load[s] == least])
                task.assignees[slot] = new
                view = b.assign(task, new)
                game.event("tasks.reassigned", task=task.id, from_seat=seat, to_seat=new)
                game.observe(new, "tasks.reassigned", previous_assignee=seat, task=view)

    def context_extra(self, game: Game, seat: int) -> dict[str, Any] | None:
        b: TaskBoard | None = game.state.ext.get(NAME)
        if b is None or not b.views[seat]:
            return None
        player = game.state.player(seat)
        mine = [t.id for t in b.graph.of(seat)]
        return {
            "tasks": [
                {
                    **view,
                    "done": task_id in b.known_done[seat],
                    "path": route(game.state.map, player.room, view["room"]),
                }
                for task_id, view in b.views[seat].items()
                if task_id in mine
            ]
        }

    def result_extra(self, game: Game) -> dict[str, Any] | None:
        return board(game.state).summary()


def resolve_work(game: Game, rc: RoundContext) -> None:
    b = board(game.state)
    # Prerequisites count only if complete at the start of the Round.
    done_before = {t.id for t in b.graph if t.done}
    rc.scratch["tasks.done_before"] = done_before
    for seat, action in rc.acting(WorkOnTask):
        task = b.graph.get(action.task)
        stats = b.stats[seat]
        waiting = [r for r in task.requires if r not in done_before]
        if waiting:
            stats.blocked += 1
            game.event("tasks.blocked", seat=seat, task=task.id, waiting_on=waiting)
            game.observe(seat, "tasks.blocked", task=task.id, waiting_on=waiting)
            continue
        correct = task_kind(task.kind).grade(task.content, action.answer)
        game.event("tasks.graded", seat=seat, task=task.id, correct=correct, fake=task.fake)
        rc.scratch.setdefault(GRADED, []).append((seat, task.id, action.answer, correct))
        # Private: only the worker learns the grade.
        game.observe(seat, "tasks.graded", task=task.id, correct=correct)
        if not correct:
            stats.wrong += 1
        elif not task.done:
            task.done, task.done_by, task.done_round = True, seat, rc.round
            stats.done += 1
            b.known_done[seat].add(task.id)


def all_tasks_done(game: Game) -> tuple[Team, str] | None:
    if board(game.state).graph.all_done():
        return Team.CREW, "tasks_done"
    return None


def route(game_map: GameMap, start: str, goal: str) -> list[str]:
    """Rooms to walk through from ``start`` to ``goal`` (excluding ``start``)."""
    previous: dict[str, str | None] = {start: None}
    frontier = deque([start])
    while frontier:
        room = frontier.popleft()
        if room == goal:
            break
        for n in game_map.neighbors(room):
            if n not in previous:
                previous[n] = room
                frontier.append(n)
    path: list[str] = []
    step: str | None = goal
    while step is not None and step != start:
        path.append(step)
        step = previous[step]
    return path[::-1]


def _answer_shape_ok(answer: Any) -> bool:
    if isinstance(answer, list):
        return all(isinstance(x, str | int | float) for x in answer)
    return isinstance(answer, str | int | float)
