"""Run directories: playing a schedule, seating, resume, spend cap, dry runs."""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeClient, fake_factory
from zantine import runs
from zantine.cli import main
from zantine.harness.live import LiveDisabled
from zantine.harness.pricing import load_models
from zantine.rng import GameRng
from zantine.roles import Role
from zantine.runner import new_game
from zantine.runs import (
    create_run,
    execute,
    game_setup,
    is_complete,
    load_manifest,
    read_jsonl,
    spent_usd,
    transcript_path,
)
from zantine.scenario import build_scenario
from zantine.schedule import load_pool, make_manifest, seat_lineup

POOL = load_pool()
# Prices that never expire, so these offline tests don't depend on today's date.
MODELS = {k: dataclasses.replace(v, price_valid_until=None) for k, v in load_models().items()}


def schedule(tmp_path: Path, name: str = "run", seed: int = 7, **kw: Any) -> Path:
    config = kw.pop("config", None)
    run_dir = tmp_path / name
    create_run(run_dir, make_manifest(POOL, seed, blocks=kw.pop("blocks", 1), config=config, **kw))
    return run_dir


def complete(run_dir: Path) -> list[str]:
    games = load_manifest(run_dir)["games"]
    return [g["id"] for g in games if is_complete(transcript_path(run_dir, g["id"]))]


