"""The reasoning effort sweep (v1.6, #26): per-run effort, its mapping, schedule and report."""

from __future__ import annotations

import dataclasses
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from fake_providers import OpenAIFake, fake_adapter, openai_reply, openai_tail, polite_call
from zantine import schedule as schedule_module
from zantine.analysis.effort import TEAM, load_effort, write_effort_report
from zantine.cli import main
from zantine.config import GameConfig
from zantine.harness.ledger import Ledger
from zantine.harness.live import play_live
from zantine.harness.pricing import EFFORT_LEVELS, ModelSpec, load_models, split_effort
from zantine.harness.provider import HarnessRequest, ProviderAdapter, ToolSpec
from zantine.rng import GameRng
from zantine.runs import (
    check_seating,
    create_run,
    execute,
    game_setup,
    read_jsonl,
    transcript_path,
)
from zantine.schedule import load_pool, make_effort_manifest, make_manifest, verify
from zantine.seats.registry import make_controller

POOL = load_pool()
ALPHA = sorted(POOL)
# Prices that never expire, so these offline tests don't depend on today's date.
MODELS = {k: dataclasses.replace(v, price_valid_until=None) for k, v in load_models().items()}
TOOL = ToolSpec("wait", "Wait.", {"type": "object", "properties": {}, "required": []})
REQUEST = HarnessRequest(system="RULES", tools=(TOOL,), memory=("block 1",), tail="NOW")
RANKING_WORDS = re.compile(
    r"\b(winners?|best|worst|top|rank(ed|ing|s)?|leaderboard|beats?)\b", re.I
)
# Thinking tokens per call the effort-aware fake reports at each lab setting.
THINKING = {"low": 200, "medium": 1500, "high": 4000}


def wire_effort(adapter: ProviderAdapter) -> str:
    """The effort value in the request body the adapter would send."""
    params = adapter.build_params(REQUEST)  # type: ignore[attr-defined]
    match adapter.provider:
        case "anthropic":
            return params["output_config"]["effort"]
        case "google":
            return params["config"]["thinking_config"]["thinking_level"]
        case _:
            return params["reasoning"]["effort"]


def effort_aware_adapter(spec: ModelSpec) -> ProviderAdapter:
    """The usual fakes, except OpenAI-family Models think more at higher effort."""
    if spec.provider in ("openai", "meta"):
        adapter = fake_adapter(spec)

        def reply(p: dict[str, Any]) -> Any:
            think = THINKING[p["reasoning"]["effort"]]
            return openai_reply(
                polite_call(openai_tail(p)), output_tokens=500 + think, reasoning_tokens=think
            )

        adapter.client = OpenAIFake(reply)  # type: ignore[attr-defined]
        return adapter
    return fake_adapter(spec)


# The mapping -----------------------------------------------------------------------------


def test_seat_options_parse_and_refuse_anything_else() -> None:
    assert split_effort("grok-4.7") == ("grok-4.7", None)
    assert split_effort("grok-4.7?effort=high") == ("grok-4.7", "high")
    for bad in ("grok-4.7?effort=turbo", "grok-4.7?speed=fast", "grok-4.7?effort", "?effort=low"):
        with pytest.raises(ValueError):
            split_effort(bad)


@pytest.mark.parametrize("model_id", sorted(MODELS))
@pytest.mark.parametrize("level", EFFORT_LEVELS)
def test_each_level_is_sent_as_the_labs_own_setting(model_id: str, level: str) -> None:
    spec = MODELS[model_id].at_effort(level)
    adapter = fake_adapter(spec)
    assert spec.effort == level
    assert wire_effort(adapter) == spec.effort_setting == MODELS[model_id].efforts[level]
    # Every mapped value is one the lab documents for its effort parameter.
    assert set(spec.efforts.values()) <= set(type(adapter).effort_settings)


def test_a_level_the_lab_lacks_is_refused_not_mapped_to_the_nearest() -> None:
    spec = MODELS["gemini-4-argon"]
    no_low = dataclasses.replace(spec, efforts={"medium": "medium", "high": "high"})
    with pytest.raises(ValueError, match="not mapped to the nearest"):
        no_low.at_effort("low")


def test_an_adapter_refuses_a_setting_its_lab_does_not_document() -> None:
    spec = MODELS["gemini-4-argon"]
    wrong = dataclasses.replace(spec, efforts={**spec.efforts, "high": "xhigh"}, effort="high")
    with pytest.raises(ValueError, match="does not accept effort 'xhigh'"):
        fake_adapter(wrong)


