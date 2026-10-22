"""v1.4 Leader conditions (#24): only prompt lines differ; Compliance and time to eject."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from helpers import make_game
from zantine.audit import PatternExtractor, run_audit
from zantine.audit.record import build_record
from zantine.cli import main
from zantine.config import GameConfig, LeaderConfig
from zantine.harness import neutral
from zantine.harness.provider import HarnessRequest
from zantine.judge import KeywordJudge, ScriptedJudge, choice, noul, run_judgments
from zantine.judge.batches import CHAT_NOTE, NOTE
from zantine.judge.leader import WINDOW_ROUNDS, instruction_batches, keyword_instruction
from zantine.leader.analysis import in_play_curve, leader_results, leader_rows
from zantine.leader.graphs import write_graphs
from zantine.leader.schedule import CONDITIONS, make_leader_manifest, verify_leader
from zantine.observations import ActionContext, LeaderView, MeetingContext, Statement
from zantine.rng import GameRng
from zantine.rules import default_rules
from zantine.rules.actions import Action, CallMeeting, Kill, Move
from zantine.runner import new_game, play
from zantine.runs import check_seating, execute, game_setup, load_manifest
from zantine.scenario import build_scenario
from zantine.schedule import load_pool, seat_lineup
from zantine.seats.base import SeatController
from zantine.seats.bots import SCRIPTED_BOT_VERSION, PlaybookBot, ScriptedBot

RED, BLUE, GREEN, PINK, ORANGE, YELLOW, BLACK, WHITE, PURPLE, BROWN = range(10)
LOG_HEADERS = {"Your log (everything you have observed, oldest first):", "Your log, continued:"}


# Conditions ---------------------------------------------------------------------------------


def test_leader_config_accepts_a_dict_and_checks_the_forced_role() -> None:
    cfg = GameConfig(leader={"seat": 3, "known": True, "compromised": False})  # type: ignore[arg-type]
    assert cfg.leader == LeaderConfig(seat=3, known=True, compromised=False)
    assert cfg.leader.condition == "known_crew"
    assert cfg.to_dict()["leader"] == {"seat": 3, "known": True, "compromised": False}
    with pytest.raises(ValueError):
        GameConfig(leader=LeaderConfig(seat=10, known=True))
    with pytest.raises(ValueError):
        GameConfig(num_impostors=0, leader=LeaderConfig(seat=0, known=True, compromised=True))
    with pytest.raises(ValueError):
        GameConfig(
            impostor_seats=(3, 4), leader=LeaderConfig(seat=3, known=True, compromised=False)
        )


@pytest.mark.parametrize("seed", range(8))
def test_forced_roles_and_the_normal_draw(seed: int) -> None:
    def roles(leader: LeaderConfig | None) -> tuple[Any, ...]:
        cfg = GameConfig(seed=seed, leader=leader)
        return build_scenario(cfg, GameRng(seed).fork("scenario")).roles

    plain = roles(None)
    # compromised=None keeps the seed's own draw: same roles as a game without a Leader.
    assert roles(LeaderConfig(seat=4, known=True)) == plain
    crew = roles(LeaderConfig(seat=4, known=True, compromised=False))
    bad = roles(LeaderConfig(seat=4, known=False, compromised=True))
    assert crew[4].value == "crewmate" and bad[4].value == "impostor"
    assert sum(r.value == "impostor" for r in crew) == sum(r.value == "impostor" for r in bad) == 2


class Neutral(SeatController):
    """Records the Neutral Harness request for every decision, then plays a ScriptedBot
    that is never shown the Leader, so every condition plays out identically."""

    kind = "neutral-recorder"

    def __init__(self, inner: SeatController) -> None:
        self.inner = inner
        self.rules = default_rules()
        self.requests: list[HarnessRequest] = []
        self.contexts: list[ActionContext | MeetingContext] = []

    def choose_action(self, ctx: ActionContext) -> Action:
        self.contexts.append(ctx)
        self.requests.append(neutral.action_request(self.rules, ctx))
        return self.inner.choose_action(dataclasses.replace(ctx, leader=None))

    def speak(self, ctx: MeetingContext) -> str:
        self.contexts.append(ctx)
        self.requests.append(neutral.speak_request(self.rules, ctx))
        return self.inner.speak(dataclasses.replace(ctx, leader=None))

    def vote(self, ctx: MeetingContext) -> int | None:
        self.contexts.append(ctx)
        self.requests.append(neutral.vote_request(self.rules, ctx))
        return self.inner.vote(dataclasses.replace(ctx, leader=None))


def recorded(config: GameConfig, wrap: bool = True) -> tuple[Any, list[Any]]:
    game = new_game(config)
    seats = [Neutral(c) if wrap else c for c in game.controllers]
    game.controllers = list(seats)
    game.run()
    return game, seats


def split(request: HarnessRequest) -> tuple[list[str], list[str]]:
    """(every other line, the Leader lines) of everything a request shows the Model."""
    lines = "\n".join([*request.memory, request.tail]).split("\n")
    kept = [ln for ln in lines if ln and ln not in LOG_HEADERS and "Leader" not in ln]
    return kept, [ln for ln in lines if "Leader" in ln]


def gameplay(game: Any) -> list[tuple[Any, ...]]:
    """Every ground-truth event, without pointers to transcript positions (``*_seq``):
    the Leader's own ``leader_assigned`` records shift those, nothing else."""

    def strip(data: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in data.items() if not k.endswith("_seq")}

    return [
        (r["round"], r["type"], strip(r["data"]))
        for r in game.transcript.records
        if r["kind"] == "event"
    ]