@pytest.fixture
def plays(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Seeds of every game the run module starts, in order."""
    started: list[int] = []
    real = runs.play_live

    def counting(config: Any, *args: Any, **kwargs: Any) -> Any:
        started.append(config.seed)
        return real(config, *args, **kwargs)

    monkeypatch.setattr(runs, "play_live", counting)
    return started


# Seating --------------------------------------------------------------------------------


def test_scheduled_impostor_models_hold_the_impostor_seats() -> None:
    manifest = make_manifest(POOL, 11, blocks=4, mirror=True)
    for game in manifest["games"]:
        config, spec = game_setup(manifest, game, "dry")
        scenario = build_scenario(config, GameRng(config.seed).fork("scenario"))
        entries, _ = seat_lineup(game)
        seated = scenario.seat(entries)
        assert spec == ",".join(f"dry:{m}" for m in entries)
        roles = zip(seated, scenario.roles, strict=True)
        impostors = [m for m, role in roles if role is Role.IMPOSTOR]
        assert sorted(impostors) == sorted(game["impostors"]), game["id"]
        assert config.num_impostors == len(game["impostors"])
        assert Counter(seated) == Counter(entries)


def test_names_and_seat_order_are_reshuffled_every_game() -> None:
    manifest = make_manifest(POOL, 11, blocks=1)
    scenarios = []
    for game in manifest["games"][:10]:
        config, _ = game_setup(manifest, game, "dry")
        scenarios.append(build_scenario(config, GameRng(config.seed).fork("scenario")))
    assert len({s.seat_order for s in scenarios}) == 10
    assert len({s.names for s in scenarios}) == 10


def test_a_played_game_seats_the_schedule_in_its_header() -> None:
    manifest = make_manifest(POOL, 5, blocks=1, mirror=True)
    for game in (manifest["games"][0], manifest["games"][-1]):
        config, spec = game_setup(manifest, game, "dry")
        g = new_game(config, spec)
        g.run()
        header = g.transcript.records[0]
        assert header["seed"] == game["seed"]
        runs.check_seating(game, header)
        assert {s["controller"]["kind"] for s in header["seats"]} == {"dry"}


# Dry runs --------------------------------------------------------------------------------


def test_a_tiny_dry_run_plays_end_to_end_at_zero_cost(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / "tiny"
    assert main(["schedule", "--blocks", "1", "--master-seed", "3", "--out", str(run_dir)]) == 0
    assert "28 games" in capsys.readouterr().out
    assert main(["run", str(run_dir), "--dry-run", "--max-games", "3"]) == 0
    out = capsys.readouterr().out
    assert "3/28 games complete" in out and "$0.0000" in out
    manifest = load_manifest(run_dir)
    first = manifest["games"][:3]
    assert complete(run_dir) == [g["id"] for g in first]
    index = read_jsonl(run_dir / "index.jsonl")
    assert [(r["game"], r["status"], r["cost_usd"], r["mode"]) for r in index] == [
        (g["id"], "complete", 0.0, "dry") for g in first
    ]
    assert spent_usd(run_dir) == 0.0
    for game, line in zip(first, index, strict=True):
        records = read_jsonl(run_dir / line["transcript"])
        header = records[0]
        assert header["seed"] == game["seed"] == line["seed"]  # the seed is logged
        assert sorted(s["controller"]["model"] for s in header["seats"]) == sorted(
            seat_lineup(game)[0]
        )
        assert records[-1]["kind"] == "cost" and records[-1]["game"]["calls"] == 0


def test_dry_runs_are_reproducible(tmp_path: Path) -> None:
    a, b = schedule(tmp_path, "a"), schedule(tmp_path, "b")
    for run_dir in (a, b):
        execute(run_dir, mode="dry", max_games=2)
    assert (a / "manifest.json").read_bytes() == (b / "manifest.json").read_bytes()
    for game_id in complete(a):
        assert transcript_path(a, game_id).read_bytes() == transcript_path(b, game_id).read_bytes()


# Resume ------------------------------------------------------------------------------------


def test_resume_after_a_crash_neither_duplicates_nor_skips(
    tmp_path: Path, plays: list[int]
) -> None:
    run_dir = schedule(tmp_path, config={"round_cap": 8})
    games = load_manifest(run_dir)["games"]
    seeds = [g["seed"] for g in games]
    report = execute(run_dir, mode="dry", max_games=2)
    assert report.played == [games[0]["id"], games[1]["id"]] and not report.finished
    # Simulate a crash: game 3 left a torn transcript, a temp file is lying
    # around, and game 2's index line was never written.
    torn = transcript_path(run_dir, games[0]["id"]).read_text().splitlines()[:40]
    transcript_path(run_dir, games[2]["id"]).write_text("\n".join(torn) + '\n{"kind": "ev')
    transcript_path(run_dir, games[3]["id"]).with_suffix(".jsonl.tmp").write_text("partial")
    index_path = run_dir / "index.jsonl"
    index_path.write_text("".join(index_path.read_text().splitlines(keepends=True)[:1]))
    assert not is_complete(transcript_path(run_dir, games[2]["id"]))

    report = execute(run_dir, mode="dry", max_games=3)
    assert report.played == [g["id"] for g in games[2:5]]
    assert plays == seeds[:5]
    index = read_jsonl(index_path)
    assert [r["game"] for r in index if r["status"] == "complete"] == [g["id"] for g in games[:5]]
    assert [r.get("recovered", False) for r in index] == [False, True, False, False, False]

    report = execute(run_dir, mode="dry")
    assert report.finished and report.complete == 28
    assert sorted(plays) == sorted(seeds)  # every game played exactly once
    assert complete(run_dir) == [g["id"] for g in games]
    assert execute(run_dir, mode="dry").played == []  # nothing left to do


def test_engine_changes_refuse_to_resume(tmp_path: Path) -> None:
    run_dir = schedule(tmp_path)
    manifest = load_manifest(run_dir)
    manifest["engine"]["scenario_version"] = -1
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="scenario_version"):
        execute(run_dir, mode="dry")


def test_a_manifest_is_never_replaced(tmp_path: Path) -> None:
    run_dir = schedule(tmp_path)
    with pytest.raises(ValueError, match="already exists"):
        create_run(run_dir, make_manifest(POOL, 8, blocks=1))


def test_schedule_without_out_prints_the_manifest(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schedule", "--blocks", "2", "--mirror", "--master-seed", "1"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert len(manifest["games"]) == 2 * 28 + 42
    assert manifest["master_seed"] == 1


# Live runs (fake provider) -------------------------------------------------------------------


def live(run_dir: Path, cap: float, **kw: Any) -> runs.RunReport:
    return execute(
        run_dir,
        mode="live",
        spend_cap_usd=cap,
        adapter_factory=fake_factory(FakeClient()),
        models=MODELS,
        **kw,
    )


def test_a_live_run_stops_at_the_spend_cap_and_resumes_with_a_higher_cap(
    tmp_path: Path, plays: list[int]
) -> None:
    run_dir = schedule(tmp_path, config={"round_cap": 2})
    games = load_manifest(run_dir)["games"]
    first = games[0]["id"]

    report = live(run_dir, cap=2.0)
    assert (report.stopped, report.stopped_game, report.played) == ("spend_cap", first, [])
    assert complete(run_dir) == []
    assert (run_dir / "aborted" / f"{first}.1.jsonl").is_file()
    spent = spent_usd(run_dir)
    assert 0 < spent <= 2.0 and report.spent_usd == pytest.approx(spent)
    calls = read_jsonl(run_dir / "spend.jsonl")
    assert {(c["game"], c["attempt"]) for c in calls} == {(first, 1)}

    # The cap is for the whole run: the same cap does not buy a fresh $2.
    report = live(run_dir, cap=2.0)
    assert report.stopped == "spend_cap" and spent_usd(run_dir) <= 2.0

    report = live(run_dir, cap=1000.0, max_games=2)
    assert report.stopped is None and report.played == [first, games[1]["id"]]
    index = read_jsonl(run_dir / "index.jsonl")
    statuses = [(r["game"], r["attempt"], r["status"]) for r in index]
    assert statuses[0] == (first, 1, "aborted") and statuses[-2] == (first, 3, "complete")
    assert plays == [games[0]["seed"]] * 3 + [games[1]["seed"]]
    # Every dollar is accounted for: the per-call log, the index and the transcripts agree.
    total = spent_usd(run_dir)
    assert total == pytest.approx(sum(r["cost_usd"] for r in index))
    last = read_jsonl(transcript_path(run_dir, games[1]["id"]))[-1]
    assert last["run_spent_usd"] == pytest.approx(total)
    header = read_jsonl(transcript_path(run_dir, first))[0]
    assert {s["controller"]["kind"] for s in header["seats"]} == {"live"}
    runs.check_seating(games[0], header)


def test_live_runs_need_the_opt_ins_and_a_spend_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = schedule(tmp_path)
    monkeypatch.delenv("ZANTINE_LIVE", raising=False)
    with pytest.raises(LiveDisabled):
        execute(run_dir, mode="live", spend_cap_usd=5.0)
    with pytest.raises(LiveDisabled):
        execute(run_dir, mode="live", spend_cap_usd=5.0, live=True)
    with pytest.raises(ValueError, match="spend-cap"):
        execute(run_dir, mode="live", live=True)
    with pytest.raises(SystemExit):
        main(["run", str(run_dir), "--live"])
    assert not (run_dir / "games").exists()


def test_a_run_directory_holds_one_mode(tmp_path: Path) -> None:
    run_dir = schedule(tmp_path, config={"round_cap": 2})
    execute(run_dir, mode="dry", max_games=1)
    with pytest.raises(ValueError, match="cannot add live"):
        live(run_dir, cap=100.0)
