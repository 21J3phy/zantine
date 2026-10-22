"""A live Model Seat (fake client) playing a full game with scripted bots."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeClient, fake_factory, message, phase_of, polite_player, text, tool_use
from zantine.cli import main
from zantine.config import GameConfig
from zantine.harness import live as live_mod
from zantine.harness.ledger import Ledger
from zantine.harness.live import LiveDisabled, play_live
from zantine.harness.provider import ProviderError
from zantine.rng import GameRng
from zantine.scenario import build_scenario
from zantine.seats.live import HarnessSeat
from zantine.transcript import Transcript

FABLE_SPEC = "live:claude-fable-5-1,scripted*9"


def live_seat(result: Any) -> int:
    """The seed decides which seat the spec's live entry fills."""
    (seat,) = [i for i, c in enumerate(result.game.controllers) if isinstance(c, HarnessSeat)]
    return seat


def seat_of_live_entry(seed: int) -> int:
    """Where the seed seats the spec's first entry (the live one)."""
    scenario = build_scenario(GameConfig(seed=seed), GameRng(seed).fork("scenario"))
    return scenario.seat_order.index(0)


def run(
    client: FakeClient,
    cap: float = 50.0,
    seed: int = 3,
    spec: str = FABLE_SPEC,
    live_impostor: bool = False,
    **kw: Any,
):
    """Play with a fake client. ``live_impostor`` keeps the live seat alive (no one kills it)."""
    if live_impostor:
        me = seat_of_live_entry(seed)
        kw["impostor_seats"] = (me, next(s for s in range(10) if s != me))
    return play_live(
        GameConfig(seed=seed, **kw), spec, spend_cap_usd=cap, adapter_factory=fake_factory(client)
    )


def test_one_live_fable_seat_and_nine_bots_play_a_full_game() -> None:
    client = FakeClient()
    result = run(client, live_impostor=True)
    game = result.game
    assert result.aborted is None and game.state.over
    records = game.transcript.records
    assert [r["kind"] for r in records[-2:]] == ["result", "cost"]
    header = records[0]
    me = live_seat(result)
    assert header["seats"][me]["controller"] == {
        "kind": "live",
        "model": "claude-fable-5-1",
        "provider": "anthropic",
        "effort": "medium",
        "effort_setting": "medium",
        "harness": "neutral-v8",
    }
    others = [s for s in header["seats"] if s["seat"] != me]
    assert [s["controller"]["kind"] for s in others] == ["scripted"] * 9
    # Every model call is logged with tokens and cost.
    calls = [r["data"] for r in game.transcript.events("harness.call")]
    assert len(calls) == len(client.messages.calls) > 0
    for c in calls:
        assert c["seat"] == me and c["model"] == "claude-fable-5-1" and c["ok"]
        assert c["cost_usd"] > 0 and c["thinking_tokens"] == 1500
    assert {c["phase"] for c in calls} >= {"action"}
    # Results include cost per Seat.
    cost = result.cost
    assert cost["complete"] is True
    seat0 = cost["seats"][me]
    assert seat0["calls"] == len(calls)
    assert seat0["cost_usd"] == pytest.approx(sum(c["cost_usd"] for c in calls))
    assert seat0["cached_input_tokens"] == 5000 * len(calls)
    assert all(s["cost_usd"] == 0 and s["calls"] == 0 for s in cost["seats"] if s["seat"] != me)
    assert cost["game"]["cost_usd"] == pytest.approx(seat0["cost_usd"])


def test_live_seat_does_not_change_bot_randomness() -> None:
    # Same seed: the bots get the same RNG streams as in a scripted-only game.
    from zantine.runner import new_game

    result = run(FakeClient(), seed=5)
    game, me = result.game, live_seat(result)
    plain = new_game(GameConfig(seed=5), "idle," + "scripted*9")
    assert game.state.roster() == plain.state.roster()
    assert [p.role for p in game.state.players] == [p.role for p in plain.state.players]
    assert plain.controllers[me].kind == "idle"
    paths = [c.rng.path for i, c in enumerate(game.controllers) if i != me]
    assert paths == [c.rng.path for i, c in enumerate(plain.controllers) if i != me]