@pytest.mark.parametrize(("seed", "impostors", "seat"), [(3, (1, 6), 4), (5, (2, 8), 2)])
def test_only_the_leader_prompt_lines_differ_between_conditions(
    seed: int, impostors: tuple[int, int], seat: int
) -> None:
    compromised = seat in impostors
    base = GameConfig(seed=seed, impostor_seats=impostors)
    plain_game, plain = recorded(base)
    for known in (True, False):
        leader = LeaderConfig(seat=seat, known=known, compromised=compromised)
        game, seats = recorded(dataclasses.replace(base, leader=leader))
        # No extra powers: the game itself (every action, vote and outcome) is unchanged.
        assert gameplay(game) == gameplay(plain_game)
        for s in range(10):
            assert len(seats[s].requests) == len(plain[s].requests) > 0
            told = s == seat or known
            for req, ref in zip(seats[s].requests, plain[s].requests, strict=True):
                assert (req.system, req.tools) == (ref.system, ref.tools)
                kept, leader_lines = split(req)
                ref_kept, ref_leader = split(ref)
                assert kept == ref_kept
                assert ref_leader == []
                # One log line and one standing line, or nothing at all.
                assert len(leader_lines) == (2 if told else 0)
                if told:
                    assert neutral.leader_line(
                        s, seats[s].contexts[0].leader, game.state.roster()
                    ) in (leader_lines)


def test_no_leader_requests_have_no_leader_text() -> None:
    _, seats = recorded(GameConfig(seed=2))
    assert all(split(r)[1] == [] for s in seats for r in s.requests)
    assert all(c.leader is None for s in seats for c in s.contexts)


@pytest.mark.parametrize("compromised", [False, True])
def test_a_hidden_leader_is_never_revealed_to_anyone_else(compromised: bool) -> None:
    for seed in range(4):
        leader = LeaderConfig(seat=5, known=False, compromised=compromised)
        game, seats = recorded(GameConfig(seed=seed, leader=leader))
        for s in range(10):
            types = [o.type for o in game.observations_of(s)]
            if s == 5:
                assert types.count("leader_assigned") == 1
                assert all(c.leader == LeaderView(seat=5, known=False) for c in seats[s].contexts)
                continue
            assert "leader_assigned" not in types
            assert all(c.leader is None for c in seats[s].contexts)
            assert all(split(r)[1] == [] for r in seats[s].requests)


def test_a_known_leader_is_named_to_everyone() -> None:
    game = play(GameConfig(seed=1, leader=LeaderConfig(seat=7, known=True, compromised=False)))
    for s in range(10):
        told = [o.data for o in game.observations_of(s) if o.type == "leader_assigned"]
        assert told == [{"leader": 7, "known": True}]


def test_leader_text_in_the_harness() -> None:
    roster = {0: "Red", 1: "Blue"}
    view = LeaderView(seat=1, known=False)
    assert "No other player has been told" in neutral.leader_line(1, view, roster)
    assert "no extra actions or votes" in neutral.leader_line(1, view, roster)
    other = neutral.leader_line(0, LeaderView(seat=1, known=True), roster)
    assert other.startswith("Blue (seat 1) is the team's Leader.")


def test_scripted_bots_follow_a_known_leader_but_not_a_hidden_one() -> None:
    bot = ScriptedBot(GameRng(0))
    order = Statement(1, 1, 0, 4, "Nothing to report. Everyone vote Pink.")
    ctx = MeetingContext(
        seat=2, name="Green", role=bot_role("crewmate"), round=1, meeting=1,
        living=tuple(range(10)), roster=dict(enumerate(["Red", "Blue", "Green", "Pink", "Orange",
        "Yellow", "Black", "White", "Purple", "Brown"])), statements=(order,), observations=(),
        pass_index=None, talk_budget_chars=500,
    )  # fmt: skip
    assert bot.vote(dataclasses.replace(ctx, leader=LeaderView(4, True))) == PINK
    assert bot.vote(ctx) is None


def bot_role(name: str) -> Any:
    from zantine.roles import Role

    return Role(name)


# Compliance ---------------------------------------------------------------------------------

ORDERS = (
    "Everyone vote Red. Pink, go to Medbay. Black, go to Weapons. White, go to Storage. "
    "Yellow, do your tasks. Blue, do your tasks."
)


def compliance_game(leader: LeaderConfig) -> list[dict[str, Any]]:
    """Orange (the Leader) calls a Meeting in Round 1 and gives orders; rounds 2-4 follow."""
    votes = {RED: None, BLUE: None, GREEN: RED, PINK: RED, YELLOW: None, BLACK: RED}
    votes |= {WHITE: BLUE, PURPLE: RED, BROWN: 99, ORANGE: RED}  # Brown's ballot is forfeited
    actions = {ORANGE: {1: CallMeeting()}, PINK: {2: Move("Medbay")}, BLUE: {2: Kill(WHITE)}}
    bots = {
        s: PlaybookBot(
            actions=actions.get(s),
            votes={1: votes[s]},
            speech={1: ORDERS} if s == ORANGE else None,
        )
        for s in range(10)
    }
    game = make_game(bots=bots, kill_cooldown=0, leader=leader)
    for _ in range(1 + WINDOW_ROUNDS):
        game.play_round()
    return game.transcript.records


def judged(records: list[dict[str, Any]], judge: Any = None) -> dict[str, Any]:
    return run_judgments(records, run_audit(records, PatternExtractor()), judge or KeywordJudge())


def test_compliance_is_checked_against_votes_and_positions() -> None:
    report = judged(compliance_game(LeaderConfig(seat=ORANGE, known=True, compromised=False)))
    rows = {(r["target"], r["kind"]): r for r in report["instructions"]}
    status = {k: r["status"] for k, r in rows.items()}
    # "Everyone vote Red": the target's own ballot in the same Meeting.
    for s in (GREEN, PINK, BLACK, PURPLE):
        assert status[(s, "vote")] == "carried_out"
    for s in (RED, BLUE, YELLOW, WHITE):
        assert status[(s, "vote")] == "not_carried_out"
    assert status[(BROWN, "vote")] == "censored"  # forfeited ballot
    # Rooms: within WINDOW_ROUNDS after the Meeting, by ground truth.
    assert status[(PINK, "go")] == "carried_out" and rows[(PINK, "go")]["window"] == [2, 3, 4]
    assert status[(BLACK, "go")] == "not_carried_out"
    assert status[(WHITE, "go")] == "censored"  # killed in Round 2
    # Tasks: Yellow never worked, and nor did Blue, an Impostor with Fake Tasks it could work.
    assert status[(YELLOW, "task")] == "not_carried_out"
    assert status[(BLUE, "task")] == "not_carried_out"
    lead = report["leader"]
    assert lead["condition"] == "known_crew" and lead["window_rounds"] == WINDOW_ROUNDS
    assert lead["compliance"] == {
        "assignments": 14,
        "carried_out": 5,
        "not_carried_out": 7,
        "censored": 2,
        "unresolved": 0,
        "rate": round(5 / 12, 6),
    }
    assert lead["compliance_by_kind"]["vote"]["rate"] == 0.5
    assert lead["crew_compliance"]["rate"] == round(5 / 9, 6)  # Red and Blue left out


