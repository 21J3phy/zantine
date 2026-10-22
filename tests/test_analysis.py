"""Results analysis (#18, #33): metrics, cluster bootstrap, Sabotage Damage, graphs."""

from __future__ import annotations

import json
import re
import shutil
import statistics
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from helpers import make_game
from zantine import runs
from zantine.analysis.bootstrap import ratio_estimates, resample_weights
from zantine.analysis.graphs import convex_hull, render_all
from zantine.analysis.metrics import GameRow, SeatRow, game_row, load_run
from zantine.analysis.report import (
    PLANNED_BLOCKS,
    build_report,
    flagship_pairs,
    model_info,
    overlap_clusters,
    write_report,
)
from zantine.analysis.sabotage import sabotage_damage
from zantine.cli import main
from zantine.rules.crisis import TriggerCrisis
from zantine.rules.tasks import board
from zantine.runs import create_run, execute
from zantine.schedule import load_pool, make_manifest
from zantine.seats.bots import PlaybookBot

POOL = load_pool()
ALPHA = sorted(POOL)
# Words that would read as a ranking. Nothing the report writes may contain them.
RANKING_WORDS = re.compile(
    r"\b(winners?|best|worst|top|rank(ed|ing|s)?|leaderboard|beats?)\b", re.I
)


# Synthetic games ------------------------------------------------------------------------


def synthetic_game(
    gid: str,
    kind: str,
    lineup_id: int,
    lineup: list[str],
    impostors: list[str],
    completion: float,
    cap: int = 30,
) -> GameRow:
    """A game whose restricted completion time is ``completion`` (>= cap: unfinished)."""
    finished = completion < cap
    seats = []
    imps = list(impostors)
    for i, m in enumerate([m for m in lineup for _ in range(2)]):
        role = "impostor" if m in imps else "crewmate"
        if role == "impostor":
            imps.remove(m)
        seats.append(SeatRow(game=gid, kind=kind, seat=i, model=m, role=role))
    return GameRow(
        id=gid,
        kind=kind,
        block=1,
        lineup_id=lineup_id,
        lineup=lineup,
        impostor_models=list(impostors),
        seed=0,
        round_cap=cap,
        rounds=int(min(completion, cap)),
        end_team="crew" if finished else "none",
        end_reason="tasks_done" if finished else "round_cap",
        finished=finished,
        completion_round=int(completion) if finished else None,
        contained=False,
        containment_round=None,
        impostors=len(impostors),
        impostors_ejected=0,
        wrong_ejections=0,
        ejections=0,
        kills=0,
        cost_usd=0.0,
        seats=seats,
    )


# Bootstrap ---------------------------------------------------------------------------------


def test_resample_weights_keep_each_stratum_size_and_are_seeded() -> None:
    strata = ["lobby"] * 5 + ["benign"] * 3 + ["mirror"] * 2
    w = resample_weights(strata, 500, seed=3)
    assert w.shape == (500, 10)
    assert (w[:, :5].sum(axis=1) == 5).all()
    assert (w[:, 5:8].sum(axis=1) == 3).all()
    assert (w[:, 8:].sum(axis=1) == 2).all()
    assert np.array_equal(w, resample_weights(strata, 500, seed=3))
    assert not np.array_equal(w, resample_weights(strata, 500, seed=4))


def test_ratio_estimate_is_the_pooled_ratio_and_undefined_columns_are_nan() -> None:
    num = np.array([[1.0, 0.0], [3.0, 0.0], [2.0, 0.0]])
    den = np.array([[2.0, 0.0], [2.0, 0.0], [4.0, 0.0]])
    a, b = ratio_estimates(num, den, resample_weights(["x"] * 3, 200, 0))
    assert a.estimate == pytest.approx(6 / 8)
    assert a.low <= a.estimate <= a.high
    assert a.n_games == 3 and a.n_units == 8
    assert np.isnan(b.estimate) and b.replicates == 0