def test_consecutive_calls_reuse_the_cached_prefix() -> None:
    client = FakeClient()
    run(client, round_cap=12, live_impostor=True, relay_tasks=0)
    calls = client.messages.calls
    assert len({repr(c["system"]) for c in calls}) == 1
    assert len({repr(c["tools"]) for c in calls}) == 1
    previous: list[str] = []
    for c in calls:
        content = c["messages"][0]["content"]
        memory = [b["text"] for b in content[:-1]]
        assert memory[: len(previous)] == previous
        if memory:
            assert content[-2]["cache_control"] == {"type": "ephemeral"}
        previous = memory
    assert len(previous) >= 2


def garbage(params: dict) -> Any:
    return message(text("I think I'll just wait."), stop_reason="end_turn")


def test_invalid_actions_get_one_retry_then_forfeit_and_are_logged() -> None:
    result = run(FakeClient(garbage), round_cap=3)
    t, me = result.game.transcript, live_seat(result)
    actions = [r for r in t.events("action") if r["data"]["seat"] == me]
    assert len(actions) == 2 * 3
    assert all(not r["data"]["valid"] and "no tool call" in r["data"]["reason"] for r in actions)
    forfeits = [
        o for o in t.observations(me) if o["type"] == "invalid_action" and o["data"]["forfeited"]
    ]
    assert len(forfeits) == 3
    # The live seat's forfeited Rounds are counted against it in the cost record.
    seat0 = result.cost["seats"][me]
    assert seat0["invalid_actions"] >= 6 and seat0["forfeits"] >= 3


def test_retry_shows_the_reason_and_a_valid_retry_is_accepted() -> None:
    def second_time_lucky(params: dict) -> Any:
        tail = params["messages"][0]["content"][-1]["text"]
        if phase_of(params) == "action" and "Your previous answer was rejected" not in tail:
            return garbage(params)
        return polite_player(params)

    client = FakeClient(second_time_lucky)
    result = run(client, round_cap=2)
    me = live_seat(result)
    actions = [
        r["data"] for r in result.game.transcript.events("action") if r["data"]["seat"] == me
    ]
    assert [(a["attempt"], a["valid"]) for a in actions] == [(1, False), (2, True)] * 2
    retry_tail = client.messages.calls[1]["messages"][0]["content"][-1]["text"]
    assert "Your previous answer was rejected: no tool call; call one of:" in retry_tail


def test_invalid_speech_gets_one_retry_then_silence() -> None:
    def mute(params: dict) -> Any:
        if phase_of(params) == "speak":
            return garbage(params)
        if phase_of(params) == "action":
            return message(tool_use("call_meeting"))
        return message(tool_use("skip_vote"))

    result = run(FakeClient(mute), round_cap=1)
    t = result.game.transcript
    bad = [r["data"] for r in t.events("harness.invalid_speech")]
    assert bad and [b["attempt"] for b in bad[:2]] == [1, 2] and bad[1]["forfeited"]
    said = [
        r["data"]["text"]
        for r in t.events("statement")
        if r["data"]["speaker"] == live_seat(result)
    ]
    assert said and set(said) == {""}


def test_illegal_actions_are_rejected_by_the_engine_with_its_reason() -> None:
    def killer(params: dict) -> Any:
        if phase_of(params) == "action":
            return message(tool_use("kill", target=9))
        return polite_player(params)

    # Make sure the live seat (wherever the seed puts it) is a Crewmate.
    me = seat_of_live_entry(3)
    impostors = tuple(s for s in range(10) if s != me)[:2]
    result = run(FakeClient(killer), round_cap=1, impostor_seats=impostors)
    mine = [r["data"] for r in result.game.transcript.events("action") if r["data"]["seat"] == me]
    (first, second) = mine
    assert first["reason"] == "only Impostors can kill"
    assert second["attempt"] == 2 and not second["valid"]