# Live Seats: header, harness.call events and request bodies -------------------------------


def test_a_seat_option_sets_the_effort_on_the_wire_in_the_header_and_on_every_call() -> None:
    adapters: dict[str, ProviderAdapter] = {}

    def factory(spec: ModelSpec) -> ProviderAdapter:
        adapters[spec.id] = effort_aware_adapter(spec)
        return adapters[spec.id]

    bots = "live:gpt-6.1-sol?effort=low*5,live:claude-opus-5-5?effort=high*5"
    run = play_live(
        GameConfig(seed=4, round_cap=3),
        bots,
        spend_cap_usd=50,
        adapter_factory=factory,
        models=MODELS,
    )
    assert run.aborted is None
    want = {"gpt-6.1-sol": "low", "claude-opus-5-5": "high"}
    header = run.game.transcript.records[0]
    for seat in header["seats"]:
        c = seat["controller"]
        assert c["effort"] == want[c["model"]]
        assert c["effort_setting"] == MODELS[c["model"]].efforts[want[c["model"]]]
    sent = [p["reasoning"]["effort"] for p in adapters["gpt-6.1-sol"].client.responses.calls]
    assert sent and set(sent) == {"low"}
    sent = [p["output_config"]["effort"] for p in adapters["claude-opus-5-5"].client.messages.calls]
    assert sent and set(sent) == {"high"}
    calls = [r["data"] for r in run.game.transcript.events("harness.call")]
    assert calls and all(c["effort"] == want[c["model"]] for c in calls)
    assert all(c["effort_setting"] == want[c["model"]] for c in calls)
    assert {c["thinking_tokens"] for c in calls if c["model"] == "gpt-6.1-sol"} == {200}
    for model_id, totals in run.cost["models"].items():
        assert totals["effort"] == want[model_id]


def test_the_run_level_override_and_seat_options_must_agree() -> None:
    config = GameConfig(seed=4, round_cap=2)
    kw: dict[str, Any] = {"spend_cap_usd": 5, "adapter_factory": fake_adapter, "models": MODELS}
    with pytest.raises(ValueError, match="asks for effort 'high' but the run sets 'low'"):
        play_live(config, "live:grok-4.7?effort=high*10", effort="low", **kw)
    with pytest.raises(ValueError, match="two effort levels"):
        play_live(config, "live:grok-4.7?effort=high*5,live:grok-4.7?effort=low*5", **kw)
    run = play_live(config, "live:grok-4.7?effort=low*10", effort="low", **kw)
    assert {s["controller"]["effort"] for s in run.game.transcript.records[0]["seats"]} == {"low"}


def test_dry_seats_record_the_effort_they_stand_in_for() -> None:
    rng = GameRng(1)
    seat = make_controller("dry:grok-4.7?effort=high", rng)
    assert seat.describe() == {
        "kind": "dry",
        "model": "grok-4.7",
        "effort": "high",
        "effort_setting": MODELS["grok-4.7"].efforts["high"],
        "plays_as": "scripted",
    }
    assert "effort" not in make_controller("dry:grok-4.7", rng).describe()
    with pytest.raises(ValueError):
        make_controller("dry:grok-4.7?effort=extreme", rng)


def test_spend_log_lines_carry_the_effort(tmp_path: Path) -> None:
    ledger = Ledger(5.0)
    ledger.send(
        fake_adapter(MODELS["gpt-6-astra"].at_effort("high")),
        REQUEST,
        purpose="seat",
        phase="action",
    )
    (rec,) = ledger.records
    assert (rec.effort, rec.effort_setting) == ("high", "high")


# The schedule ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sweep() -> dict[str, Any]:
    return make_effort_manifest(POOL, 11)


def test_the_sweep_is_140_mirror_matches_10_per_model_per_level(sweep: dict[str, Any]) -> None:
    games = sweep["games"]
    assert len(games) == 140 and verify(sweep) == []
    assert sweep["experiment"] == "effort" and sweep["effort_levels"] == ["low", "high"]
    assert {g["kind"] for g in games} == {"mirror"}
    cells = Counter((g["lineup"][0], g["effort"]) for g in games)
    assert cells == {(m, lvl): 10 for m in POOL for lvl in ("low", "high")}
    assert all(g["impostors"] == [g["lineup"][0]] * 2 for g in games)
    assert len({g["seed"] for g in games}) == 140
    # Each round of 14 games covers every (Model, level) cell once.
    for k in range(10):
        chunk = games[14 * k : 14 * (k + 1)]
        assert len({(g["lineup"][0], g["effort"]) for g in chunk}) == 14
    assert make_effort_manifest(POOL, 11) == sweep