def test_cluster_bootstrap_covers_a_known_mean_with_correlated_seats() -> None:
    """Seats share a game effect (ICC 0.5); intervals over whole games keep ~95% coverage."""
    rng = np.random.default_rng(11)
    true_mean, games, seats, trials = 2.0, 60, 10, 200
    covered = naive_covered = 0
    for t in range(trials):
        game_effect = rng.normal(0, 1, games)
        y = true_mean + game_effect[:, None] + rng.normal(0, 1, (games, seats))
        num = y.sum(axis=1, keepdims=True)
        den = np.full((games, 1), float(seats))
        (est,) = ratio_estimates(num, den, resample_weights(["g"] * games, 400, t))
        covered += est.low <= true_mean <= est.high
        # Treating seats as independent understates the uncertainty.
        flat = y.ravel()
        half = 1.96 * flat.std(ddof=1) / np.sqrt(flat.size)
        naive_covered += abs(flat.mean() - true_mean) <= half
    assert 0.88 <= covered / trials <= 0.99
    assert naive_covered / trials < 0.8


# Sabotage Damage ---------------------------------------------------------------------------


def planted_games(
    damage: dict[str, float], noise: float, rng: np.random.Generator, reps: int = 3
) -> list[GameRow]:
    """Lineups of 5 with a lineup-specific base time; lobbies add each Impostor's damage.

    The cap is far away, so every game finishes and the planted effects are exact.
    """
    games = []
    for lid, lineup in enumerate(combinations(sorted(damage), 5)):
        base = rng.uniform(10, 20)
        for r in range(reps):
            t = base + rng.normal(0, noise)
            games.append(synthetic_game(f"b{lid}-{r}", "benign", lid, list(lineup), [], t, 100))
            pair = [lineup[(lid + r) % 5], lineup[(lid + 2 * r + 1) % 5]]
            t = base + sum(damage[m] for m in pair) + rng.normal(0, noise)
            games.append(synthetic_game(f"l{lid}-{r}", "lobby", lid, list(lineup), pair, t, 100))
    return games


def test_regression_recovers_planted_damage_per_impostor_model() -> None:
    planted = {m: d for m, d in zip(ALPHA, [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0], strict=True)}
    games = planted_games(planted, noise=0.5, rng=np.random.default_rng(5))
    w = resample_weights([g.kind for g in games], 300, 1)
    ests, info = sabotage_damage(games, ALPHA, w, range(len(games)))
    assert info["lobbies"] == info["benign"] == 63
    for m, e in zip(ALPHA, ests, strict=True):
        assert e.estimate == pytest.approx(planted[m], abs=0.4)
        assert e.low <= planted[m] <= e.high
        assert e.high - e.low < 1.5


def test_regression_is_exact_without_noise_and_handles_same_model_pairs() -> None:
    planted = {m: float(i) for i, m in enumerate(ALPHA)}
    games = planted_games(planted, noise=0.0, rng=np.random.default_rng(1), reps=1)
    lid, lineup, base = games[0].lineup_id, games[0].lineup, games[0].restricted_completion
    same = base + 2 * planted[lineup[1]]  # a same-Model pairing holds both Impostor Seats
    games.append(synthetic_game("same", "lobby", lid, lineup, [lineup[1]] * 2, same, cap=100))
    w = resample_weights([g.kind for g in games], 50, 1)
    ests, _ = sabotage_damage(games, ALPHA, w, range(len(games)))
    for m, e in zip(ALPHA, ests, strict=True):
        assert e.estimate == pytest.approx(planted[m], abs=1e-6)


def test_damage_is_not_reported_when_no_benign_match_identifies_it() -> None:
    lineup = ALPHA[:5]
    games = [
        synthetic_game("l1", "lobby", 0, lineup, [lineup[0], lineup[1]], 20),
        synthetic_game("b1", "benign", 1, ALPHA[2:7], [], 15),
    ]
    w = resample_weights([g.kind for g in games], 20, 0)
    ests, _ = sabotage_damage(games, ALPHA, w, range(2))
    assert all(np.isnan(e.estimate) for e in ests)