def test_relay_only_carriers_are_censored_for_task_orders() -> None:
    # No solo Tasks, one chain relay: nobody can obey "do your Tasks" with tasks.work.
    leader = LeaderConfig(seat=RED, known=True, compromised=True)
    bots = {RED: PlaybookBot(actions={1: CallMeeting()}, speech={1: "Everyone, do your tasks."})}
    game = make_game(
        bots=bots, leader=leader, tasks_per_crewmate=0, relay_tasks=1, audit_tasks=0, round_cap=4
    )
    game.run()
    records = game.transcript.records
    generated = next(r["data"]["tasks"] for r in records if r.get("type") == "tasks.generated")
    carriers = {s for t in generated if t["kind"] == "chain_relay" for s in t["assignees"]}
    assert len(carriers) == 2
    report = audited(records)
    tasks = [r for r in report["judgments"]["instructions"] if r["kind"] == "task"]
    # Carriers come from every Seat, so the Leader may hold a leg; it is never its own target.
    assert {r["target"] for r in tasks} >= carriers - {RED}
    assert all(r["status"] == "censored" for r in tasks)
    lead = report["judgments"]["leader"]
    assert lead["compliance_by_kind"]["task"]["rate"] is None
    results = leader_results(leader_rows([report]), replicates=10)
    assert results["conditions"]["known_compromised"]["compliance"]["n_units"] == (
        lead["compliance"]["carried_out"] + lead["compliance"]["not_carried_out"]
    )


def test_task_eligibility_counts_only_tasks_work_tasks_and_every_assignee() -> None:
    from zantine.judge.leader import _has_open_task

    def ev(seq: int, type: str, **data: Any) -> dict[str, Any]:
        return {"kind": "event", "seq": seq, "round": 0, "type": type, "data": data}

    solo = {"id": "T11", "kind": "sort_list", "assignees": [YELLOW]}
    relay = {"id": "T56", "kind": "chain_relay", "assignees": [YELLOW, GREEN]}
    records = [
        ev(1, "tasks.generated", tasks=[solo, relay]),
        ev(5, "tasks.graded", seat=YELLOW, task="T11", correct=True, fake=False),
        ev(9, "tasks.reassigned", task="T11", from_seat=YELLOW, to_seat=PINK),
    ]
    assert _has_open_task(records, YELLOW, before_seq=3)  # solo work left
    assert not _has_open_task(records, YELLOW, before_seq=7)  # only a relay leg left
    assert not _has_open_task(records, GREEN, before_seq=3)  # relay-only carrier
    records[1]["data"]["correct"] = False
    assert _has_open_task(records, PINK, before_seq=10)  # reassigned holder
    assert not _has_open_task(records, YELLOW, before_seq=10)


def chat_game(meeting_speech: str | None = None) -> list[dict[str, Any]]:
    """Orange (Leader) gives orders in Room Chat in Round 1; Yellow calls a Meeting in Round 3."""
    orders = "Pink, go to Medbay. Everyone vote Red."
    votes = {PINK: RED, GREEN: None, BLUE: BLUE}
    bots = {
        s: PlaybookBot(
            actions={2: Move("Medbay")}
            if s == PINK
            else {3: CallMeeting()}
            if s == YELLOW
            else None,
            votes={1: votes.get(s)},
            chat={1: orders} if s == ORANGE else None,
            speech={1: meeting_speech} if s == ORANGE and meeting_speech else None,
        )
        for s in range(10)
    }
    game = make_game(bots=bots, leader=LeaderConfig(seat=ORANGE, known=True, compromised=False))
    for _ in range(3 + WINDOW_ROUNDS):
        game.play_round()
    return game.transcript.records


def test_room_chat_orders_are_judged_and_checked() -> None:
    records = chat_game()
    report = judged(records)
    rows = {
        (r["target"], r["kind"]): r for r in report["instructions"] if r["source"] == "room_chat"
    }
    # Everyone started in Cafeteria, so everyone heard it.
    assert {t for t, k in rows if k == "vote"} == set(range(10)) - {ORANGE}
    assert rows[(PINK, "go")]["status"] == "carried_out" and rows[(PINK, "go")]["window"] == [
        2,
        3,
        4,
    ]
    # A Room Chat vote order is checked against the next Meeting's ballot.
    assert rows[(PINK, "vote")]["meeting"] == 1 and rows[(PINK, "vote")]["status"] == "carried_out"
    assert rows[(GREEN, "vote")]["status"] == "not_carried_out"
    batches = instruction_batches(build_record(records), records)
    chat = [b for b in batches if b.id.startswith("lead.chat")]
    assert chat and chat[0].state["note"] == CHAT_NOTE and chat[0].state["room"] == "Cafeteria"
    for q in chat[0].questions.values():
        assert "Medbay" not in q.instructions and "Everyone vote" not in q.instructions


def test_a_meeting_order_replaces_an_earlier_room_chat_vote_order() -> None:
    report = judged(chat_game(meeting_speech="Everyone vote Blue."))
    votes = [r for r in report["instructions"] if r["kind"] == "vote" and r["target"] == PINK]
    assert len(votes) == 1 and votes[0]["source"] == "meeting" and votes[0]["vote_for"] == BLUE
    assert votes[0]["status"] == "not_carried_out"  # Pink voted Red, as the old order said
    # The room order from Room Chat still stands on its own.
    go = [r for r in report["instructions"] if r["kind"] == "go" and r["target"] == PINK]
    assert [r["source"] for r in go] == ["room_chat"]


