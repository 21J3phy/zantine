"""The agent-loop Harness (#27): a Seat in an alternative Harness, everything else fixed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeClient, fake_factory, message, phase_of, polite_player, text, tool_use
from fake_providers import calls_made, fake_adapter
from test_summarizer import meeting_heavy_game
from zantine.cli import main
from zantine.config import GameConfig
from zantine.harness import agent_loop, neutral
from zantine.harness import live as live_mod
from zantine.harness.live import live_models, play_live
from zantine.harness.pricing import load_models
from zantine.harness.provider import ToolCall
from zantine.rng import GameRng
from zantine.scenario import build_scenario
from zantine.seats.agent_loop import AgentLoopSeat
from zantine.seats.live import HarnessSeat, parse_live_arg
from zantine.transcript import Transcript

LOOP_SPEC = "live:claude-fable-5-1@agent-loop,scripted*9"
# The same Model in both Harnesses, in one game.
BOTH_SPEC = "live:claude-fable-5-1,live:claude-fable-5-1@agent-loop,scripted*8"


def tail_of(params: dict) -> str:
    return params["messages"][0]["content"][-1]["text"]


def is_loop(params: dict) -> bool:
    return any(t["name"] == "update_notes" for t in params["tools"])


def seat_of_entry(seed: int, entry: int) -> int:
    scenario = build_scenario(GameConfig(seed=seed), GameRng(seed).fork("scenario"))
    return scenario.seat_order.index(entry)


def run(client: FakeClient, spec: str = LOOP_SPEC, seed: int = 3, cap: float = 50.0, **kw: Any):
    return play_live(
        GameConfig(seed=seed, **kw), spec, spend_cap_usd=cap, adapter_factory=fake_factory(client)
    )


def actions_of(result: Any, seat: int) -> list[dict]:
    return [r["data"] for r in result.game.transcript.events("action") if r["data"]["seat"] == seat]


def seat_of(result: Any, cls: type) -> int:
    (seat,) = [i for i, c in enumerate(result.game.controllers) if type(c) is cls]
    return seat


def test_an_agent_loop_seat_plays_a_full_game() -> None:
    me = seat_of_entry(3, 0)
    client = FakeClient()
    result = run(client, impostor_seats=(me, (me + 1) % 10))
    game = result.game
    assert result.aborted is None and game.state.over
    header = game.transcript.records[0]
    assert header["seats"][me]["controller"] == {
        "kind": "live",
        "model": "claude-fable-5-1",
        "provider": "anthropic",
        "effort": "medium",
        "effort_setting": "medium",
        "harness": "agent-loop-v1",
    }
    calls = [r["data"] for r in game.transcript.events("harness.call")]
    assert len(calls) == len(client.messages.calls) > 0
    assert all(c["harness"] == "agent-loop" and c["harness_version"] == 1 for c in calls)
    assert {c["phase"] for c in calls} >= {"action", "speak", "vote"}
    # The game ran on the agent-loop prompt and tools.
    assert all(c["system"][0]["text"] == agent_loop.SYSTEM_PROMPT for c in client.messages.calls)
    assert result.cost["harnesses"] == {
        "agent-loop-v1": {"seats": [me], **{k: result.cost["game"][k] for k in result.cost["game"]}}
    }


def test_both_harnesses_log_the_same_fields_through_one_ledger() -> None:
    client = FakeClient()
    result = run(client, spec=BOTH_SPEC, round_cap=6)
    neutral_seat, loop_seat = seat_of(result, HarnessSeat), seat_of(result, AgentLoopSeat)
    calls = [r["data"] for r in result.game.transcript.events("harness.call")]
    by_seat = {s: [c for c in calls if c["seat"] == s] for s in (neutral_seat, loop_seat)}
    assert all(by_seat.values())
    assert {(c["harness"], c["harness_version"]) for c in by_seat[neutral_seat]} == {
        ("neutral", neutral.HARNESS_VERSION)
    }
    assert {(c["harness"], c["harness_version"]) for c in by_seat[loop_seat]} == {
        ("agent-loop", agent_loop.HARNESS_VERSION)
    }
    assert {frozenset(c) for c in calls} == {frozenset(calls[0])}
    # One ledger prices both, and the cost record splits them by Harness.
    assert result.ledger.spent_usd == pytest.approx(sum(c["cost_usd"] for c in calls))
    harnesses = result.cost["harnesses"]
    assert harnesses["neutral-v5"]["seats"] == [neutral_seat]
    assert harnesses["agent-loop-v1"]["seats"] == [loop_seat]
    for label, seat in (("neutral-v5", neutral_seat), ("agent-loop-v1", loop_seat)):
        assert harnesses[label]["calls"] == len(by_seat[seat])
        assert harnesses[label]["cost_usd"] == result.cost["seats"][seat]["cost_usd"]
    total = sum(h["cost_usd"] for h in harnesses.values())
    assert total == pytest.approx(result.cost["game"]["cost_usd"])


def test_the_neutral_seat_sends_exactly_what_it_would_alone() -> None:
    client = FakeClient()
    run(client, spec=BOTH_SPEC, round_cap=4)
    neutral_calls = [c for c in client.messages.calls if not is_loop(c)]
    assert neutral_calls
    assert all(c["system"][0]["text"] == neutral.SYSTEM_PROMPT for c in neutral_calls)
    assert all("Your notes" not in tail_of(c) for c in neutral_calls)


def test_the_prompt_keeps_the_neutral_rules_and_drops_only_the_condensing_note() -> None:
    assert agent_loop.SYSTEM_PROMPT.startswith(neutral.RULES_PROMPT)
    assert neutral.TOOLS_NOTE in agent_loop.SYSTEM_PROMPT
    assert neutral.CONDENSED_LOG_NOTE not in agent_loop.SYSTEM_PROMPT
    assert neutral.RESPONSE_PROMPT not in agent_loop.SYSTEM_PROMPT
    assert "0 to 2 Impostors" in agent_loop.SYSTEM_PROMPT


def test_the_log_is_complete_and_append_only() -> None:
    game, bots = meeting_heavy_game()
    contexts = bots[5].action_contexts
    previous: tuple[str, ...] = ()
    for ctx in contexts:
        memory = agent_loop.action_request(game.rules, ctx).memory
        assert memory[: len(previous)] == previous
        previous = memory
    ctx = contexts[-1]
    verbatim = neutral.memory_lines(ctx.observations, ctx.roster, summarize=False)
    # The Neutral Harness condenses this log; the agent loop sends every line as it was.
    assert neutral.memory_lines(ctx.observations, ctx.roster) != verbatim
    request = agent_loop.action_request(game.rules, ctx)
    sent = "\n".join(request.memory) + "\n" + request.tail
    at = 0
    for line in verbatim:  # every line, once each, in order
        at = sent.index(line, at) + len(line)
    assert request.memory[0].startswith(agent_loop.LOG_HEADER)


def test_notes_persist_and_are_shown_only_to_their_author() -> None:
    def note_taker(params: dict) -> Any:
        if not is_loop(params):
            return polite_player(params)
        tail = tail_of(params)
        if phase_of(params) == "action" and "notes saved" not in tail:
            return message(tool_use("update_notes", text=f"note at {len(tail)}"))
        return polite_player(params)

    client = FakeClient(note_taker)
    result = run(client, spec=BOTH_SPEC, round_cap=3)
    me = seat_of(result, AgentLoopSeat)
    events = [r["data"] for r in result.game.transcript.events("harness.notes")]
    assert len(events) == 3 and all(e["seat"] == me and e["phase"] == "action" for e in events)
    loop_calls = [c for c in client.messages.calls if is_loop(c)]
    action_calls = [c for c in loop_calls if phase_of(c) == "action"]
    # Two calls per Round: the note, then the action, which sees the saved note.
    assert len(action_calls) == 6
    assert "Your notes: (empty)" in tail_of(action_calls[0])
    assert f"Your notes:\n{events[0]['text']}" in tail_of(action_calls[1])
    assert "1. update_notes(...) -> notes saved" in tail_of(action_calls[1])
    # The next decision starts afresh but keeps the notes.
    assert "steps so far" not in tail_of(action_calls[2])
    assert f"Your notes:\n{events[0]['text']}" in tail_of(action_calls[2])
    # Both decision calls share the engine's attempt number.
    calls = [r["data"] for r in result.game.transcript.events("harness.call")]
    mine = [c for c in calls if c["seat"] == me and c["phase"] == "action"]
    assert [c["attempt"] for c in mine] == [1] * 6
    actions = actions_of(result, me)
    assert all(a["valid"] for a in actions)
    # Nobody else ever sees them.
    others = "\n".join(
        "\n".join(b["text"] for b in c["messages"][0]["content"])
        for c in client.messages.calls
        if not is_loop(c)
    )
    assert "note at" not in others


def test_notes_are_cut_at_the_limit() -> None:
    notes, result = agent_loop.new_notes(ToolCall("update_notes", {"text": "x" * 2000}))
    assert notes == "x" * agent_loop.NOTES_LIMIT and "cut to 1500" in result
    assert agent_loop.new_notes(ToolCall("update_notes", {"text": 3}))[0] is None


def test_tool_errors_go_back_to_the_model_within_the_decision() -> None:
    def confused(params: dict) -> Any:
        if phase_of(params) == "action" and "steps so far" not in tail_of(params):
            return message(tool_use("speak", text="hello?"))
        return polite_player(params)

    client = FakeClient(confused)
    result = run(client, round_cap=2)
    me = seat_of(result, AgentLoopSeat)
    second = tail_of(client.messages.calls[1])
    assert "1. speak(" in second and "rejected: 'speak' is not allowed now" in second
    assert "(3 call(s) left.)" in second
    actions = actions_of(result, me)
    # Fixed inside the loop: the engine sees one valid action per Round.
    assert [(a["attempt"], a["valid"]) for a in actions] == [(1, True)] * 2


def test_running_out_of_turns_is_an_invalid_action_then_the_engine_retry() -> None:
    def scribbler(params: dict) -> Any:
        return message(tool_use("update_notes", text="thinking..."))

    client = FakeClient(scribbler)
    result = run(client, round_cap=1)
    me = seat_of(result, AgentLoopSeat)
    actions = actions_of(result, me)
    assert [(a["attempt"], a["valid"]) for a in actions] == [(1, False), (2, False)]
    assert all("no answer within 4 model calls" in a["reason"] for a in actions)
    assert len(client.messages.calls) == 2 * agent_loop.MAX_TURNS
    assert result.cost["seats"][me]["forfeits"] == 1


def test_a_reply_without_a_tool_call_ends_the_decision() -> None:
    def chatty(params: dict) -> Any:
        if phase_of(params) == "action":
            return message(text("I'll wait."), stop_reason="end_turn")
        return polite_player(params)

    client = FakeClient(chatty)
    result = run(client, round_cap=1)
    me = seat_of(result, AgentLoopSeat)
    actions = actions_of(result, me)
    assert len(actions) == 2 and all("no tool call" in a["reason"] for a in actions)
    assert len(client.messages.calls) == 2
    retry = tail_of(client.messages.calls[1])
    assert "Your previous answer was rejected: no tool call; call one of:" in retry


def test_invalid_speech_gets_one_retry_then_silence() -> None:
    def mute(params: dict) -> Any:
        match phase_of(params):
            case "speak":
                return message(text("..."), stop_reason="end_turn")
            case "action":
                return message(tool_use("call_meeting"))
        return message(tool_use("skip_vote"))

    result = run(FakeClient(mute), round_cap=1)
    me = seat_of(result, AgentLoopSeat)
    bad = [r["data"] for r in result.game.transcript.events("harness.invalid_speech")]
    assert bad and [b["attempt"] for b in bad[:2]] == [1, 2] and bad[1]["forfeited"]
    said = [
        r["data"]["text"]
        for r in result.game.transcript.events("statement")
        if r["data"]["speaker"] == me
    ]
    assert said and set(said) == {""}


def test_same_seed_same_transcript() -> None:
    a = run(FakeClient(), spec=BOTH_SPEC, round_cap=5).game.transcript.records
    b = run(FakeClient(), spec=BOTH_SPEC, round_cap=5).game.transcript.records
    assert a == b


def test_every_pool_model_can_play_in_the_agent_loop() -> None:
    pool = load_models()
    lineup = ",".join(f"live:{m}@agent-loop" for m in sorted(pool)) + ",scripted*3"
    adapters = {}

    def factory(spec):
        adapters[spec.id] = fake_adapter(spec)
        return adapters[spec.id]

    result = play_live(
        GameConfig(seed=3, round_cap=3), lineup, spend_cap_usd=50, adapter_factory=factory
    )
    assert result.aborted is None
    assert all(calls_made(a) > 0 for a in adapters.values())
    calls = [r["data"] for r in result.game.transcript.events("harness.call")]
    assert {c["model"] for c in calls} == set(pool)
    assert all(c["harness"] == "agent-loop" and c["ok"] for c in calls)


def test_spend_cap_covers_the_loop() -> None:
    result = run(FakeClient(), cap=0.05)
    assert result.aborted == "spend_cap"
    assert result.ledger.spent_usd <= 0.05


def test_bot_spec_picks_the_harness_per_seat() -> None:
    assert parse_live_arg("claude-fable-5-1") == ("claude-fable-5-1", HarnessSeat)
    assert parse_live_arg("claude-fable-5-1@neutral") == ("claude-fable-5-1", HarnessSeat)
    assert parse_live_arg("claude-fable-5-1@agent-loop") == ("claude-fable-5-1", AgentLoopSeat)
    assert live_models(["live:a@agent-loop", "scripted", "live:b"]) == ["a", "b"]
    with pytest.raises(ValueError, match="unknown harness 'langgraph'"):
        parse_live_arg("claude-fable-5-1@langgraph")
    with pytest.raises(ValueError, match="names no Harness"):
        parse_live_arg("claude-fable-5-1@")
    with pytest.raises(ValueError, match="need a model"):
        live_models(["live:@agent-loop"])


def test_cli_records_the_harness_in_the_transcript_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZANTINE_LIVE", "1")
    monkeypatch.setattr(live_mod, "make_adapter", fake_factory(FakeClient()))
    out = tmp_path / "loop.jsonl"
    argv = ["play", "--seed", "3", "--round-cap", "2", "--bots", BOTH_SPEC, "--live"]
    assert main([*argv, "--spend-cap", "20", "--out", str(out), "--quiet"]) == 0
    records = Transcript.read(out)
    live = [s["controller"] for s in records[0]["seats"] if s["controller"]["kind"] == "live"]
    assert sorted(c["harness"] for c in live) == ["agent-loop-v1", "neutral-v5"]
    assert set(records[-1]["harnesses"]) == {"agent-loop-v1", "neutral-v5"}
