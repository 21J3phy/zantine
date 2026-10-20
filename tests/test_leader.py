"""v1.4 Leader conditions (#24): only prompt lines differ; Compliance and time to eject."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from helpers import make_game
from zantine.audit import PatternExtractor, run_audit
from zantine.cli import main
from zantine.config import GameConfig, LeaderConfig
from zantine.harness import neutral
from zantine.harness.provider import HarnessRequest
from zantine.judge import KeywordJudge, ScriptedJudge, choice, noul, run_judgments
from zantine.judge.batches import NOTE
from zantine.judge.leader import WINDOW_ROUNDS, instruction_batches, keyword_instruction
from zantine.leader.analysis import in_play_curve, leader_results, leader_rows
from zantine.leader.bootstrap import draw_counts, ratio
from zantine.leader.graphs import write_graphs
from zantine.leader.schedule import (
    CONDITIONS,
    leader_game_setup,
    load_pool,
    make_leader_manifest,
    seat_lineup,
    verify_leader,
)
from zantine.observations import ActionContext, LeaderView, MeetingContext, Statement
from zantine.rng import GameRng
from zantine.rules import default_rules
from zantine.rules.actions import Action, CallMeeting, Kill, Move
from zantine.runner import new_game, play
from zantine.scenario import build_scenario
from zantine.seats.base import SeatController
from zantine.seats.bots import PlaybookBot, ScriptedBot

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
    return [
        (r["round"], r["type"], r["data"]) for r in game.transcript.records if r["kind"] == "event"
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
    # Tasks: Yellow never worked; Blue is an Impostor and has no Tasks.
    assert status[(YELLOW, "task")] == "not_carried_out"
    assert status[(BLUE, "task")] == "censored"
    lead = report["leader"]
    assert lead["condition"] == "known_crew" and lead["window_rounds"] == WINDOW_ROUNDS
    assert lead["compliance"] == {
        "assignments": 14,
        "carried_out": 5,
        "not_carried_out": 6,
        "censored": 3,
        "unresolved": 0,
        "rate": round(5 / 11, 6),
    }
    assert lead["compliance_by_kind"]["vote"]["rate"] == 0.5
    assert lead["crew_compliance"]["rate"] == round(5 / 9, 6)  # Red and Blue left out


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
    batches = instruction_batches(rec)
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
    batches = instruction_batches(build_record(records))
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


def test_schedule_problems_are_caught(manifest: dict[str, Any]) -> None:
    broken = json.loads(json.dumps(manifest))
    game = next(g for g in broken["games"] if g["leader"]["compromised"])
    game["impostors"] = [m for m in game["lineup"] if m != game["leader"]["model"]][:2]
    assert verify_leader(broken)
    broken = json.loads(json.dumps(manifest))
    broken["games"].pop()
    assert any("games per Leader Model" in p for p in verify_leader(broken))


def test_leader_game_setup_seats_the_leader_model_in_its_role(manifest: dict[str, Any]) -> None:
    for game in manifest["games"][:12]:
        config, spec = leader_game_setup(manifest, game, "dry")
        entries, _ = seat_lineup(game)
        assert spec == ",".join(f"dry:{m}" for m in entries)
        order = build_scenario(config, GameRng(config.seed).fork("scenario")).seat_order
        assert config.leader is not None
        assert entries[order[config.leader.seat]] == game["leader"]["model"]
        assert (config.leader.seat in config.impostor_seats) == game["leader"]["compromised"]
        assert config.leader.known == game["leader"]["known"]


# Analysis and graphs ------------------------------------------------------------------------


def test_cluster_bootstrap_ratio() -> None:
    draws = draw_counts(4, 400, GameRng(1))
    est = ratio([1, 2, 0, 3], [2, 2, 2, 4], draws).to_dict()
    assert est["estimate"] == 0.6 and est["n_games"] == 4 and est["replicates"] == 400
    assert est["ci_low"] <= est["estimate"] <= est["ci_high"]
    assert ratio([0, 0], [0, 0], draw_counts(2, 10, GameRng(1))).to_dict()["estimate"] is None


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


def test_cli_play_and_schedule(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "g.jsonl"
    argv = ["play", "--seed", "3", "--leader", "hidden", "--leader-seat", "4"]
    assert main([*argv, "--leader-role", "impostor", "--out", str(out)]) == 0
    assert "Hidden Leader" in capsys.readouterr().out
    header = json.loads(out.read_text().splitlines()[0])
    assert header["config"]["leader"] == {"seat": 4, "known": False, "compromised": True}
    assert header["seats"][4]["role"] == "impostor"
    assert main(["leader-schedule", "--seed", "1", "--out", str(tmp_path / "m.json")]) == 0
    assert len(json.loads((tmp_path / "m.json").read_text())["games"]) == 196
