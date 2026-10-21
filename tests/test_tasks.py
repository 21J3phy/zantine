"""Tasks: seeded solo steps, the Task Graph, private grading, Productivity, solo trials."""

import json
from typing import Any

import pytest

from helpers import events, make_game, obs_of
from zantine.config import GameConfig
from zantine.engine import Game
from zantine.map import tiny_map
from zantine.rng import GameRng
from zantine.rules.actions import CallMeeting, Kill
from zantine.rules.tasks import WorkOnTask, board, route
from zantine.runner import play
from zantine.tasks import (
    Task,
    TaskContent,
    TaskGraph,
    TaskKind,
    build_task_graph,
    register_task_kind,
    run_solo_trial,
    run_solo_trials,
    task_kind,
    task_kind_names,
)
from zantine.tasks.graph import MAX_CHAIN
from zantine.tasks.steps import SortList


def started(**config: Any) -> Game:
    """A started game of idle PlaybookBots; seats 0 and 1 are Impostors. Solo steps only."""
    game = make_game(**{"relay_tasks": 0, "audit_tasks": 0, "splitkey_tasks": 0, **config})
    game.start()
    return game


def act(game: Game, seat: int, action: Any) -> None:
    """Script ``seat``'s action for the next Round."""
    game.controllers[seat].actions[game.state.round + 1] = action


def work(game: Game, task: Task, correct: bool = True) -> WorkOnTask:
    answer = task.content.answer if correct else "wrong"
    return WorkOnTask(task=task.id, answer=answer)


def place(game: Game, *tasks: Task) -> None:
    for task in tasks:
        game.state.player(task.assignees[0]).room = task.room


def dependent_pair(game: Game) -> tuple[Task, Task]:
    graph = board(game.state).graph
    dep = next(t for t in graph if t.requires)
    return graph.get(dep.requires[0]), dep


# Working and grading ----------------------------------------------------------------


def test_a_crewmate_completes_its_task_and_only_it_learns_the_grade() -> None:
    game = started(task_dependency_chance=0)
    task = board(game.state).graph.of(5)[0]
    place(game, task)
    act(game, 5, work(game, task))
    game.play_round()
    assert task.done and task.done_by == 5 and task.done_round == 1
    assert obs_of(game, 5, "tasks.graded") == [{"task": task.id, "correct": True}]
    for seat in range(10):
        if seat != 5:
            assert not [o for o in game.observations_of(seat) if o.type.startswith("tasks.graded")]
    assert events(game, "tasks.graded") == [
        {"seat": 5, "task": task.id, "correct": True, "fake": False}
    ]
    ctx = game.action_context(game.state.player(5))
    assert next(t for t in ctx.extra["tasks"]["tasks"] if t["task"] == task.id)["done"] is True


def test_a_wrong_answer_does_not_complete_the_task_and_can_be_retried() -> None:
    game = started(task_dependency_chance=0)
    task = board(game.state).graph.of(5)[0]
    place(game, task)
    act(game, 5, work(game, task, correct=False))
    game.play_round()
    assert not task.done
    assert obs_of(game, 5, "tasks.graded") == [{"task": task.id, "correct": False}]
    act(game, 5, work(game, task))
    game.play_round()
    assert task.done
    assert board(game.state).stats[5].wrong == 1


@pytest.mark.parametrize(
    ("make_action", "reason"),
    [
        (lambda t: WorkOnTask(task="T999", answer=1), "no such Task"),
        (lambda t: WorkOnTask(task=t.id, answer={"a": 1}), "answer must be"),
    ],
)
def test_bad_work_actions_are_invalid(make_action, reason: str) -> None:
    game = started(task_dependency_chance=0)
    task = board(game.state).graph.of(5)[0]
    place(game, task)
    act(game, 5, make_action(task))
    game.play_round()
    assert obs_of(game, 5, "invalid_action")[0]["reason"].startswith(reason)