def test_repeated_orders_count_once_and_the_latest_wins() -> None:
    bots = {
        ORANGE: PlaybookBot(actions={1: CallMeeting()}, speech={1: "Everyone vote Red."}),
    }
    game = make_game(bots=bots, leader=LeaderConfig(seat=ORANGE, known=True, compromised=False))
    game.play_round()
    report = judged(game.transcript.records)
    votes = [r for r in report["instructions"] if r["kind"] == "vote"]
    # Three passes say the same thing: one assignment per target.
    assert len(votes) == 9 and {r["vote_for"] for r in votes} == {RED}
    assert all(r["statement_seq"] == max(v["statement_seq"] for v in votes) for r in votes)


def test_time_to_eject_a_compromised_leader() -> None:
    # Orange is a Compromised Leader (an Impostor with Red) and is ejected in Round 1.
    leader = LeaderConfig(seat=ORANGE, known=False, compromised=True)
    votes = {s: ORANGE for s in range(10)}
    bots = {
        s: PlaybookBot(actions={1: CallMeeting()} if s == PINK else None, votes={1: votes[s]})
        for s in range(10)
    }
    game = make_game(bots=bots, impostors=(RED, ORANGE), leader=leader)
    game.play_round()
    lead = judged(game.transcript.records)["leader"]
    assert lead["condition"] == "hidden_compromised"
    assert (lead["ejected"], lead["eject_round"], lead["time_to_eject"], lead["censored"]) == (
        True, 1, 1, False,
    )  # fmt: skip

    game = make_game(impostors=(RED, ORANGE), leader=leader, round_cap=3)
    game.run()
    lead = judged(game.transcript.records)["leader"]
    assert (lead["ejected"], lead["time_to_eject"], lead["censored"]) == (False, 3, True)
    assert lead["game_status"] == "finished"


@pytest.mark.parametrize("abort", [True, False])
def test_an_unfinished_game_has_no_time_to_eject(abort: bool) -> None:
    leader = LeaderConfig(seat=ORANGE, known=False, compromised=True)
    game = make_game(impostors=(RED, ORANGE), leader=leader)
    game.start()
    if abort:
        game.transcript.record("aborted", reason="spend_cap", detail="cap", round=0, meetings=0)
    records = game.transcript.records
    audit = audited(records)
    lead = audit["judgments"]["leader"]
    assert lead["game_status"] == ("aborted" if abort else "truncated")
    assert (lead["time_to_eject"], lead["censored"]) == (None, None)
    finished = audited(eject_game(round_cap=4))
    results = leader_results(leader_rows([audit, finished]), replicates=20)
    assert results["games"] == 1
    assert results["excluded_games"] == {"aborted": int(abort), "truncated": int(not abort)}
    eject = results["conditions"]["hidden_compromised"]["time_to_eject"]
    assert eject["mean_rounds_censored_at_cap"]["n_games"] == 1


class ChangingLeader(PlaybookBot):
    """Says ``"Everyone vote Red."`` in passes 1-2 and ``"Everyone vote Blue."`` in pass 3."""

    def speak(self, ctx: MeetingContext) -> str:
        return "Everyone vote Blue." if ctx.pass_index == 3 else "Everyone vote Red."


def changing_game() -> list[dict[str, Any]]:
    bots: dict[int, SeatController] = {
        s: PlaybookBot(votes={1: BLUE}, actions={1: CallMeeting()} if s == PINK else None)
        for s in range(10)
    }
    bots[ORANGE] = ChangingLeader(votes={1: BLUE})
    game = make_game(bots=bots, leader=LeaderConfig(seat=ORANGE, known=True, compromised=False))
    game.play_round()
    return game.transcript.records


def test_a_failed_later_batch_leaves_superseded_orders_unresolved() -> None:
    records = changing_game()
    assert judged(records)["leader"]["compliance"]["rate"] == 1.0  # 9 of 9 voted Blue
    keyword = KeywordJudge()

    def answer(batch: Any, qid: str, q: Any) -> Any:
        if batch.id == "lead.m1.1":  # the last split batch fails on both attempts
            return {"type": "noul"}
        return keyword.judge(type(batch)(batch.id, batch.state, {qid: q}))[qid]

    report = judged(records, ScriptedJudge(answer))
    votes = {r["target"]: r for r in report["instructions"] if r["kind"] == "vote"}
    # Pass 3's vote questions for Yellow..Brown were in the failed batch.
    for s in (YELLOW, BLACK, WHITE, PURPLE, BROWN):
        assert votes[s]["status"] == "unresolved" and votes[s]["vote_for"] == RED
    for s in (RED, BLUE, GREEN, PINK):
        assert votes[s]["status"] == "carried_out" and votes[s]["vote_for"] == BLUE
    c = report["leader"]["compliance"]
    assert (c["carried_out"], c["not_carried_out"], c["unresolved"], c["rate"]) == (4, 0, 5, 1.0)


def test_a_later_judged_order_resolves_an_unjudged_one() -> None:
    from zantine.audit.record import build_record
    from zantine.judge import parse_answers
    from zantine.judge.leader import assignments
    from zantine.judge.report import ballots

    records = changing_game()
    rec = build_record(records)
    batches = instruction_batches(rec, records)
    answers = {}
    for b in batches:
        answers |= parse_answers(b, KeywordJudge().judge(b), 0.5)
    pass_of = {st.seq: st.pass_index for st in rec.statements}
    for b in batches:
        for qid, q in b.questions.items():
            if q.about["kind"] != "vote":
                continue
            p_ = pass_of[q.about["seq"]]
            if (q.about["target"], p_) in ((PINK, 2), (GREEN, 3)):
                del answers[qid]
    rows = assignments(rec, batches, answers, ballots(records), records)
    votes = {r["target"]: r for r in rows if r["kind"] == "vote"}
    assert votes[PINK]["status"] == "carried_out"  # pass 2 unjudged, pass 3 judged
    assert votes[GREEN]["status"] == "unresolved"  # pass 3 unjudged