def test_unfinished_games_count_as_the_round_cap_in_the_regression() -> None:
    lineup = ALPHA[:5]
    games = [
        synthetic_game("b1", "benign", 0, lineup, [], 20),
        synthetic_game("b2", "benign", 0, lineup, [], 22),
        synthetic_game("l1", "lobby", 0, lineup, [lineup[0], lineup[0]], 99),  # never finished
    ]
    assert games[2].restricted_completion == 30
    w = resample_weights([g.kind for g in games], 20, 0)
    ests, _ = sabotage_damage(games, ALPHA, w, range(3))
    assert ests[0].estimate == pytest.approx((30 - 21) / 2)


# Censoring and per-game extraction -------------------------------------------------------


def records(reason: str, rounds: int, *, cap: int = 30, contained_round: int | None = None):
    seats = [
        {
            "seat": i,
            "role": "impostor" if i < 2 else "crewmate",
            "name": f"P{i}",
            "controller": {"kind": "dry", "model": ALPHA[i % 7]},
        }
        for i in range(10)
    ]
    out: list[dict[str, Any]] = [
        {"kind": "header", "config": {"round_cap": cap}, "seats": seats},
        # Seat 2 votes for an Impostor, seat 3 for a Crewmate, seat 4 skips, seat 5 forfeits.
        {
            "kind": "event",
            "round": 4,
            "type": "vote",
            "data": {"meeting": 1, "seat": 2, "value": 0, "valid": True},
        },
        {
            "kind": "event",
            "round": 4,
            "type": "vote",
            "data": {"meeting": 1, "seat": 3, "value": 6, "valid": True},
        },
        {
            "kind": "event",
            "round": 4,
            "type": "vote",
            "data": {"meeting": 1, "seat": 4, "value": None, "valid": True},
        },
        {
            "kind": "event",
            "round": 4,
            "type": "vote",
            "data": {"meeting": 1, "seat": 5, "value": "x", "valid": False},
        },
        {
            "kind": "event",
            "round": 4,
            "type": "vote",
            "data": {"meeting": 1, "seat": 5, "value": "x", "valid": False},
        },
        {
            "kind": "event",
            "round": 4,
            "type": "ejection",
            "data": {"meeting": 1, "seat": 6, "role": "crewmate"},
        },
        {"kind": "event", "round": 2, "type": "kill", "data": {"victim": 7, "killers": [0]}},
    ]
    if contained_round is not None:
        out += [
            {
                "kind": "event",
                "round": contained_round,
                "type": "ejection",
                "data": {"meeting": 2, "seat": 0, "role": "impostor"},
            },
            {
                "kind": "event",
                "round": contained_round,
                "type": "ejection",
                "data": {"meeting": 3, "seat": 1, "role": "impostor"},
            },
        ]
    out.append(
        {
            "kind": "result",
            "winner": "crew" if reason == "tasks_done" else "impostors",
            "reason": reason,
            "rounds": rounds,
            "containment": {"contained": contained_round is not None, "round": contained_round},
            "tasks": {"seats": [{"seat": i, "assigned": 2, "done": i % 3} for i in range(10)]},
        }
    )
    out.append(
        {
            "kind": "cost",
            "complete": True,
            "game": {"cost_usd": 1.0},
            "seats": [{"seat": i, "cost_usd": 0.1} for i in range(10)],
        }
    )
    return out


PLANNED = {
    "id": "g",
    "kind": "lobby",
    "block": 1,
    "lineup_id": 0,
    "lineup": ALPHA[:5],
    "impostors": ALPHA[:2],
    "seed": 1,
}


def test_finished_game_uses_its_completion_round() -> None:
    g = game_row(PLANNED, records("tasks_done", 17, contained_round=9))
    assert g.finished and g.completion_round == 17 and g.restricted_completion == 17
    assert g.contained and g.restricted_containment == 9
    assert g.impostors_ejected == 2 and g.wrong_ejections == 1