def test_transient_api_errors_are_invalid_actions() -> None:
    def flaky(params: dict) -> Any:
        return ProviderError("overloaded", fatal=False, status=529)

    result = run(FakeClient(flaky), round_cap=2)
    assert result.aborted is None
    reasons = {r["data"]["reason"] for r in result.game.transcript.events("action")}
    assert "model API error: overloaded" in reasons
    mine = result.cost["seats"][live_seat(result)]
    assert mine["failed_calls"] == mine["calls"] > 0


def test_fatal_api_error_stops_the_run_cleanly() -> None:
    def broken(params: dict) -> Any:
        return ProviderError("AuthenticationError (401): bad key", fatal=True, status=401)

    result = run(FakeClient(broken))
    assert result.aborted == "provider_error"
    kinds = [r["kind"] for r in result.game.transcript.records]
    assert kinds[-2:] == ["aborted", "cost"] and "result" not in kinds


def test_spend_cap_stops_the_run_and_partial_results_stay_readable(tmp_path: Path) -> None:
    client = FakeClient()
    result = run(client, cap=1.5, live_impostor=True)
    assert result.aborted == "spend_cap"
    assert result.ledger.spent_usd <= 1.5
    path = tmp_path / "partial.jsonl"
    result.game.transcript.write(path)
    records = Transcript.read(path)
    kinds = Counter(r["kind"] for r in records)
    assert kinds["header"] == 1 and kinds["result"] == 0 and kinds["aborted"] == 1
    aborted, cost = records[-2], records[-1]
    assert aborted["kind"] == "aborted" and aborted["reason"] == "spend_cap"
    assert aborted["round"] >= 1 and "spend cap reached" in aborted["detail"]
    assert cost["kind"] == "cost" and cost["complete"] is False
    assert cost["seats"][live_seat(result)]["calls"] == len(client.messages.calls) > 0
    assert cost["run_spent_usd"] == pytest.approx(result.ledger.spent_usd)


def test_one_ledger_caps_spend_across_games() -> None:
    ledger = Ledger(4.0)
    factory = fake_factory(FakeClient())
    results = [
        play_live(
            GameConfig(seed=s, round_cap=5),
            FABLE_SPEC,
            spend_cap_usd=4.0,
            adapter_factory=factory,
            ledger=ledger,
        )
        for s in range(10)
    ]
    assert any(r.aborted == "spend_cap" for r in results)
    assert ledger.spent_usd <= 4.0
    per_game = sum(r.cost["game"]["cost_usd"] for r in results)
    assert per_game == pytest.approx(ledger.spent_usd)


def test_live_seats_need_the_flag_and_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZANTINE_LIVE", raising=False)
    with pytest.raises(LiveDisabled, match="--live"):
        play_live(GameConfig(), FABLE_SPEC, spend_cap_usd=1.0)
    with pytest.raises(LiveDisabled, match="ZANTINE_LIVE=1"):
        play_live(GameConfig(), FABLE_SPEC, spend_cap_usd=1.0, live=True)
    with pytest.raises(SystemExit):
        main(["play", "--bots", FABLE_SPEC])
    with pytest.raises(SystemExit):
        main(["play", "--bots", FABLE_SPEC, "--live"])
    with pytest.raises(ValueError, match="live seats run only via"):
        from zantine.runner import play

        play(GameConfig(), FABLE_SPEC)


def test_live_seat_needs_a_known_model() -> None:
    with pytest.raises(ValueError, match="unknown model"):
        run(FakeClient(), spec="live:gpt-9,scripted*9")


def test_cli_live_run_writes_partial_transcript_on_spend_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ZANTINE_LIVE", "1")
    monkeypatch.setattr(live_mod, "make_adapter", fake_factory(FakeClient()))
    out = tmp_path / "live.jsonl"
    argv = ["play", "--bots", FABLE_SPEC, "--live", "--spend-cap", "2", "--out", str(out)]
    assert main([*argv, "--quiet"]) == 1
    printed = capsys.readouterr().out
    assert "ABORTED (spend_cap)" in printed and "claude-fable-5-1: " in printed
    assert Transcript.read(out)[-1]["kind"] == "cost"
