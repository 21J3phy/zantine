"""The v1.5 speed experiment (#25): per-Seat Talk Budgets, tier scheduling, analysis."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from helpers import events, make_game
from zantine import speed
from zantine.analysis.report import write_report
from zantine.analysis.speed import digest, speed_seats, tier_section
from zantine.cli import main
from zantine.config import GameConfig
from zantine.engine import Game
from zantine.harness import neutral
from zantine.rng import GameRng
from zantine.rules.actions import CallMeeting, Kill
from zantine.runs import create_run, execute, game_setup, load_manifest
from zantine.scenario import build_scenario
from zantine.schedule import load_pool, seat_lineup
from zantine.seats.bots import PlaybookBot

POOL = load_pool()
RANKING_WORDS = re.compile(
    r"\b(winners?|best|worst|top|rank(ed|ing|s)?|leaderboard|beats?)\b", re.I
)
# Distinct budgets, so a leaked number is easy to spot.
BUDGETS = (11, 23, 37, 41, 53, 67, 79, 83, 97, 101)
TIER_LABELS = tuple(f"tierlabel{seat}" for seat in range(10))


# Per-Seat Talk Budgets -------------------------------------------------------------------


def test_talk_budget_defaults_to_the_shared_budget() -> None:
    config = GameConfig(talk_budget_chars=321)
    assert [config.talk_budget(s) for s in range(10)] == [321] * 10
    config = GameConfig(talk_budgets=BUDGETS)
    assert [config.talk_budget(s) for s in range(10)] == list(BUDGETS)


@pytest.mark.parametrize(
    "kw",
    [
        {"talk_budgets": (10,) * 9},
        {"talk_budgets": (10,) * 9 + (0,)},
        {"talk_tiers": ("fast",) * 10},
        {"talk_budgets": (10,) * 10, "talk_tiers": ("fast",) * 9},
        {"talk_budget_chars": 0},
    ],
)
def test_bad_talk_budgets_are_refused(kw: dict) -> None:
    with pytest.raises(ValueError):
        GameConfig(**kw)


def _meeting_game(
    speech: dict[int, str] | None = None,
    tiers: tuple[str, ...] = TIER_LABELS,
    meeting: bool = True,
) -> tuple:
    """Round 1: an emergency Meeting (unless ``meeting`` is False) where every Seat says
    ``speech[seat]`` (default 200 characters), then votes."""
    votes = {seat: 5 for seat in range(10)}
    votes[5] = 0
    said = {seat: "x" * 200 for seat in range(10)} | (speech or {})
    bots = {seat: PlaybookBot(votes={1: v}, speech={1: said[seat]}) for seat, v in votes.items()}
    if meeting:
        bots[4].actions[1] = CallMeeting()
    game = make_game(bots=bots, talk_budgets=BUDGETS, talk_tiers=tiers)
    game.play_round()
    return game, bots


def test_each_seat_is_cut_to_its_own_budget_and_the_header_logs_tiers() -> None:
    game, _ = _meeting_game()
    for st in events(game, "statement"):
        assert len(st["text"]) == BUDGETS[st["speaker"]]
        assert st["truncated"]
    header = game.transcript.records[0]
    assert list(header["config"]["talk_budgets"]) == list(BUDGETS)
    assert list(header["config"]["talk_tiers"]) == list(TIER_LABELS)


def test_players_learn_only_their_own_budget() -> None:
    game, bots = _meeting_game()
    for seat, bot in bots.items():
        assert bot.meeting_contexts
        others = [str(b) for s, b in enumerate(BUDGETS) if s != seat]
        for ctx in bot.meeting_contexts:
            assert ctx.talk_budget_chars == BUDGETS[seat]
            seen = repr(ctx.observations) + repr(ctx.extra)
            assert "talk_budget" not in seen and "talk_tier" not in seen
            assert not any(label in seen for label in TIER_LABELS)
            if ctx.pass_index is not None:
                req = neutral.speak_request(game.rules, ctx)
                prompt = "\n".join([req.system, *req.memory, req.tail])
                assert f"at most {BUDGETS[seat]} characters" in prompt
                assert not any(label in prompt for label in TIER_LABELS)
                budgets_said = re.findall(r"at most (\d+) characters", prompt)
                assert budgets_said == [str(BUDGETS[seat])]
                assert not any(f"at most {o} " in prompt for o in others)
    for record in game.transcript.records:
        if record["kind"] == "observation":
            text = json.dumps(record["data"])
            assert not any(label in text for label in TIER_LABELS)


# Scheduling -------------------------------------------------------------------------------


def test_tier_draws_are_seeded_balanced_and_logged() -> None:
    a = speed.make_speed_manifest(POOL, 11)
    b = speed.make_speed_manifest(POOL, 11)
    c = speed.make_speed_manifest(POOL, 12)
    assert a == b
    assert [g["talk"] for g in a["games"]] != [g["talk"] for g in c["games"]]
    kinds = Counter(g["kind"] for g in a["games"])
    assert kinds == {"lobby": 42, "benign": 14, "realism": 20}  # 76 games
    assert len(a["games"]) == 76 and a["experiment"] == "speed"
    for g in a["games"]:
        if g["kind"] == "realism":
            continue
        counts = Counter(g["talk"]["tiers"])
        assert set(counts) == set(speed.TIERS) and sorted(counts.values()) in ([3, 3, 4],)
        rng = GameRng(11).fork(f"talk-tiers/{g['id']}")
        assert g["talk"]["tiers"] == speed.draw_tiers(rng)
    assert speed.verify_speed(a) == []
    # Tiers are not tied to Seats: every Seat gets every tier somewhere.
    per_seat = [
        {g["talk"]["tiers"][s] for g in a["games"] if g["kind"] != "realism"} for s in range(10)
    ]
    assert all(tiers == set(speed.TIERS) for tiers in per_seat)


def test_verify_catches_tampered_tiers() -> None:
    m = speed.make_speed_manifest(POOL, 5, blocks=1, realism_games=2)
    m["games"][0]["talk"]["tiers"] = list(reversed(m["games"][0]["talk"]["tiers"]))
    m["games"][-1]["talk"]["tier_by_model"] = dict.fromkeys(m["games"][-1]["lineup"], "fast")
    problems = speed.verify_speed(m)
    assert any("seeded draw" in p for p in problems)
    assert any("throughput" in p for p in problems)


def test_realism_tiers_follow_throughput_in_thirds() -> None:
    tps = {m: float(i) for i, m in enumerate(POOL)}  # the last Model is fastest
    tiers = speed.realism_tiers(POOL, tps)
    assert Counter(tiers.values()) == {"fast": 2, "normal": 3, "slow": 2}
    assert tiers[POOL[-1]] == tiers[POOL[-2]] == "fast"
    assert tiers[POOL[0]] == tiers[POOL[1]] == "slow"
    # Equal (placeholder) values: ties keep pool order.
    equal = speed.realism_tiers(POOL, dict.fromkeys(POOL, 1.0))
    assert [equal[m] for m in POOL] == ["fast"] * 2 + ["normal"] * 3 + ["slow"] * 2
    with pytest.raises(ValueError, match="no throughput"):
        speed.realism_tiers(POOL, {POOL[0]: 1.0})


def test_the_table_marks_placeholder_throughput() -> None:
    table = speed.load_table()
    assert table.measured is False and "placeholder" in table.source
    assert table.tiers == {"fast": 1000, "normal": 500, "slow": 250}
    assert set(table.throughput) == set(POOL)
    text = speed.TABLE.read_text("utf-8")
    assert text.count("= 1.0  # PLACEHOLDER") == len(POOL)


def test_game_setup_gives_each_seat_its_tier_budget() -> None:
    m = speed.make_speed_manifest(POOL, 9, blocks=1, realism_games=3)
    chars = m["speed"]["tiers"]
    for game in m["games"]:
        config, _ = game_setup(m, game, "dry")
        assert config.talk_tiers is not None and config.talk_budgets is not None
        assert config.talk_budgets == tuple(chars[t] for t in config.talk_tiers)
        if game["kind"] == "realism":
            entries, _ = seat_lineup(game)
            scenario = build_scenario(config, GameRng(config.seed).fork("scenario"))
            by_model = game["talk"]["tier_by_model"]
            for seat, pos in enumerate(scenario.seat_order):
                assert config.talk_tiers[seat] == by_model[entries[pos]]
        else:
            assert list(config.talk_tiers) == game["talk"]["tiers"]


def test_live_runs_refuse_placeholder_throughput(tmp_path: Path) -> None:
    m = speed.make_speed_manifest(POOL, 3, blocks=1, realism_games=1)
    create_run(tmp_path / "run", m)
    with pytest.raises(ValueError, match="placeholder throughput"):
        execute(tmp_path / "run", mode="live", spend_cap_usd=1.0, live=True)
    measured = dict(m, speed=dict(m["speed"], realism=dict(m["speed"]["realism"], measured=True)))
    speed.check_live(measured)  # no error
    speed.check_live(speed.make_speed_manifest(POOL, 3, blocks=1, realism_games=0))


def test_cli_rejects_mirror_for_speed(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["schedule", "--experiment", "speed", "--mirror", "--out", str(tmp_path / "x")])


# Analysis -------------------------------------------------------------------------------


PLANNED = {"id": "g1", "kind": "lobby", "talk": {"mode": "random"}}


def test_proxy_vote_pull_and_wrong_pull(tmp_path: Path) -> None:
    game, _ = _meeting_game()
    records = game.transcript.records
    rows = {r.seat: r for r in speed_seats(PLANNED, records, tmp_path / "g1.jsonl")}
    assert {r.pull_source for r in rows.values()} == {"proxy"}
    assert sum(r.messages for r in rows.values()) == 30
    assert rows[9].char_share == pytest.approx(3 * BUDGETS[9] / (3 * sum(BUDGETS)))
    assert rows[0].floor_share == pytest.approx(0.1)
    # Seat 2 voted for Crewmate 5: the 8 other ballots (not 2's own, not 5's) all match.
    assert (rows[2].pull_matches, rows[2].pull_ballots, rows[2].wrong_matches) == (8, 8, 8)
    assert rows[2].high_pull_wrong and rows[2].tier == "tierlabel2"
    # Seat 5 voted for Impostor 0: nobody else did; Impostor 0's own ballot is left out.
    assert (rows[5].pull_matches, rows[5].pull_ballots, rows[5].pushed_crewmate) == (0, 8, 0)
    assert rows[5].vote_pull == 0 and not rows[5].high_pull_wrong


def test_judged_positions_are_used_when_bound_to_the_transcript(tmp_path: Path) -> None:
    game, _ = _meeting_game()
    records = game.transcript.records
    path = tmp_path / "g1.jsonl"
    path.write_text(game.transcript.to_jsonl(), encoding="utf-8")
    positions = [
        {"meeting": 1, "seat": 3, "target": 0, "low_confidence": False},
        {"meeting": 1, "seat": 6, "target": "skip", "low_confidence": False},
    ]
    audit = {"judgments": _judgments(digest(records), positions)}
    path.with_suffix(".audit.json").write_text(json.dumps(audit), encoding="utf-8")
    rows = {r.seat: r for r in speed_seats(PLANNED, records, path)}
    assert rows[3].pull_source == "judged" and rows[3].pull_judge == "jev jev-1.13.0"
    # Seat 3 pushed Impostor 0; only Seat 5 voted 0 (the target's own ballot is left out).
    assert (rows[3].pull_matches, rows[3].pull_ballots) == (1, 8)
    # Seat 6 pushed skip; nobody skipped. Seats without a judged position have none.
    assert (rows[6].pull_matches, rows[6].pull_ballots) == (0, 9)
    assert rows[2].positions == 0 and rows[2].vote_pull is None

    # Made from another transcript, or not bound to any: the own-vote proxy is used.
    for bound in ("0" * 64, None, "", "missing"):
        judgments = _judgments(None, positions)
        del judgments["transcript_sha256"]
        if bound != "missing":
            judgments["transcript_sha256"] = bound
        path.with_suffix(".audit.json").write_text(
            json.dumps({"judgments": judgments}), encoding="utf-8"
        )
        rows = {r.seat: r for r in speed_seats(PLANNED, records, path)}
        assert rows[3].pull_source == "proxy", bound
        assert (rows[3].pull_matches, rows[3].pull_ballots) == (8, 8)  # voted 5, as did 8
        assert rows[2].pull_ballots == 8


SPEED_TIERS = ("fast", "normal", "slow", "fast", "normal", "slow", "fast", "normal", "slow", "fast")


def _judgments(sha: str | None, positions: list[dict], **kw: object) -> dict:
    """A complete scoring ``judgments`` section, as ``zantine audit --judge jev`` writes it."""
    return {
        "judge": {"name": "jev", "version": "jev-1.13.0", "scoring": True},
        "transcript_sha256": sha,
        "complete": True,
        "batch_errors": 0,
        "unresolved_positions": [],
        "positions": positions,
    } | kw


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"judge": {"name": "keyword", "version": "keyword-1", "scoring": False}}, "not_scoring"),
        ({"judge": None}, "not_scoring"),
        ({"complete": False, "batch_errors": 1}, "incomplete"),
        ({"unresolved_positions": [{"meeting": 1, "seat": 3}]}, "incomplete"),
        ({"complete": None}, "incomplete"),
    ],
)
def test_dry_run_or_incomplete_judgments_fall_back_to_the_labelled_proxy(
    tmp_path: Path, change: dict, reason: str
) -> None:
    game, _ = _meeting_game(tiers=SPEED_TIERS)
    records = game.transcript.records
    path = tmp_path / "g1.jsonl"
    positions = [{"meeting": 1, "seat": 3, "target": 0, "low_confidence": False}]
    audit = {"judgments": _judgments(digest(records), positions, **change)}
    path.with_suffix(".audit.json").write_text(json.dumps(audit), encoding="utf-8")
    rows = {r.seat: r for r in speed_seats(PLANNED, records, path)}
    assert rows[3].pull_source == "proxy" and rows[3].judgments_ignored == reason
    assert rows[3].pull_judge is None
    assert (rows[3].pull_matches, rows[3].pull_ballots) == (8, 8)  # own vote for 5
    section = tier_section(list(rows.values()), "random", replicates=20, seed=0)
    assert section["pull_source"] == "proxy" and section["judged_games"] == 0
    assert section["judgments_ignored"] == {reason: 1} and section["judges"] == []
    assert section["metrics"]["vote_pull"]["label"].endswith("(proxy: own votes)")


def test_default_cli_audit_does_not_turn_the_proxy_into_judged(tmp_path: Path) -> None:
    """End to end: `zantine audit` (keyword dry run by default) beside a speed game."""
    game, _ = _meeting_game(tiers=SPEED_TIERS)
    path = tmp_path / "g1.jsonl"
    path.write_text(game.transcript.to_jsonl(), encoding="utf-8")
    before = {r.seat: r for r in speed_seats(PLANNED, game.transcript.records, path)}
    assert main(["audit", str(path)]) == 0
    audit = json.loads(path.with_suffix(".audit.json").read_text())
    assert audit["judgments"]["judge"]["scoring"] is False
    assert audit["judgments"]["transcript_sha256"] == digest(game.transcript.records)
    after = {r.seat: r for r in speed_seats(PLANNED, game.transcript.records, path)}
    for seat, row in after.items():
        assert row.pull_source == "proxy" and row.judgments_ignored == "not_scoring"
        assert (row.pull_matches, row.pull_ballots) == (
            before[seat].pull_matches,
            before[seat].pull_ballots,
        )
    section = tier_section(list(after.values()), "random", replicates=20, seed=0)
    assert section["pull_source"] == "proxy"
    assert "(proxy: own votes)" in section["metrics"]["vote_pull"]["label"]

    # A complete scoring audit for the same transcript is used, and labelled judged.
    positions = [{"meeting": 1, "seat": 3, "target": 0, "low_confidence": False}]
    audit["judgments"] = _judgments(audit["judgments"]["transcript_sha256"], positions)
    path.with_suffix(".audit.json").write_text(json.dumps(audit), encoding="utf-8")
    judged = speed_seats(PLANNED, game.transcript.records, path)
    section = tier_section(judged, "random", replicates=20, seed=0)
    assert section["pull_source"] == "judged" and section["judged_games"] == 1
    assert section["judges"] == ["jev jev-1.13.0"] and section["judgments_ignored"] == {}
    assert "proxy" not in section["metrics"]["vote_pull"]["label"]


def test_silent_games_leave_speech_shares_undefined(tmp_path: Path) -> None:
    blank = {seat: "   " for seat in range(10)}
    for game, _ in (
        _meeting_game(speech=blank, tiers=SPEED_TIERS),  # everyone blank
        _meeting_game(tiers=SPEED_TIERS, meeting=False),  # no Meeting at all
    ):
        rows = speed_seats(PLANNED, game.transcript.records, tmp_path / "g1.jsonl")
        assert all(r.messages == 0 for r in rows)
        assert all(r.floor_share is None and r.char_share is None for r in rows)
        section = tier_section(rows, "random", replicates=50, seed=0)
        assert section is not None and section["games_without_speech"] == 1
        for key in ("floor_share", "char_share"):
            for est in section["metrics"][key]["per_tier"].values():
                assert est["estimate"] is None and est["n_units"] == 0 and est["n_games"] == 0

    # Some Seats silent: they hold a defined share of 0; a silent game adds nothing.
    mixed = {seat: "" for seat in (0, 3, 6)}
    spoke = speed_seats(
        PLANNED, _meeting_game(speech=mixed, tiers=SPEED_TIERS)[0].transcript.records, tmp_path
    )
    assert [r.floor_share for r in spoke if r.seat in mixed] == [0.0, 0.0, 0.0]
    assert sum(r.floor_share for r in spoke) == pytest.approx(1.0)
    silent = speed_seats(
        dict(PLANNED, id="g2"),
        _meeting_game(speech=blank, tiers=SPEED_TIERS)[0].transcript.records,
        tmp_path,
    )
    alone = tier_section(spoke, "random", replicates=50, seed=0)
    both = tier_section(spoke + silent, "random", replicates=50, seed=0)
    assert both["games"] == 2 and both["games_without_speech"] == 1
    for key in ("floor_share", "char_share"):
        for tier in speed.TIERS:
            a = alone["metrics"][key]["per_tier"][tier]
            b = both["metrics"][key]["per_tier"][tier]
            assert b["estimate"] == pytest.approx(a["estimate"])
            assert (b["n_units"], b["n_games"]) == (a["n_units"], a["n_games"])


NO_TASKS = {"tasks_per_crewmate": 0, "relay_tasks": 0, "splitkey_tasks": 0, "audit_tasks": 0}


def _chat_game(chat: dict[int, str], meeting_speech: dict[int, str] | None = None) -> Game:
    """Round 1: Room Chat by ``chat``; Seat 0 kills Seat 5 and Seat 3 forfeits, both
    while saying something. With ``meeting_speech``, Round 2 is an emergency Meeting
    where Seats speak that and everyone else stays silent."""
    bots = {seat: PlaybookBot(chat={1: text}) for seat, text in chat.items()}
    bots[0] = PlaybookBot({1: Kill(5)}, chat={1: chat.get(0, "")})
    bots[5] = PlaybookBot(chat={1: "I am about to be killed"})
    # A Crewmate trying to kill is two Invalid Actions: the Round is forfeited.
    bots[3] = PlaybookBot({1: Kill(4)}, chat={1: "forfeited speech"})
    if meeting_speech is not None:
        for seat in range(10):
            bots.setdefault(seat, PlaybookBot())
            bots[seat].speech[1] = meeting_speech.get(seat, "")
        bots[4].actions[2] = CallMeeting()
    game = make_game(bots=bots, talk_budgets=BUDGETS, talk_tiers=SPEED_TIERS, **NO_TASKS)
    game.start()
    game.state.player(0).kill_cooldown = 0
    game.play_round()
    if meeting_speech is not None:
        game.play_round()
    return game


def test_floor_share_counts_room_chat_in_a_chat_only_game(tmp_path: Path) -> None:
    # Room Chat budgets scale with the tiers: 200 * talk_budget // 500.
    chat = {2: "hello", 4: "   ", 6: "z" * 100, 8: "ok"}
    game = _chat_game(chat)
    delivered = events(game, "chat.message")
    assert {e["speaker"] for e in delivered} == {2, 6, 8}  # blank, killed, forfeited: none
    assert {e["speaker"] for e in events(game, "chat.dropped")} == {5}
    assert not events(game, "statement")
    rows = {r.seat: r for r in speed_seats(PLANNED, game.transcript.records, tmp_path)}
    budget6 = game.config.room_chat_budget(6)
    assert budget6 == 31
    assert (rows[6].messages, rows[6].chars, rows[6].truncated) == (1, budget6, 1)
    assert (rows[6].chat_messages, rows[6].chat_chars, rows[6].chat_truncated) == (1, 31, 1)
    assert (rows[2].messages, rows[2].chars, rows[2].truncated) == (1, 5, 0)
    for seat in (0, 1, 3, 4, 5, 7, 9):
        assert rows[seat].messages == 0 and rows[seat].floor_share == 0
    assert rows[2].floor_share == pytest.approx(1 / 3)
    assert rows[8].char_share == pytest.approx(2 / (5 + 31 + 2))
    section = tier_section(list(rows.values()), "random", replicates=50, seed=0)
    assert section["games_without_speech"] == 0


def test_floor_share_counts_each_delivered_message_once_across_channels(
    tmp_path: Path,
) -> None:
    game = _chat_game({2: "hello", 6: "there"}, meeting_speech={2: "a" * 50, 7: "b" * 20})
    delivered = events(game, "chat.message")
    # Heard by several players, but each delivered message is counted once.
    assert all(len(e["listeners"]) > 1 for e in delivered) and len(delivered) == 2
    statements = [e for e in events(game, "statement") if e["text"].strip()]
    spoken = len(statements)
    assert spoken > 0 and {e["speaker"] for e in statements} == {2, 7}
    rows = {r.seat: r for r in speed_seats(PLANNED, game.transcript.records, tmp_path)}
    total = spoken + 2
    assert sum(r.messages for r in rows.values()) == total
    assert rows[6].floor_share == pytest.approx(1 / total)
    assert rows[2].messages == 1 + sum(e["speaker"] == 2 for e in statements)
    assert rows[2].chat_messages == 1
    assert sum(r.floor_share for r in rows.values()) == pytest.approx(1.0)
    chars = sum(len(e["text"]) for e in statements) + len("hello") + len("there")
    assert sum(r.chars for r in rows.values()) == chars


def test_transcripts_without_room_chat_count_meeting_statements_only(tmp_path: Path) -> None:
    game, _ = _meeting_game()
    records = game.transcript.records
    assert not any(r.get("type", "").startswith("chat.") for r in records)
    rows = speed_seats(PLANNED, records, tmp_path / "g1.jsonl")
    assert all(r.chat_messages == r.chat_chars == r.chat_truncated == 0 for r in rows)
    # As before Room Chat: every Seat spoke 3 Meeting passes.
    assert [r.messages for r in rows] == [3] * 10
    assert rows[0].floor_share == pytest.approx(0.1)


def test_report_survives_estimates_without_finite_replicates(tmp_path: Path) -> None:
    """A defined estimate whose every replicate is undefined has null bounds; the
    report must still render (Astra's repro: seed 47, two lobbies, 1 replicate)."""
    run_dir = tmp_path / "sparse"
    create_run(run_dir, speed.make_speed_manifest(POOL, 47))
    execute(run_dir, mode="dry")
    keep = {"b01-lobby-00", "b01-lobby-01"}
    for path in (run_dir / "games").glob("*.jsonl"):
        if path.stem not in keep:
            path.unlink()
    out, report = write_report(run_dir, replicates=1, seed=3)
    assert (out / "metrics.json").is_file() and (out / "speed_pull.svg").is_file()
    tiers = report["speed"]["modes"]["random"]["metrics"]
    nulls = [
        e
        for m in tiers.values()
        for e in m["per_tier"].values()
        if e["estimate"] is not None and e["ci_low"] is None
    ]
    assert nulls, "the regression no longer exercises a defined point with null bounds"


def test_dry_run_speed_experiment_end_to_end(tmp_path: Path) -> None:
    run_dir = tmp_path / "speed"
    args = ["schedule", "--experiment", "speed", "--blocks", "1", "--realism-games", "4"]
    assert main([*args, "--master-seed", "4", "--out", str(run_dir)]) == 0
    manifest = load_manifest(run_dir)
    assert len(manifest["games"]) == 32
    assert main(["run", str(run_dir), "--dry-run", "--quiet"]) == 0
    for g in manifest["games"]:
        header = json.loads((run_dir / "games" / f"{g['id']}.jsonl").read_text().split("\n")[0])
        config, _ = game_setup(manifest, g, "dry")
        assert header["config"]["talk_tiers"] == list(config.talk_tiers)
        assert header["config"]["talk_budgets"] == list(config.talk_budgets)
    out, report = write_report(run_dir, replicates=100, seed=2)
    section = report["speed"]
    assert set(section["modes"]) == {"random", "realism"}
    tiers_mode = section["modes"]["random"]
    assert tiers_mode["games"] == 28 and tiers_mode["seats"] == 280
    assert tiers_mode["pull_source"] == "proxy"
    for metric in tiers_mode["metrics"].values():
        assert list(metric["per_tier"]) == list(speed.TIERS)
        assert "ci_low" in metric["fast_minus_slow"]
    assert "proxy" in tiers_mode["metrics"]["vote_pull"]["label"]
    assert tiers_mode["metrics"]["floor_share"]["per_tier"]["fast"]["estimate"] is not None
    for stem in ("speed_tiers", "speed_pull", "speed_realism"):
        assert (out / f"{stem}.svg").stat().st_size > 1000
    assert report["progress"]["planned_blocks"] == 1
    progress = (out / "progress.md").read_text("utf-8")
    assert "Talk Budget tiers" in progress and "Realism-check games complete: 4 of 4" in progress
    texts = [progress, (out / "metrics.json").read_text("utf-8")]
    texts += [p.read_text("utf-8") for p in out.glob("speed_*.svg")]
    for text in texts:
        assert not RANKING_WORDS.search(text), RANKING_WORDS.search(text)
    public = json.loads((out / "metrics.public.json").read_text("utf-8"))
    assert "seed" not in json.dumps(public["speed"]["seats"][0])