def test_verify_catches_a_broken_sweep(sweep: dict[str, Any]) -> None:
    broken = json.loads(json.dumps(sweep))
    broken["games"][0]["effort"] = "medium"
    problems = verify(broken)
    assert any("not a swept level" in p for p in problems)
    assert any("unbalanced" in p for p in problems)


def test_the_sweep_refuses_a_level_a_model_lacks(monkeypatch: pytest.MonkeyPatch) -> None:
    models = dict(MODELS)
    models["grok-4.7"] = dataclasses.replace(models["grok-4.7"], efforts={"medium": "medium"})
    monkeypatch.setattr(schedule_module, "load_models", lambda: models)
    with pytest.raises(ValueError, match=r"grok-4\.7: effort 'low'"):
        make_effort_manifest(POOL, 1)


def test_cli_schedules_the_sweep(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "schedule",
                "--experiment",
                "effort",
                "--master-seed",
                "2",
                "--out",
                str(tmp_path / "e"),
            ]
        )
        == 0
    )
    assert "140 games: effort sweep" in capsys.readouterr().out
    manifest = json.loads((tmp_path / "e" / "manifest.json").read_text("utf-8"))
    assert manifest["master_seed"] == 2 and len(manifest["games"]) == 140
    with pytest.raises(SystemExit):
        main(["schedule", "--experiment", "effort", "--blocks", "1"])
    with pytest.raises(SystemExit):
        main(["schedule", "--per-level", "3"])


def test_a_sweep_game_seats_every_model_at_its_level(sweep: dict[str, Any]) -> None:
    game = sweep["games"][0]
    _, bots = game_setup(sweep, game, "dry")
    assert bots == ",".join([f"dry:{game['lineup'][0]}?effort={game['effort']}"] * 10)
    header = {
        "seats": [
            {
                "role": "impostor" if i < 2 else "crewmate",
                "controller": {"model": game["lineup"][0], "effort": "medium"},
            }
            for i in range(10)
        ]
    }
    with pytest.raises(RuntimeError, match="did not play at effort"):
        check_seating(game, header)


# Running and reporting --------------------------------------------------------------------


@pytest.fixture(scope="module")
def dry_runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("effort")
    sweep_dir, v1_dir = root / "sweep", root / "v1"
    create_run(sweep_dir, make_effort_manifest(POOL, 5, per_level=2, config={"round_cap": 6}))
    execute(sweep_dir, mode="dry")
    create_run(v1_dir, make_manifest(POOL, 6, blocks=0, mirror=True, config={"round_cap": 6}))
    execute(v1_dir, mode="dry")
    return sweep_dir, v1_dir


def test_a_dry_sweep_plays_end_to_end_and_records_effort(dry_runs: tuple[Path, Path]) -> None:
    sweep_dir, _ = dry_runs
    manifest = json.loads((sweep_dir / "manifest.json").read_text("utf-8"))
    index = read_jsonl(sweep_dir / "index.jsonl")
    assert len(index) == 28 and all(r["status"] == "complete" for r in index)
    by_id = {g["id"]: g for g in manifest["games"]}
    for line in index:
        assert line["effort"] == by_id[line["game"]]["effort"]
        header = read_jsonl(transcript_path(sweep_dir, line["game"]))[0]
        assert {s["controller"]["effort"] for s in header["seats"]} == {line["effort"]}


def svg_text(path: Path) -> list[str]:
    return re.findall(r"<text[^>]*>(.*?)</text>", path.read_text("utf-8"), flags=re.S)