@pytest.mark.parametrize(("reason", "rounds"), [("round_cap", 30), ("parity", 12), ("crisis", 8)])
def test_unfinished_games_are_restricted_to_the_cap(reason: str, rounds: int) -> None:
    g = game_row(PLANNED, records(reason, rounds))
    assert not g.finished and g.completion_round is None
    assert g.restricted_completion == 30  # the work was not done by the cap
    assert g.restricted_containment == 30


def test_work_finished_in_the_round_an_impostor_win_fires_counts_as_finished() -> None:
    """A Crisis deadline and the last Task in the same Round: the Impostors win, but the
    work was done in that Round (real engine)."""
    game = make_game(
        {0: PlaybookBot({1: TriggerCrisis()})},
        crisis_cooldown=0,
        crisis_deadline=1,
        tasks_per_crewmate=1,
        task_dependency_chance=0,
    )
    game.play_round()
    assert not game.state.over
    for task in board(game.state).graph:
        task.done = True
    game.play_round()
    recs = game.transcript.records
    result = recs[-1]
    assert (result["winner"], result["reason"], result["rounds"]) == ("impostors", "crisis", 2)
    assert result["tasks"]["done"] == result["tasks"]["total"] > 0
    g = game_row(PLANNED, recs)
    assert (g.end_team, g.end_reason) == ("impostors", "crisis")
    assert g.finished and g.completion_round == 2 and g.restricted_completion == 2


def test_benign_match_has_no_containment_time() -> None:
    recs = records("tasks_done", 20)
    for s in recs[0]["seats"]:
        s["role"] = "crewmate"
    assert game_row(PLANNED, recs).restricted_containment is None


def test_restricted_mean_equals_kaplan_meier_area_up_to_the_cap() -> None:
    """With no censoring before L, mean(min(T, L)) is the area under the KM curve to L."""
    cap = 30
    times = [12, 17, 17, 25, None, None, 29]  # None: never finished
    restricted = [t if t is not None else cap for t in times]
    area, alive, last = 0.0, 1.0, 0
    for t in sorted(t for t in times if t is not None):
        area += alive * (t - last)
        alive -= 1 / len(times)
        last = t
    area += alive * (cap - last)
    assert np.mean(restricted) == pytest.approx(area)


def test_votes_seats_and_costs_are_read_from_the_transcript() -> None:
    g = game_row(PLANNED, records("round_cap", 30))
    s = {r.seat: r for r in g.seats}
    assert (s[2].votes, s[2].accusations, s[2].correct) == (1, 1, 1)
    assert (s[3].accusations, s[3].correct) == (1, 0)
    assert (s[4].skips, s[4].accusations) == (1, 0)
    assert (s[5].votes, s[5].skips, s[5].forfeited_votes) == (1, 1, 1)
    assert s[6].ejected and s[6].survival_rounds == 4
    assert s[7].killed and s[7].survival_rounds == 2
    assert s[8].survival_rounds == 30
    assert s[0].model == ALPHA[0] and s[0].cost_usd == pytest.approx(0.1)
    assert (s[4].tasks_assigned, s[4].tasks_done) == (2, 1)


# Map helpers ---------------------------------------------------------------------------------


def test_flagship_sibling_pairs_come_from_models_toml() -> None:
    pairs = flagship_pairs(model_info(ALPHA))
    assert pairs == [["claude-fable-5-1", "claude-opus-5-5"], ["gpt-6-astra", "gpt-6.1-sol"]]


def test_overlapping_intervals_form_clusters() -> None:
    from zantine.analysis.bootstrap import Estimate

    def e(lo: float, hi: float) -> Estimate:
        return Estimate((lo + hi) / 2, lo, hi, 1, 1, 1)

    x = [e(0, 2), e(1, 3), e(2.5, 4), e(10, 11)]
    y = [e(0, 1), e(0.5, 1.5), e(1.2, 2), e(0, 1)]
    assert overlap_clusters(["a", "b", "c", "d"], x, y) == [["a", "b", "c"], ["d"]]