def test_work_must_be_on_your_own_task_in_its_room_and_not_done() -> None:
    game = started(task_dependency_chance=0)
    graph = board(game.state).graph
    mine, theirs = graph.of(5)[0], graph.of(6)[0]
    game.state.player(5).room = game.state.map.meeting_room
    act(game, 5, work(game, mine))  # wrong room
    act(game, 0, work(game, theirs))  # Impostors have no Tasks
    game.play_round()
    meeting_room = game.state.map.meeting_room
    assert f"not {meeting_room}" in obs_of(game, 5, "invalid_action")[0]["reason"]
    assert "not one of your Tasks" in obs_of(game, 0, "invalid_action")[0]["reason"]
    place(game, mine)
    act(game, 5, work(game, mine))
    game.play_round()
    act(game, 5, work(game, mine))
    game.play_round()
    assert "already done" in obs_of(game, 5, "invalid_action")[-1]["reason"]


def test_work_actions_round_trip_through_parse_action() -> None:
    game = started()
    action = WorkOnTask(task="T3", answer=[3, 1, 2])
    assert game.rules.parse_action(action.to_dict()) == action


# The Task Graph ----------------------------------------------------------------------


def test_a_step_is_blocked_until_its_prerequisite_is_truly_complete() -> None:
    game = started(task_dependency_chance=1.0, tasks_per_crewmate=1)
    pre, dep = dependent_pair(game)
    a, b = pre.assignees[0], dep.assignees[0]
    place(game, pre, dep)
    # Round 1: prerequisite answered wrongly; dependent is blocked.
    act(game, a, work(game, pre, correct=False))
    act(game, b, work(game, dep))
    game.play_round()
    assert obs_of(game, b, "tasks.blocked") == [{"task": dep.id, "waiting_on": [pre.id]}]
    # Round 2: prerequisite done this Round; it only unlocks from the next Round.
    act(game, a, work(game, pre))
    act(game, b, work(game, dep))
    game.play_round()
    assert pre.done and not dep.done
    assert len(obs_of(game, b, "tasks.blocked")) == 2
    # Round 3: now it works.
    act(game, b, work(game, dep))
    game.play_round()
    assert dep.done
    assert board(game.state).stats[b].blocked == 2


def test_the_dependent_never_learns_the_prerequisites_grade() -> None:
    game = started(task_dependency_chance=1.0, tasks_per_crewmate=1)
    pre, dep = dependent_pair(game)
    a, b = pre.assignees[0], dep.assignees[0]
    place(game, pre)
    act(game, a, work(game, pre))
    game.play_round()
    assert pre.done
    ctx = game.action_context(game.state.player(b))
    view = next(t for t in ctx.extra["tasks"]["tasks"] if t["task"] == dep.id)
    assert view["requires"] == [
        {"task": pre.id, "title": pre.content.title, "room": pre.room, "assignees": [a]}
    ]
    assert "done" not in view["requires"][0]
    assert not [o for o in game.observations_of(b) if o.data.get("task") == pre.id]


def test_generated_graph_is_acyclic_crosses_players_and_has_short_chains() -> None:
    for seed in range(30):
        game = started(seed=seed, task_dependency_chance=1.0, tasks_per_crewmate=3)
        graph = board(game.state).graph
        assert len(graph) == 8 * 3
        assert not graph.of(0) and not graph.of(1)
        assert all(len(graph.of(s)) == 3 for s in range(2, 10))
        seen: set[str] = set()
        depth: dict[str, int] = {}
        for task in graph:
            assert task.room != game.state.map.meeting_room
            for req in task.requires:
                assert req in seen
                assert graph.get(req).assignees != task.assignees
            depth[task.id] = 1 + max((depth[r] for r in task.requires), default=0)
            assert depth[task.id] <= MAX_CHAIN
            seen.add(task.id)
        assert any(t.requires for t in graph)


def test_task_graph_rejects_unknown_prerequisites() -> None:
    content = SortList().generate(GameRng(0))
    with pytest.raises(ValueError):
        TaskGraph([Task("T1", "Weapons", [2], content, requires=("T2",))])


