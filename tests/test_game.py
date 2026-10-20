"""End-to-end: scripted bots play full games; the transcript is complete."""

import json
from collections import Counter
from pathlib import Path

import pytest

from zantine.cli import main
from zantine.config import GameConfig
from zantine.runner import play
from zantine.transcript import Transcript


@pytest.mark.parametrize("seed", range(20))
def test_scripted_game_runs_to_completion(seed: int) -> None:
    game = play(GameConfig(seed=seed))
    state = game.state
    assert state.over
    assert 1 <= state.round <= game.config.round_cap
    records = game.transcript.records
    assert records[0]["kind"] == "header"
    assert records[-1]["kind"] == "result"
    assert records[-1]["winner"] == state.winner.value
    assert [r["seq"] for r in records] == list(range(len(records)))
    header = records[0]
    assert header["seed"] == seed
    assert len(header["seats"]) == 10
    assert Counter(s["role"] for s in header["seats"]) == {"crewmate": 8, "impostor": 2}


def test_all_end_states_are_reachable_with_scripted_bots() -> None:
    reasons = {play(GameConfig(seed=s)).state.win_reason for s in range(40)}
    assert reasons == {"parity", "round_cap", "impostors_ejected"}


def test_same_seed_gives_identical_transcript() -> None:
    a = play(GameConfig(seed=11)).transcript.to_jsonl()
    b = play(GameConfig(seed=11)).transcript.to_jsonl()
    c = play(GameConfig(seed=12)).transcript.to_jsonl()
    assert a == b
    assert a != c


def test_transcript_records_every_action_and_every_private_observation() -> None:
    game = play(GameConfig(seed=3))
    t = game.transcript
    action_events = [r for r in t.events("action") if r["data"]["attempt"] == 1]
    by_round = Counter(r["round"] for r in action_events)
    # Every seat alive at the start of each Round submitted an action.
    alive = 10
    deaths = Counter(r["round"] for r in t.events("kill")) + Counter(
        r["round"] for r in t.events("ejection")
    )
    for rnd in range(1, game.state.round + 1):
        assert by_round[rnd] == alive
        alive -= deaths[rnd]
    for seat in range(10):
        recorded = [(r["round"], r["type"], r["data"]) for r in t.observations(seat)]
        given = [(o.round, o.type, o.data) for o in game.observations_of(seat)]
        assert recorded == given and given


def test_transcript_round_trips_through_jsonl(tmp_path: Path) -> None:
    game = play(GameConfig(seed=4))
    path = tmp_path / "game.jsonl"
    game.transcript.write(path)
    assert Transcript.read(path) == [
        json.loads(line) for line in game.transcript.to_jsonl().splitlines()
    ]


def test_cli_plays_a_game_and_writes_the_transcript(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "t.jsonl"
    assert main(["play", "--seed", "5", "--bots", "scripted*8,idle*2", "--out", str(out)]) == 0
    records = Transcript.read(out)
    assert records[-1]["kind"] == "result"
    kinds = Counter(s["controller"]["kind"] for s in records[0]["seats"])
    assert kinds == {"scripted": 8, "idle": 2}
    assert "win by" in capsys.readouterr().out


def test_cli_rejects_bad_bot_spec() -> None:
    with pytest.raises(SystemExit):
        main(["play", "--bots", "scripted*3"])