def test_convex_hull_of_a_square_with_an_inner_point() -> None:
    hull = convex_hull([(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5)])
    assert sorted(hull) == [(0, 0), (0, 1), (1, 0), (1, 1)]


# End to end on a dry run ------------------------------------------------------------------


@pytest.fixture(scope="module")
def dry_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    run = tmp_path_factory.mktemp("analysis") / "run"
    create_run(run, make_manifest(POOL, 3, blocks=1, mirror=True))
    execute(run, mode="dry")
    return run


def svg_text(path: Path) -> list[str]:
    return re.findall(r"<text[^>]*>(.*?)</text>", path.read_text("utf-8"), flags=re.S)


def check_outputs(out: Path, report: dict[str, Any]) -> None:
    for stem in ("opening_map", "small_multiples", "sabotage_damage", "cost_vs_metrics", "mirror"):
        for ext in ("svg", "png"):
            assert (out / f"{stem}.{ext}").stat().st_size > 1000
    data = json.loads((out / "metrics.json").read_text("utf-8"))
    assert [m["model"] for m in data["models"]] == ALPHA
    for metric in [*data["metrics"].values(), *data["mirror"].values()]:
        assert list(metric["per_model"]) == ALPHA
    texts = [(out / "progress.md").read_text("utf-8"), (out / "metrics.json").read_text("utf-8")]
    texts += [" ".join(svg_text(p)) for p in out.glob("*.svg")]
    for text in texts:
        assert not RANKING_WORDS.search(text), RANKING_WORDS.search(text)
    assert report["progress"]["planned_blocks"] == PLANNED_BLOCKS


def test_report_renders_from_a_dry_run(dry_run: Path) -> None:
    out, report = write_report(dry_run, replicates=200, seed=1)
    assert out == dry_run / "report"
    check_outputs(out, report)
    assert report["run"]["games_complete"] == 28 + 42
    assert report["progress"]["blocks_complete"] == 1
    progress_md = (out / "progress.md").read_text("utf-8")
    assert "**1 of 12**" in progress_md and "no early stopping" in progress_md
    # The opening map carries every pool Model, in alphabetical order, and the caption.
    texts = svg_text(out / "opening_map.svg")
    labels = [t for t in texts if t in ALPHA]
    assert sorted(labels) == ALPHA
    assert any("game-level cluster bootstrap" in t for t in texts)
    # Small multiples list Models top to bottom in the same fixed order in every panel.
    sm = [t for t in svg_text(out / "small_multiples.svg") if t in ALPHA]
    assert len(sm) % 7 == 0 and len(sm) >= 7 * 10
    # Mirror Matches stay out of the cross-model panels.
    assert report["metrics"]["productivity"]["scope"] == "lobby"
    assert all(m["scope"] == "mirror" for m in report["mirror"].values())
    # Sabotage Damage is estimated on dry data for the Models whose lineups have Benign Matches.
    damage = report["metrics"]["sabotage_damage"]["per_model"]
    assert any(v["estimate"] is not None for v in damage.values())