def test_route_follows_corridors() -> None:
    m = tiny_map()
    assert route(m, "Cafeteria", "Cafeteria") == []
    assert route(m, "Cafeteria", "Medbay") == ["Medbay"]
    path = route(m, "Cafeteria", "Navigation")
    assert len(path) == 2 and path[-1] == "Navigation"
    assert m.is_adjacent("Cafeteria", path[0]) and m.is_adjacent(path[0], "Navigation")


# Ending the game and Productivity ------------------------------------------------------


def test_all_tasks_done_is_a_crew_win_with_productivity_in_the_result() -> None:
    game = started(task_dependency_chance=0, tasks_per_crewmate=1)
    graph = board(game.state).graph
    place(game, *graph)
    for task in graph:
        act(game, task.assignees[0], work(game, task, correct=task.assignees[0] != 4))
    game.play_round()
    assert not game.state.over
    [last] = graph.of(4)
    act(game, 4, work(game, last))
    game.play_round()
    assert game.state.over and game.state.win_reason == "tasks_done"
    result = game.transcript.records[-1]
    assert result["winner"] == "crew" and result["reason"] == "tasks_done"
    tasks = result["tasks"]
    assert (tasks["productivity"], tasks["done"], tasks["total"]) == (1.0, 8, 8)
    seats = {s["seat"]: s for s in tasks["seats"]}
    assert seats[0]["productivity"] is None and seats[0]["assigned"] == 0
    assert seats[4] == {
        "seat": 4,
        "assigned": 1,
        "done": 1,
        "wrong": 1,
        "blocked": 0,
        "productivity": 1.0,
    }


def test_partial_productivity_when_impostors_win() -> None:
    game = started(task_dependency_chance=0, tasks_per_crewmate=2, round_cap=2)
    task = board(game.state).graph.of(7)[0]
    place(game, task)
    act(game, 7, work(game, task))
    game.run()
    tasks = game.transcript.records[-1]["tasks"]
    assert game.state.win_reason == "round_cap"
    assert (tasks["productivity"], tasks["done"], tasks["total"]) == (1 / 16, 1, 16)
    assert {s["seat"]: s["productivity"] for s in tasks["seats"]}[7] == 0.5


def test_no_tasks_means_no_task_win() -> None:
    game = started(tasks_per_crewmate=0, round_cap=1)
    game.run()
    assert game.state.win_reason == "round_cap"
    assert game.transcript.records[-1]["tasks"]["productivity"] is None


def test_a_dead_crewmates_tasks_are_reassigned_after_the_next_meeting() -> None:
    game = started(task_dependency_chance=0, kill_cooldown=0)
    b = board(game.state)
    victim_tasks = b.graph.of(5)
    act(game, 0, Kill(5))
    act(game, 6, CallMeeting())
    game.play_round()
    assert not b.graph.of(5)
    moved = events(game, "tasks.reassigned")
    assert [e["task"] for e in moved] == [t.id for t in victim_tasks]
    for e in moved:
        new = e["to_seat"]
        assert e["from_seat"] == 5 and game.state.player(new).alive
        assert not game.state.player(new).is_impostor
        [obs] = [o for o in obs_of(game, new, "tasks.reassigned") if o["task"]["task"] == e["task"]]
        assert obs["previous_assignee"] == 5
        assert e["task"] in [
            t["task"] for t in game.action_context(game.state.player(new)).extra["tasks"]["tasks"]
        ]
    # The reassigned seats got the fewest-loaded choices: nobody holds 4 while another holds 2.
    loads = [len(b.graph.of(s)) for s in range(2, 10) if s != 5]
    assert max(loads) - min(loads) <= 1


# Hidden information and seeding ---------------------------------------------------------