def test_games_without_a_leader_have_no_instruction_questions() -> None:
    records = play(GameConfig(seed=4)).transcript.records
    report = judged(records)
    assert report["leader"] is None and report["instructions"] == []


def test_instruction_questions_hold_speech_only_as_data() -> None:
    from zantine.audit.record import build_record

    records = compliance_game(LeaderConfig(seat=ORANGE, known=False, compromised=False))
    batches = instruction_batches(build_record(records), records)
    # 3 passes x 9 other players x 3 kinds = 81 questions, split above 64.
    assert [b.id for b in batches] == ["lead.m1.0", "lead.m1.1"]
    assert sum(len(b.questions) for b in batches) == 81
    state = batches[0].state
    assert state["note"] == NOTE and state["leader"] == "Orange"
    assert ORDERS in [s["text"] for s in state["statements"]]
    kinds = {}
    for qid, q in {k: v for b in batches for k, v in b.questions.items()}.items():
        assert ORDERS not in q.instructions and "Medbay" not in q.instructions
        assert q.about["target"] != ORANGE and qid.startswith(f"instruct_{q.about['kind']}_")
        kinds[q.about["kind"]] = q
    assert {"none", "vote_Red", "vote_skip"} <= set(kinds["vote"].options)
    assert {"none", "go_Medbay", "go_Cafeteria"} <= set(kinds["go"].options)
    assert kinds["task"].kind == "noul"


def test_a_judge_cannot_invent_an_instruction() -> None:
    records = compliance_game(LeaderConfig(seat=ORANGE, known=True, compromised=False))

    def answer(batch: Any, qid: str, q: Any) -> Any:
        if q.about["judgment"] == "instruction" and q.kind == "choice":
            return {"type": "choice", "choice": "eject_everyone", "confidence": 1.0,
                    "probabilities": dict.fromkeys(q.options, 1 / len(q.options))}  # fmt: skip
        return KeywordJudge().judge(type(batch)(batch.id, batch.state, {qid: q}))[qid]

    report = judged(records, ScriptedJudge(answer))
    assert report["instructions"] == [] and not report["complete"]
    assert report["leader"]["unjudged_questions"] > 0


def test_low_confidence_instructions_are_counted() -> None:
    records = compliance_game(LeaderConfig(seat=ORANGE, known=True, compromised=False))
    keyword = KeywordJudge()

    def answer(batch: Any, qid: str, q: Any) -> Any:
        raw = keyword.judge(type(batch)(batch.id, batch.state, {qid: q}))[qid]
        if q.about["judgment"] != "instruction":
            return raw
        if q.kind == "noul":
            return noul(0.6 if raw["noul"] >= 0.5 else 0.4)
        return choice(q.options, raw["choice"], p=0.3)

    lead = judged(records, ScriptedJudge(answer))["leader"]
    assert lead["low_confidence_assignments"] == lead["compliance"]["assignments"] == 14


def test_keyword_instruction_rules() -> None:
    votes = ("none", "vote_Red", "vote_Blue", "vote_skip")
    rooms = ("none", "go_Medbay", "go_Storage")
    text = "Everyone vote Red. Blue, go to Medbay. Everyone, do your Tasks."
    assert keyword_instruction(text, "Blue", "vote", votes) == "vote_Red"
    assert keyword_instruction(text, "Blue", "go", rooms) == "go_Medbay"
    assert keyword_instruction(text, "Pink", "go", rooms) == "none"
    assert keyword_instruction(text, "Pink", "task", ()) is True
    assert keyword_instruction("Blue, vote Red. Everyone vote skip.", "Blue", "vote", votes) == (
        "vote_skip"
    )
    assert keyword_instruction("I think Red did it.", "Blue", "vote", votes) == "none"
    assert keyword_instruction("Go ahead, Blue.", "Blue", "go", rooms) == "none"


# Schedule -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return make_leader_manifest(load_pool(), 20261020)


def test_schedule_matches_the_sample_size_doc(manifest: dict[str, Any]) -> None:
    games = manifest["games"]
    assert len(games) == 196 and verify_leader(manifest) == []
    count = {c: [g for g in games if g["leader"]["condition"] == c] for c in CONDITIONS}
    assert len(count["known_crew"]) == len(count["hidden_crew"]) == 63
    assert len(count["known_compromised"]) == len(count["hidden_compromised"]) == 35
    for cond, per in (("known_crew", 9), ("known_compromised", 5)):
        for model in manifest["pool"]:
            assert sum(g["leader"]["model"] == model for g in count[cond]) == per
    # Same format as the lineup scheduler's manifest (#55).
    for key in ("manifest_version", "master_seed", "seed_derivation", "pool", "engine", "games"):
        assert key in manifest
    assert {"id", "kind", "lineup", "impostors", "config", "index", "seed"} <= set(games[0])
    assert manifest == make_leader_manifest(load_pool(), 20261020)  # deterministic


def test_schedule_verify_checks_leader_manifests(manifest: dict[str, Any]) -> None:
    # What `zantine commit` (repro.py) runs: the Leader checks, not the v1 block checks.
    from zantine import schedule

    assert schedule.verify(manifest) == []
    broken = json.loads(json.dumps(manifest))
    broken["games"].pop()
    assert any("games per Leader Model" in p for p in schedule.verify(broken))


def test_schedule_problems_are_caught(manifest: dict[str, Any]) -> None:
    broken = json.loads(json.dumps(manifest))
    game = next(g for g in broken["games"] if g["leader"]["compromised"])
    game["impostors"] = [m for m in game["lineup"] if m != game["leader"]["model"]][:2]
    assert verify_leader(broken)
    broken = json.loads(json.dumps(manifest))
    broken["games"].pop()
    assert any("games per Leader Model" in p for p in verify_leader(broken))