def test_run_spend_comes_from_the_spend_log_and_mode_from_run_json(
    dry_run: Path, tmp_path: Path
) -> None:
    run = tmp_path / "run"
    shutil.copytree(dry_run, run, ignore=shutil.ignore_patterns("report"))
    # An aborted attempt's settled call and a reservation a crash never settled
    # count toward the run's spend, though no complete transcript records them.
    lines = [
        {"kind": "reserve", "reservation": 1, "reserved_usd": 0.5},
        {"kind": "call", "reservation": 1, "cost_usd": 0.25},
        {"kind": "reserve", "reservation": 2, "reserved_usd": 1.0},
    ]
    (run / "spend.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines), "utf-8")
    out, report = write_report(run, replicates=20)
    assert report["run"]["mode"] == "dry"
    assert report["run"]["run_spend_usd"] == 1.25
    assert report["run"]["total_cost_usd"] == 0
    assert "Spend: $1.25 for the whole run" in (out / "progress.md").read_text("utf-8")


@pytest.mark.parametrize("damage", ["malformed-middle", "torn-trailing"])
def test_a_corrupt_transcript_is_left_out_as_the_runner_does(
    dry_run: Path, tmp_path: Path, damage: str
) -> None:
    run = tmp_path / "run"
    shutil.copytree(dry_run, run, ignore=shutil.ignore_patterns("report"))
    path = next((run / "games").glob("b01-lobby-*.jsonl"))
    lines = path.read_text("utf-8").splitlines()
    if damage == "malformed-middle":
        i = next(i for i, line in enumerate(lines) if '"type": "vote"' in line)
        lines[i] = lines[i][:-5]
    else:
        lines.append('{"kind": "event", "rou')
    path.write_text("\n".join(lines) + "\n", "utf-8")
    assert not runs.is_complete(path)
    data = load_run(run)
    assert data.not_complete == [path.stem]
    assert path.stem not in {g.id for g in data.games}
    assert len(data.games) == len(load_run(dry_run).games) - 1
    out, report = write_report(run, replicates=20)
    assert report["run"]["transcripts_not_complete"] == [path.stem]
    assert f"`{path.stem}`" in (out / "progress.md").read_text("utf-8")


def test_a_dry_run_gives_the_analysis_real_completion_times(dry_run: Path) -> None:
    """Scripted stand-ins finish their work, so a dry run checks Productivity and
    Sabotage Damage with real numbers rather than every game at the round cap."""
    data = load_run(dry_run)
    benign = [g for g in data.games if g.kind == "benign"]
    lobbies = [g for g in data.games if g.kind == "lobby"]
    assert benign and all(g.finished for g in benign)
    assert any(g.finished for g in lobbies) and not all(g.finished for g in lobbies)
    assert len({g.restricted_completion for g in lobbies}) > 3
    damage = build_report(data, replicates=50, seed=1)["metrics"]["sabotage_damage"]
    estimates = [e["estimate"] for e in damage["per_model"].values() if e["estimate"] is not None]
    assert estimates and statistics.mean(estimates) > 1
    assert len(set(estimates)) > 1


def test_report_is_deterministic_for_a_seed(dry_run: Path, tmp_path: Path) -> None:
    a = build_report(load_run(dry_run), replicates=100, seed=9)
    b = build_report(load_run(dry_run), replicates=100, seed=9)
    assert (
        a["metrics"]["correct_vote_share"]["per_model"]
        == (b["metrics"]["correct_vote_share"]["per_model"])
    )


def test_report_renders_on_a_partial_run_and_on_mirror_only_data(
    dry_run: Path, tmp_path: Path
) -> None:
    partial = tmp_path / "partial"
    create_run(partial, json.loads((dry_run / "manifest.json").read_text("utf-8")))
    execute(partial, mode="dry", max_games=3)
    out, report = write_report(partial, replicates=50)
    check_outputs(out, report)
    assert report["progress"]["blocks_complete"] == 0

    mirror_only = tmp_path / "mirror"
    shutil.copytree(dry_run, mirror_only, ignore=shutil.ignore_patterns("report"))
    for path in (mirror_only / "games").glob("b*.jsonl"):
        path.unlink()
    out, report = write_report(mirror_only, replicates=50)
    check_outputs(out, report)
    assert report["metrics"]["productivity"]["per_model"][ALPHA[0]]["estimate"] is None
    mirrors = [g for g in load_run(mirror_only).games if g.lineup == [ALPHA[0]]]
    assert mirrors
    expected = sum(g.restricted_completion for g in mirrors) / len(mirrors)
    estimate = report["mirror"]["mirror_productivity"]["per_model"][ALPHA[0]]["estimate"]
    assert estimate == pytest.approx(expected, abs=1e-6)


def test_costs_size_the_dots_and_claim_audit_reports_are_picked_up(
    dry_run: Path, tmp_path: Path
) -> None:
    run = tmp_path / "run"
    shutil.copytree(dry_run, run, ignore=shutil.ignore_patterns("report"))
    claims = {
        "claims": [
            {"seat": s, "verdict": v, "lie": s < 2}
            for s in range(10)
            for v in ("true", "false", "unverifiable")
        ]
    }
    for path in (run / "games").glob("b01-lobby-0*.jsonl"):
        path.with_name(path.name + ".claims.json").write_text(json.dumps(claims), "utf-8")
    data = load_run(run)
    for g in data.games:
        for s in g.seats:
            s.cost_usd = 0.5 + ALPHA.index(s.model) * 0.25
    report = build_report(data, replicates=100)
    assert report["metrics"]["cost_per_game"]["per_model"][ALPHA[0]]["estimate"] == 5.0
    assert report["metrics"]["reliability"]["per_model"][ALPHA[0]]["estimate"] == 0.5
    lies = [v["estimate"] for v in report["metrics"]["lie_rate"]["per_model"].values()]
    assert any(v is not None and v > 0 for v in lies)
    out = tmp_path / "out"
    out.mkdir()
    render_all(report, out)
    assert any("Dot area proportional to cost" in t for t in svg_text(out / "opening_map.svg"))


def test_cli_report(dry_run: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "cli"
    assert (
        main(["report", str(dry_run), "--replicates", "40", "--seed", "2", "--out", str(out)]) == 0
    )
    assert "blocks complete 1 of 12" in capsys.readouterr().out
    method = json.loads((out / "metrics.json").read_text("utf-8"))["method"]
    assert (method["replicates"], method["seed"]) == (40, 2)


def test_intervals_not_containing_their_estimate_are_drawn_from_their_bounds(
    dry_run: Path, tmp_path: Path
) -> None:
    report = build_report(load_run(dry_run), replicates=50)
    for key in ("productivity", "correct_vote_share", "cost_per_game", "sabotage_damage"):
        for i, v in enumerate(report["metrics"][key]["per_model"].values()):
            if v["estimate"] is not None:
                shift = 1 + abs(v["estimate"])
                # Wholly above the estimate for some Models, wholly below for others.
                v["ci_low"], v["ci_high"] = (
                    (v["estimate"] + shift, v["estimate"] + 2 * shift)
                    if i % 2
                    else (v["estimate"] - 2 * shift, v["estimate"] - shift)
                )
    out = tmp_path / "out"
    out.mkdir()
    render_all(report, out)
    assert (out / "opening_map.svg").stat().st_size > 1000


def test_cli_report_with_a_single_replicate(dry_run: Path, tmp_path: Path) -> None:
    """One replicate: a percentile 'interval' is one draw, often off the estimate."""
    out = tmp_path / "one"
    assert (
        main(["report", str(dry_run), "--replicates", "1", "--seed", "0", "--out", str(out)]) == 0
    )
    assert (out / "metrics.json").is_file() and (out / "progress.md").is_file()


def test_captions_call_the_intervals_marginal(dry_run: Path) -> None:
    out, _ = write_report(dry_run, replicates=20)
    texts = " ".join(svg_text(out / "opening_map.svg"))
    assert "marginal 95% intervals" in texts and "joint" not in texts


def test_public_metrics_hold_no_seeds(dry_run: Path) -> None:
    out, _ = write_report(dry_run, replicates=20)
    private = json.loads((out / "metrics.json").read_text("utf-8"))
    public = json.loads((out / "metrics.public.json").read_text("utf-8"))
    assert private["run"]["master_seed"] == 3 and "seed" in private["games"][0]
    assert "master_seed" not in public["run"] and "run_dir" not in public["run"]
    assert all("seed" not in g for g in public["games"])
    text = (out / "metrics.public.json").read_text("utf-8")
    for g in private["games"]:
        assert f'"seed": {g["seed"]}' not in text
    assert public["metrics"] == private["metrics"]


def test_report_refuses_a_directory_without_a_manifest(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["report", str(tmp_path)])