def test_assignments_are_private_and_never_include_the_answer_key() -> None:
    game = play(GameConfig(seed=9, audit_tasks=0))
    b = board(game.state)
    answers = {json.dumps(t.content.answer) for t in b.graph}
    for seat in range(10):
        for o in game.observations_of(seat):
            if o.type in ("tasks.assigned", "tasks.reassigned"):
                views = o.data["tasks"] if o.type == "tasks.assigned" else [o.data["task"]]
                for v in views:
                    assert "answer" not in v
                    assert seat in b.stats and v["task"] in b.stats[seat].assigned
    assigned = [o for o in game.transcript.observations() if o["type"] == "tasks.assigned"]
    impostors = {p.seat for p in game.state.players if p.is_impostor}
    assert {o["seat"] for o in assigned} == set(range(10)) - impostors
    # The answer key is only in ground-truth events.
    [generated] = events(game, "tasks.generated")
    assert {json.dumps(t["answer"]) for t in generated["tasks"]} == answers


def test_task_content_is_generated_from_the_seed() -> None:
    def contents(seed: int) -> list[Any]:
        g = started(seed=seed)
        return [(t.room, t.content.inputs, t.content.answer) for t in board(g.state).graph]

    assert contents(3) == contents(3)
    assert contents(3) != contents(4)
    answers = {json.dumps(c[2]) for seed in range(10) for c in contents(seed)}
    assert len(answers) > 100  # no fixed answer key


def test_full_games_record_task_play() -> None:
    game = play(GameConfig(seed=2))
    assert events(game, "tasks.graded")
    assert game.transcript.records[-1]["tasks"]["done"] > 0


# Task kinds and solo trials ----------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sort_list", "lookup_value"])
def test_reference_solver_passes_every_solo_trial(kind: str) -> None:
    solver = task_kind(kind).solve
    results = run_solo_trials(kind, range(60), solver)
    assert all(r.correct for r in results)
    assert len({json.dumps(r.expected) for r in results}) > 50
    assert all("answer" not in r.brief for r in results)
    assert run_solo_trial(kind, 1, lambda brief: "nonsense").correct is False


def test_answers_are_normalized() -> None:
    sort = task_kind("sort_list")
    c = sort.generate(GameRng(1))
    values = c.answer
    assert sort.grade(c, values)
    assert sort.grade(c, [str(v) for v in values])
    assert sort.grade(c, ", ".join(map(str, values)))
    assert not sort.grade(c, list(reversed(values)))
    assert not sort.grade(c, values[:-1])
    lookup = task_kind("lookup_value")
    c = lookup.generate(GameRng(1))
    assert lookup.grade(c, c.answer) and lookup.grade(c, f" {c.answer} ")
    assert not lookup.grade(c, c.answer + 1) and not lookup.grade(c, True)


def test_new_task_kinds_register_and_run_solo() -> None:
    @register_task_kind
    class Add(TaskKind):
        name = "test_add"

        def generate(self, rng: GameRng) -> TaskContent:
            a, b = rng.randint(1, 9), rng.randint(1, 9)
            return TaskContent(self.name, "Add", "Add a and b.", {"a": a, "b": b}, a + b)

        def grade(self, content: TaskContent, answer: Any) -> bool:
            return answer == content.answer

        def solve(self, brief: dict[str, Any]) -> Any:
            return brief["inputs"]["a"] + brief["inputs"]["b"]

    assert "test_add" in task_kind_names()
    assert run_solo_trial("test_add", 5, task_kind("test_add").solve).correct
    with pytest.raises(ValueError):
        register_task_kind(Add)
    with pytest.raises(ValueError):
        task_kind("nope")


def test_multi_seat_kinds_are_not_solo_steps() -> None:
    @register_task_kind
    class Pair(TaskKind):
        name = "test_pair"
        seats = 2

        def generate(self, rng: GameRng) -> TaskContent:
            return TaskContent(self.name, "Pair", "Together.", {}, 0)

        def grade(self, content: TaskContent, answer: Any) -> bool:
            return answer == 0

        def solve(self, brief: dict[str, Any]) -> Any:
            return 0

    with pytest.raises(ValueError):
        build_task_graph(GameRng(0), [2, 3], ["Weapons"], ["test_pair"], 1, 0.0)