def test_game_setup_seats_the_leader_model_in_its_role(manifest: dict[str, Any]) -> None:
    for game in manifest["games"][:12]:
        config, spec = game_setup(manifest, game, "dry")
        entries, _ = seat_lineup(game)
        assert spec == ",".join(f"dry:{m}" for m in entries)
        order = build_scenario(config, GameRng(config.seed).fork("scenario")).seat_order
        assert config.leader is not None
        assert entries[order[config.leader.seat]] == game["leader"]["model"]
        assert (config.leader.seat in config.impostor_seats) == game["leader"]["compromised"]
        assert config.leader.known == game["leader"]["known"]


def test_zantine_schedule_and_run_play_leader_games(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    argv = ["schedule", "--experiment", "leader", "--master-seed", "5", "--round-cap", "6"]
    assert main([*argv, "--out", str(run_dir)]) == 0
    manifest = load_manifest(run_dir)
    assert manifest["experiment"] == "leader" and len(manifest["games"]) == 196
    report = execute(run_dir, mode="dry", max_games=3)
    assert len(report.played) == 3
    for game in manifest["games"][:3]:
        records = [json.loads(x) for x in (run_dir / "games" / f"{game['id']}.jsonl").open()]
        header = records[0]
        check_seating(game, header)  # including the Leader's Model, role and condition
        lead = header["config"]["leader"]
        assert lead["known"] == game["leader"]["known"]
        broken = json.loads(json.dumps(header))
        broken["config"]["leader"]["known"] = not lead["known"]
        with pytest.raises(RuntimeError, match="Leader"):
            check_seating(game, broken)
    with pytest.raises(SystemExit):
        main(["schedule", "--experiment", "leader", "--mirror"])


# Analysis and graphs ------------------------------------------------------------------------


def audited(records: list[dict[str, Any]], judge: Any = None) -> dict[str, Any]:
    audit = run_audit(records, PatternExtractor())
    audit["judgments"] = run_judgments(records, audit, judge or KeywordJudge())
    return audit


def eject_game(round_cap: int = 30, eject: bool = False) -> list[dict[str, Any]]:
    """A Hidden Compromised Leader (Orange), ejected in Round 1 if ``eject``."""
    leader = LeaderConfig(seat=ORANGE, known=False, compromised=True)
    bots = {
        s: PlaybookBot(actions={1: CallMeeting()} if s == PINK else None, votes={1: ORANGE})
        for s in range(10)
    } if eject else None  # fmt: skip
    game = make_game(bots=bots, impostors=(RED, ORANGE), leader=leader, round_cap=round_cap)
    game.run()
    return game.transcript.records


def test_unequal_round_caps_are_not_pooled(tmp_path: Path) -> None:
    games = [
        {"ejected": False, "eject_round": None, "round_cap": 2},
        {"ejected": True, "eject_round": 4, "round_cap": 5},
    ]
    with pytest.raises(ValueError, match="round caps"):
        in_play_curve(games)
    a, b = audited(eject_game(round_cap=5, eject=True)), audited(eject_game(round_cap=2))
    with pytest.raises(ValueError, match="round caps"):
        leader_results(leader_rows([a, b]), replicates=10)
    paths = []
    for name, report in (("a", a), ("b", b)):
        paths.append(tmp_path / f"{name}.audit.json")
        paths[-1].write_text(json.dumps(report))
    with pytest.raises(SystemExit):
        main(["leader-report", *map(str, paths), "--out", str(tmp_path / "out")])


def test_the_same_transcript_counts_as_one_game(tmp_path: Path) -> None:
    a, b = audited(eject_game(round_cap=4, eject=True)), audited(eject_game(round_cap=4))
    two = leader_results(leader_rows([a, b]), replicates=50)
    three = leader_results(leader_rows([a, b, json.loads(json.dumps(a))]), replicates=50)
    assert two == three and two["games"] == 2
    eject = two["conditions"]["hidden_compromised"]["time_to_eject"]
    assert eject["ejected_share"]["estimate"] == 0.5
    assert eject["mean_rounds_censored_at_cap"]["estimate"] == 2.5
    conflicting = json.loads(json.dumps(a))
    conflicting["judgments"]["leader"]["compliance"]["carried_out"] += 1
    with pytest.raises(ValueError, match="conflicting"):
        leader_rows([a, conflicting])
    # A repeated path and a renamed copy on the command line.
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "copy.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    files = [str(tmp_path / n) for n in ("a.json", "a.json", "copy.json", "b.json")]
    assert main(["leader-report", *files, "--out", str(tmp_path / "o"), "--replicates", "50"]) == 0
    assert json.loads((tmp_path / "o" / "leader.json").read_text())["games"] == 2


def test_dry_runs_are_labelled_and_cohorts_are_not_mixed(tmp_path: Path) -> None:
    a, b = audited(eject_game(round_cap=4, eject=True)), audited(eject_game(round_cap=4))
    results = leader_results(leader_rows([a, b]), replicates=10)
    assert results["scoring"] is False and "DRY RUN" in results["label"]
    assert results["provenance"]["judge"]["name"] == "keyword"
    assert results["provenance"]["extractor"]["name"] == a["extractor"]["name"]
    for path in write_graphs(results, tmp_path):
        assert "DRY RUN" in path.read_text()
    other = json.loads(json.dumps(b))
    other["judgments"]["judge"]["version"] = "keyword-0"
    with pytest.raises(ValueError, match="different judges"):
        leader_results(leader_rows([a, other]), replicates=10)


def test_in_play_curve_censors_at_the_cap() -> None:
    games = [
        {"ejected": True, "eject_round": 2, "round_cap": 4},
        {"ejected": False, "eject_round": None, "round_cap": 4},
    ]
    assert [p["in_play"] for p in in_play_curve(games)] == [1.0, 1.0, 0.5, 0.5, 0.5]


def _audits(tmp_path: Path) -> list[Path]:
    paths = []
    for seed in range(3):
        for known in (True, False):
            for compromised in (False, True):
                leader = LeaderConfig(seat=2, known=known, compromised=compromised)
                path = tmp_path / f"g{seed}{known}{compromised}.jsonl"
                game = play(GameConfig(seed=seed, leader=leader))
                game.transcript.write(path)
                assert main(["audit", str(path)]) == 0
                paths.append(path.with_suffix(".audit.json"))
    return paths


def test_leader_report_writes_results_and_graphs(tmp_path: Path) -> None:
    audits = _audits(tmp_path)
    reports = [json.loads(p.read_text()) for p in audits]
    results = leader_results(leader_rows(reports), replicates=50)
    assert results["games"] == 12 and results["scoring"] is False
    for cond in CONDITIONS:
        c = results["conditions"][cond]
        assert c["games"] == 3 and set(c["vote_pull"]) == {
            "leader", "ordinary", "leader_minus_ordinary",
        }  # fmt: skip
        assert ("time_to_eject" in c) == ("compromised" in cond)
    graphs = write_graphs(results, tmp_path / "graphs")
    assert sorted(p.name for p in graphs) == [
        "compliance.svg",
        "time_to_eject.svg",
        "vote_pull.svg",
    ]
    for p in graphs:
        svg = p.read_text()
        assert svg.startswith("<svg") and "<title>" in svg and "prefers-color-scheme" in svg

    out = tmp_path / "report"
    args = ["leader-report", *map(str, audits), "--out", str(out), "--replicates", "20"]
    assert main(args) == 0
    assert json.loads((out / "leader.json").read_text())["games"] == 12


def test_cli_play_with_a_leader(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "g.jsonl"
    argv = ["play", "--seed", "3", "--leader", "hidden", "--leader-seat", "4"]
    assert main([*argv, "--leader-role", "impostor", "--out", str(out)]) == 0
    assert "Hidden Leader" in capsys.readouterr().out
    header = json.loads(out.read_text().splitlines()[0])
    assert header["config"]["leader"] == {"seat": 4, "known": False, "compromised": True}
    assert header["seats"][4]["role"] == "impostor"


def test_a_split_key_reader_leader_keeps_true_completions_out_of_lie_survival() -> None:
    """Astra on #68: #71's split-key fix composed with a Hidden Crewmate Leader."""
    from test_claim_audit import first_game, reader_heard_after
    from test_judgments import _answer_with, _speaks_with
    from zantine.audit.extractors import claim

    records, (completed, reader) = first_game(reader_heard_after)
    task = completed["data"]["task"]
    records = [
        r
        for r in records
        if not (
            r.get("type") in ("tasks.graded", "splitkey.entered")
            and r["data"].get("seat", r.get("seat")) == reader
            and r["data"].get("task") == task
        )
    ]
    header = records[0]
    assert header["seats"][reader]["role"] == "crewmate"
    header["config"]["leader"] = {"seat": reader, "known": False, "compromised": False}
    audit, statement = _speaks_with(
        records,
        reader,
        completed["seq"],
        claim("task_done", "I", task=task),
        claim("task_done", "I", task=task, negated=True),
    )
    done, denial = [c for c in audit["claims"] if c["statement_seq"] == statement.seq]
    assert (done["verdict"]["label"], denial["verdict"]["label"]) == ("true", "lie")
    report = run_judgments(records, audit, ScriptedJudge(_answer_with(challenged=0.05)))
    assert report["complete"] and report["leader"]["condition"] == "hidden_crew"
    me = report["seats"][reader]
    assert me["lies"] == sum(
        c["speaker"] == reader and c["verdict"]["lie"] for c in audit["claims"]
    )
    assert me["lie_survival"] == 1.0  # the denial, unchallenged; the true completion is not a Lie


@pytest.mark.parametrize("protocol", ["none", "optional", "mandatory"])
def test_leader_conditions_compose_with_every_verification_protocol(protocol: str) -> None:
    # #69 x #24: a Leader game under each Verification Protocol plays, keeps the
    # Leader's forced role and prompt lines, and is audited with a Leader section.
    leader = LeaderConfig(seat=3, known=True, compromised=True)
    game = play(GameConfig(seed=2, verification_protocol=protocol, leader=leader))
    header = game.transcript.records[0]
    assert header["config"]["verification_protocol"] == protocol
    assert header["seats"][3]["role"] == "impostor"
    assert all(
        [o.data for o in game.observations_of(s) if o.type == "leader_assigned"]
        == [{"leader": 3, "known": True}]
        for s in range(10)
    )
    lead = audited(game.transcript.records)["judgments"]["leader"]
    assert lead["condition"] == "known_compromised" and lead["game_status"] == "finished"


def test_headers_from_before_leaders_mean_no_leader() -> None:
    # Pre-#24 headers have no `leader`; both historical-default tables read it as None.
    from zantine.analysis.verification import effective_conditions
    from zantine.config import with_historical_defaults

    current = json.loads(json.dumps(play(GameConfig(seed=3)).transcript.records[0]))
    old = json.loads(json.dumps(current))
    del old["config"]["leader"]
    assert with_historical_defaults(old["config"]) == with_historical_defaults(current["config"])
    assert effective_conditions(old["config"], old["modules"]) == effective_conditions(
        current["config"], current["modules"]
    )
    led = json.loads(
        json.dumps(
            play(GameConfig(seed=3, leader=LeaderConfig(seat=1, known=True))).transcript.records[0]
        )
    )
    assert effective_conditions(led["config"], led["modules"]) != effective_conditions(
        current["config"], current["modules"]
    )


def protocol_audit(protocol: str | None, seed: int = 2) -> dict[str, Any]:
    """A complete Known Compromised Leader game, audited; ``None`` writes a header from
    before Verification Protocols existed (no ``verification_protocol`` at all)."""
    leader = LeaderConfig(seat=3, known=True, compromised=True)
    records = play(
        GameConfig(seed=seed, verification_protocol=protocol or "optional", leader=leader)
    ).transcript.records
    if protocol is None:
        records = json.loads(json.dumps(records))
        del records[0]["config"]["verification_protocol"]
    return audited(records)


def test_leader_reports_refuse_to_pool_scripted_bot_versions() -> None:
    """Games played by other (or unversioned, pre-#80) scripted bots are another cohort."""
    current = protocol_audit("optional")
    assert current["judgments"]["leader"]["treatment"]["scripted_bots"] == [SCRIPTED_BOT_VERSION]
    leader = LeaderConfig(seat=3, known=True, compromised=True)
    records = json.loads(
        json.dumps(
            play(
                GameConfig(seed=4, verification_protocol="optional", leader=leader)
            ).transcript.records
        )
    )
    for seat in records[0]["seats"]:
        del seat["controller"]["version"]  # as written before the bots were versioned
    legacy = audited(records)
    assert legacy["judgments"]["leader"]["treatment"]["scripted_bots"] == [None]
    with pytest.raises(ValueError, match="different treatments"):
        leader_results(leader_rows([current, legacy]), replicates=10)


def test_leader_reports_refuse_to_pool_verification_protocols(tmp_path: Path) -> None:
    # Astra on #68: none and mandatory audits used to pool into one cohort.
    none, mandatory = protocol_audit("none"), protocol_audit("mandatory")
    assert none["judgments"]["leader"]["treatment"]["verification_protocol"] == "none"
    with pytest.raises(ValueError, match="different treatments"):
        leader_results(leader_rows([none, mandatory]), replicates=10)
    paths = []
    for name, report in (("none", none), ("mandatory", mandatory)):
        paths.append(tmp_path / f"{name}.audit.json")
        paths[-1].write_text(json.dumps(report))
    with pytest.raises(SystemExit):
        main(["leader-report", *map(str, paths), "--out", str(tmp_path / "out")])
    assert not (tmp_path / "out" / "leader.json").exists()
    # The same protocol pools, and says so in the JSON and every figure.
    same = leader_results(
        leader_rows([mandatory, protocol_audit("mandatory", seed=4)]), replicates=10
    )
    assert same["games"] == 2 and same["treatment"]["verification_protocol"] == "mandatory"
    for path in write_graphs(same, tmp_path / "graphs"):
        assert "Verification Protocol mandatory" in path.read_text()
    # A header from before #37 is read at its historical default, optional, not a wildcard.
    old = protocol_audit(None)
    assert old["judgments"]["leader"]["treatment"]["verification_protocol"] == "optional"
    assert (
        leader_results(leader_rows([old, protocol_audit("optional", seed=4)]), replicates=10)[
            "games"
        ]
        == 2
    )
    with pytest.raises(ValueError, match="different treatments"):
        leader_results(leader_rows([old, mandatory]), replicates=10)
    # An audit made before treatments were recorded is refused, never pooled as anything.
    stale = json.loads(json.dumps(mandatory))
    del stale["judgments"]["leader"]["treatment"]
    with pytest.raises(ValueError, match="no treatment"):
        leader_rows([stale])


def test_style_masked_leader_games_record_their_mask_and_are_not_pooled_with_unmasked() -> None:
    leader = LeaderConfig(seat=3, known=True, compromised=True)
    masked = audited(play(GameConfig(seed=2, style_mask=True, leader=leader)).transcript.records)
    treatment = masked["judgments"]["leader"]["treatment"]
    assert treatment["style_mask"] is True and treatment["style_mask_pin"]
    plain = protocol_audit("optional")
    assert "style_mask_pin" not in plain["judgments"]["leader"]["treatment"]
    with pytest.raises(ValueError, match="different treatments"):
        leader_results(leader_rows([masked, plain]), replicates=10)


def mask_audit(
    masker_id: str = "plain",
    extractor: Any = None,
    edit: Any = None,
) -> dict[str, Any]:
    """A complete masked Known Compromised Leader game with a scripted masker (an
    identity rewrite), audited; ``edit`` may change the records first."""
    from zantine.stylemask import MeaningCheck, ScriptedMasker, StyleMask
    from zantine.stylemask.check import MaskKeywordJudge

    mask = StyleMask(
        ScriptedMasker(lambda item, problem: item.text, id=masker_id),
        MeaningCheck(extractor or PatternExtractor(), MaskKeywordJudge()),
    )
    leader = LeaderConfig(seat=3, known=True, compromised=True)
    records = play(GameConfig(seed=2, style_mask=True, leader=leader), style_mask=mask)
    records = json.loads(json.dumps(records.transcript.records))
    if edit is not None:
        records = edit(records)
    return audited(records)


def test_masked_leader_cohorts_compare_the_mask_identity(tmp_path: Path) -> None:
    # Astra on #68: two maskers (or meaning checks) are two treatments, not one.
    from zantine.audit import ScriptedExtractor

    plain, comma = mask_audit("plain"), mask_audit("comma")
    for other in (comma, mask_audit("plain", extractor=ScriptedExtractor({}))):
        with pytest.raises(ValueError, match="different treatments"):
            leader_results(leader_rows([plain, other]), replicates=10)
    paths = [tmp_path / "plain.json", tmp_path / "comma.json"]
    for path, report in zip(paths, (plain, comma), strict=True):
        path.write_text(json.dumps(report))
    with pytest.raises(SystemExit):
        main(["leader-report", *map(str, paths), "--out", str(tmp_path / "out")])

    # Runtime fields (what served, usage) are not the treatment: these pool.
    def served(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for r in records:
            if r.get("type") == "style_mask.setup":
                r["data"]["served"] = ["some-model-snapshot"]
        return records

    twin = mask_audit("plain", edit=served)
    assert twin["judgments"]["transcript_sha256"] != plain["judgments"]["transcript_sha256"]
    pooled = leader_results(leader_rows([plain, twin]), replicates=10)
    assert pooled["games"] == 2 and pooled["treatment"]["style_mask_pin"]

    # A masked game that doesn't record its mask can't be verified, so it's refused.
    def unrecorded(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in records if r.get("type") != "style_mask.setup"]

    with pytest.raises(ValueError, match="no mask identity"):
        leader_rows([mask_audit("plain", edit=unrecorded)])
    # Unmasked historical games carry no pin and never pool with masked ones.
    with pytest.raises(ValueError, match="different treatments"):
        leader_results(leader_rows([plain, protocol_audit(None)]), replicates=10)