def test_report_graphs_metrics_against_effort_with_medium_from_v1(
    dry_runs: tuple[Path, Path], tmp_path: Path
) -> None:
    sweep_dir, v1_dir = dry_runs
    out, report = write_effort_report(
        sweep_dir, medium=[v1_dir], out_dir=tmp_path / "r", replicates=100
    )
    for stem in ("effort_metrics", "effort_resources", "effort_vs_thinking", "effort_vs_cost"):
        for ext in ("svg", "png"):
            assert (out / f"{stem}.{ext}").stat().st_size > 1000
    assert report["levels"] == ["low", "medium", "high"]
    assert [m["model"] for m in report["models"]] == ALPHA
    for metric in report["metrics"].values():
        assert list(metric["per_model"]) == ALPHA
    assert set(TEAM) >= {"productivity", "impostors_ejected", "correct_vote_share"}
    for m in ALPHA:
        assert report["cells"][m] == {
            "low": {"complete": 2, "planned": 2},
            "medium": {"complete": 6, "planned": 0},
            "high": {"complete": 2, "planned": 2},
        }
        cell = report["metrics"]["productivity"]["per_model"][m]
        assert all(cell[lvl]["estimate"] is not None for lvl in EFFORT_LEVELS)
    assert report["run"]["medium_runs"][0]["mirror_games_used"] == 42
    # Model labels are the column titles, left to right in alphabetical order.
    titles = [t for t in svg_text(out / "effort_metrics.svg") if t in ALPHA]
    assert titles == ALPHA
    texts = [(out / "progress.md").read_text("utf-8"), (out / "metrics.json").read_text("utf-8")]
    texts += [" ".join(svg_text(p)) for p in out.glob("*.svg")]
    for text in texts:
        assert not RANKING_WORDS.search(text), RANKING_WORDS.search(text)
    assert "self-play" in " ".join(svg_text(out / "effort_metrics.svg"))
    text = (out / "metrics.public.json").read_text("utf-8")
    public = json.loads(text)
    assert "master_seed" not in text and str(v1_dir) not in text
    assert all("seed" not in g for g in public["games"])


def test_report_without_v1_leaves_medium_empty(dry_runs: tuple[Path, Path]) -> None:
    sweep_dir, _ = dry_runs
    data = load_effort(sweep_dir)
    assert {g.level for g in data.games} == {"low", "high"}


def test_medium_input_must_be_distinct_and_the_same_mode(
    dry_runs: tuple[Path, Path], tmp_path: Path
) -> None:
    sweep_dir, v1_dir = dry_runs
    with pytest.raises(ValueError, match="given twice"):
        load_effort(sweep_dir, [v1_dir, v1_dir])
    live_v1 = tmp_path / "live"
    create_run(live_v1, make_manifest(POOL, 6, blocks=0, mirror=True))
    (live_v1 / "run.json").write_text('{"mode": "live"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="do not mix"):
        load_effort(sweep_dir, [live_v1])


def test_cli_report_dispatches_to_the_effort_report(
    dry_runs: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sweep_dir, v1_dir = dry_runs
    out = tmp_path / "cli"
    args = ["report", str(sweep_dir), "--medium", str(v1_dir), "--replicates", "20"]
    assert main([*args, "--out", str(out)]) == 0
    assert "42 medium-effort Mirror Matches" in capsys.readouterr().out
    assert (out / "effort_vs_cost.svg").is_file()
    with pytest.raises(SystemExit):
        main(["report", str(v1_dir), "--medium", str(sweep_dir), "--out", str(tmp_path / "x")])


def test_a_live_sweep_graphs_metrics_against_thinking_and_cost(tmp_path: Path) -> None:
    run_dir = tmp_path / "live"
    pool = ["gpt-6.1-sol", "muse-wonder-1.0"]
    manifest = make_effort_manifest(pool, 9, per_level=1, config={"round_cap": 2})
    create_run(run_dir, manifest)
    report = execute(
        run_dir,
        mode="live",
        spend_cap_usd=500.0,
        adapter_factory=effort_aware_adapter,
        models=MODELS,
    )
    assert report.finished
    spend = [r for r in read_jsonl(run_dir / "spend.jsonl") if r["kind"] == "call"]
    by_game = {g["id"]: g["effort"] for g in manifest["games"]}
    assert spend and all(r["effort"] == by_game[r["game"]] for r in spend)

    _, rep = write_effort_report(run_dir, out_dir=tmp_path / "r", replicates=50)
    for m in pool:
        thinking = rep["metrics"]["thinking_per_call"]["per_model"][m]
        cost = rep["metrics"]["cost_per_game"]["per_model"][m]
        assert thinking["low"]["estimate"] == pytest.approx(THINKING["low"])
        assert thinking["high"]["estimate"] == pytest.approx(THINKING["high"])
        assert thinking["medium"]["estimate"] is None
        assert cost["high"]["estimate"] > cost["low"]["estimate"] > 0
    labels = svg_text(tmp_path / "r" / "effort_vs_thinking.svg")
    assert {"L", "H"} <= set(labels)


def test_effort_combines_with_a_seat_harness() -> None:
    bots = "live:grok-4.7@agent-loop?effort=low*2,live:grok-4.7?effort=low*8"
    run = play_live(
        GameConfig(seed=4, round_cap=2),
        bots,
        spend_cap_usd=50,
        adapter_factory=fake_adapter,
        models=MODELS,
    )
    seats = [s["controller"] for s in run.game.transcript.records[0]["seats"]]
    assert {c["effort"] for c in seats} == {"low"}
    assert len({c["harness"] for c in seats}) == 2
    calls = [r["data"] for r in run.game.transcript.events("harness.call")]
    assert calls and {c["effort"] for c in calls} == {"low"}
    with pytest.raises(ValueError):
        play_live(
            GameConfig(seed=4),
            "live:grok-4.7?effort=low@agent-loop*10",
            spend_cap_usd=5,
            adapter_factory=fake_adapter,
            models=MODELS,
        )


# Review fixes: thinking telemetry, compatible setups, mode, unique keys --------------------


def live_sweep(tmp_path: Path, think: Any, name: str = "live", cap: int = 2) -> Path:
    """A fake-live sweep of GPT-6.1 Sol whose n-th call reports ``think(n)`` thinking tokens."""
    run_dir = tmp_path / name
    create_run(
        run_dir,
        make_effort_manifest(["gpt-6.1-sol"], 9, per_level=1, config={"round_cap": cap}),
    )
    counter = iter(range(10**6))

    def factory(spec: ModelSpec) -> ProviderAdapter:
        adapter = fake_adapter(spec)

        def reply(p: dict[str, Any]) -> Any:
            t = think(next(counter))
            out = 500 + (t or 0)
            return openai_reply(polite_call(openai_tail(p)), output_tokens=out, reasoning_tokens=t)

        adapter.client = OpenAIFake(reply)  # type: ignore[attr-defined]
        return adapter

    report = execute(
        run_dir, mode="live", spend_cap_usd=500.0, adapter_factory=factory, models=MODELS
    )
    assert report.finished
    return run_dir


def thinking_cells(run_dir: Path, tmp_path: Path) -> dict[str, Any]:
    _, rep = write_effort_report(run_dir, out_dir=tmp_path / "r", replicates=20, graphs=False)
    return rep["metrics"]["thinking_per_call"]["per_model"]["gpt-6.1-sol"]


def test_missing_thinking_telemetry_is_na_not_a_measured_zero(tmp_path: Path) -> None:
    cells = thinking_cells(live_sweep(tmp_path, lambda n: None), tmp_path)
    for level in ("low", "high"):
        assert cells[level]["estimate"] is None and cells[level]["n_units"] == 0


def test_mixed_thinking_telemetry_averages_only_reported_calls(tmp_path: Path) -> None:
    run_dir = live_sweep(tmp_path, lambda n: 1000 if n % 2 else None)
    cells = thinking_cells(run_dir, tmp_path)
    calls = [r for r in read_jsonl(run_dir / "spend.jsonl") if r["kind"] == "call"]
    reported = sum(r["thinking_tokens"] is not None for r in calls)
    assert 0 < reported < len(calls)
    assert cells["low"]["estimate"] == pytest.approx(1000)
    assert cells["low"]["n_units"] + cells["high"]["n_units"] == reported


def test_a_reported_zero_is_a_measured_zero(tmp_path: Path) -> None:
    cells = thinking_cells(live_sweep(tmp_path, lambda n: 0), tmp_path)
    for level in ("low", "high"):
        assert cells[level]["estimate"] == 0 and cells[level]["n_units"] > 0


def dry_pair(tmp_path: Path, sweep_cap: int, v1_cap: int, v1_seed: int = 6) -> tuple[Path, Path]:
    sweep_dir, v1_dir = tmp_path / "sweep", tmp_path / f"v1-{v1_seed}"
    if not sweep_dir.exists():
        cfg = {"round_cap": sweep_cap}
        create_run(sweep_dir, make_effort_manifest(POOL, 5, per_level=1, config=cfg))
        execute(sweep_dir, mode="dry")
    cfg = {"round_cap": v1_cap}
    create_run(v1_dir, make_manifest(POOL, v1_seed, blocks=0, mirror=True, config=cfg))
    execute(v1_dir, mode="dry")
    return sweep_dir, v1_dir


def test_a_medium_run_with_another_round_cap_is_refused(tmp_path: Path) -> None:
    sweep_dir, v1_dir = dry_pair(tmp_path, 2, 9)
    with pytest.raises(ValueError, match=r"config\.round_cap 2 vs 9"):
        load_effort(sweep_dir, [v1_dir])


def rewrite_header(path: Path, edit: Any) -> None:
    lines = path.read_text("utf-8").splitlines()
    header = json.loads(lines[0])
    edit(header)
    lines[0] = json.dumps(header, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_medium_game_under_another_harness_or_engine_is_refused(tmp_path: Path) -> None:
    sweep_dir, v1_dir = dry_pair(tmp_path, 3, 3)
    load_effort(sweep_dir, [v1_dir])  # same setup: accepted

    def other_harness(header: dict[str, Any]) -> None:
        for s in header["seats"]:
            s["controller"]["harness"] = "agent-loop-v1"

    game = sorted((v1_dir / "games").glob("*.jsonl"))[0]
    original = game.read_text("utf-8")
    rewrite_header(game, other_harness)
    with pytest.raises(ValueError, match="seats"):
        load_effort(sweep_dir, [v1_dir])
    game.write_text(original, encoding="utf-8")

    manifest = json.loads((v1_dir / "manifest.json").read_text("utf-8"))
    manifest["engine"]["scenario_version"] = -1
    (v1_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=r"engine\.scenario_version"):
        load_effort(sweep_dir, [v1_dir])


def test_mode_comes_from_the_transcripts_when_run_json_is_missing_or_wrong(
    tmp_path: Path,
) -> None:
    live_dir = live_sweep(tmp_path, lambda n: 100)
    _, v1_dir = dry_pair(tmp_path, 2, 2)
    (v1_dir / "run.json").unlink()
    with pytest.raises(ValueError, match=r"is a dry run but .* is live"):
        load_effort(live_dir, [v1_dir])
    (v1_dir / "run.json").write_text('{"mode": "live"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"run\.json says live but its transcripts are dry"):
        load_effort(live_dir, [v1_dir])


def test_games_from_several_runs_keep_an_unambiguous_key(tmp_path: Path) -> None:
    sweep_dir, v1_a = dry_pair(tmp_path, 4, 4, v1_seed=6)
    _, v1_b = dry_pair(tmp_path, 4, 4, v1_seed=7)
    out, _ = write_effort_report(
        sweep_dir, medium=[v1_a, v1_b], out_dir=tmp_path / "r", replicates=10, graphs=False
    )
    for name in ("metrics.json", "metrics.public.json"):
        data = json.loads((out / name).read_text("utf-8"))
        keys = [g["key"] for g in data["games"]]
        assert len(keys) == len(set(keys)) == 14 + 2 * 42
        assert "medium-1/mirror-m0-1" in keys and "medium-2/mirror-m0-1" in keys
        seats = Counter(s["key"] for s in data["seats"])
        assert set(seats) == set(keys) and set(seats.values()) == {10}
        assert [r["run"] for r in data["run"]["medium_runs"]] == ["medium-1", "medium-2"]


@pytest.mark.parametrize("think", [None, 0, 700])
def test_every_exported_seat_keeps_its_own_invalid_action_and_forfeit_counts(
    think: int | None,
) -> None:
    from zantine.analysis.metrics import game_row

    def factory(spec: ModelSpec) -> ProviderAdapter:
        adapter = fake_adapter(spec)
        reply = openai_reply(
            text="no tool call", output_tokens=500 + (think or 0), reasoning_tokens=think
        )
        adapter.client = OpenAIFake(lambda p: reply)  # type: ignore[attr-defined]
        return adapter

    run = play_live(
        GameConfig(seed=3, round_cap=3),
        "live:gpt-6.1-sol,scripted*9",
        spend_cap_usd=50,
        adapter_factory=factory,
        models=MODELS,
    )
    records = run.game.transcript.records
    row = game_row({"id": "g", "kind": "lobby", "seed": 3}, records)
    cost = {c["seat"]: c for c in run.cost["seats"]}
    assert len({(c["invalid_actions"], c["forfeits"]) for c in cost.values()}) > 1
    for seat in row.seats:
        assert (seat.invalid_actions, seat.forfeits) == (
            cost[seat.seat]["invalid_actions"],
            cost[seat.seat]["forfeits"],
        )
