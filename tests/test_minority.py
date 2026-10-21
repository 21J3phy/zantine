"""v1.3 Minority Disadvantage (#23): the Lopsided Lobby plan, dry runs, the analysis.

The mask-on half needs ``GameConfig.style_mask`` (#66). Until it lands, tests that
play a mask-on game are skipped and a test checks that such games are refused
rather than silently played without the mask; once #66 is in, they run.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from zantine import runs
from zantine.analysis.minority import (
    LobbyGame,
    MinorityData,
    Target,
    build_minority_report,
    cluster_weights,
    load_minority_run,
    lobby_game,
    setup_signature,
    transcript_digest,
    write_minority_report,
)
from zantine.cli import main
from zantine.config import GameConfig
from zantine.harness.live import play_live
from zantine.rng import GameRng
from zantine.roles import Role
from zantine.runner import new_game
from zantine.runs import create_run, execute, game_setup, load_manifest, read_jsonl
from zantine.scenario import build_scenario
from zantine.schedule import (
    load_pool,
    make_manifest,
    make_minority_manifest,
    seat_lineup,
    summary,
    verify,
)

POOL = load_pool()
ALPHA = sorted(POOL)
HAS_MASK = hasattr(GameConfig, "style_mask")
needs_mask = pytest.mark.skipif(not HAS_MASK, reason="the Style Mask arrives with #66")
RANKING_WORDS = re.compile(
    r"\b(winners?|best|worst|top|rank(ed|ing|s)?|leaderboard|beats?)\b", re.I
)


@pytest.fixture(scope="module")
def plan() -> dict[str, Any]:
    return make_minority_manifest(POOL, 20261020)


def mask_off_only(manifest: dict[str, Any]) -> dict[str, Any]:
    """The mask-off half of a plan (playable without #66)."""
    out = copy.deepcopy(manifest)
    out["games"] = [g for g in out["games"] if not g["style_mask"]]
    return out


# The plan --------------------------------------------------------------------------------


def test_the_plan_has_168_games_42_ordered_pairs_twice_in_each_condition(
    plan: dict[str, Any],
) -> None:
    games = plan["games"]
    assert len(games) == 168 and plan["experiment"] == "minority"
    assert verify(plan) == []
    cells = Counter((g["majority"], g["minority"], g["style_mask"]) for g in games)
    ordered = [(a, b) for a in POOL for b in POOL if a != b]
    assert len(ordered) == 42
    assert cells == {(a, b, m): 2 for a, b in ordered for m in (False, True)}
    # Both directions of every pair.
    for a, b in ordered:
        assert cells[(a, b, False)] == cells[(b, a, False)] == 2
    assert Counter(g["style_mask"] for g in games) == {False: 84, True: 84}
    assert "168 games" in summary(plan)


def test_impostors_are_majority_seats_and_the_minority_are_always_crewmates(
    plan: dict[str, Any],
) -> None:
    for g in plan["games"]:
        assert g["kind"] == "lopsided"
        assert g["lineup"] == [g["majority"]] * 8 + [g["minority"]] * 2
        assert g["impostors"] == [g["majority"]] * 2
        entries, positions = seat_lineup(g)
        assert len(entries) == 10 and len(positions) == 2
        assert all(entries[p] == g["majority"] for p in positions)


def test_the_role_constraint_holds_in_every_mask_off_game_setup(plan: dict[str, Any]) -> None:
    for g in plan["games"]:
        if g["style_mask"]:
            continue
        config, spec = game_setup(plan, g, "dry")
        scenario = build_scenario(config, GameRng(config.seed).fork("scenario"))
        seated = scenario.seat(seat_lineup(g)[0])
        roles = dict(zip(range(10), scenario.roles, strict=True))
        assert Counter(seated) == {g["majority"]: 8, g["minority"]: 2}
        assert (
            sorted(m for s, m in enumerate(seated) if roles[s] is Role.IMPOSTOR)
            == [g["majority"]] * 2
        )
        assert all(roles[s] is Role.CREWMATE for s, m in enumerate(seated) if m == g["minority"])
        assert spec.count(f"dry:{g['minority']}") == 2


def test_mask_off_and_mask_on_twins_share_one_seed_and_setups_do_not(
    plan: dict[str, Any],
) -> None:
    by_setup: dict[str, list[dict[str, Any]]] = {}
    for g in plan["games"]:
        by_setup.setdefault(g["setup"], []).append(g)
    assert len(by_setup) == 84
    for twins in by_setup.values():
        assert sorted(g["style_mask"] for g in twins) == [False, True]
        assert len({g["seed"] for g in twins}) == 1
        assert [g["config"] for g in sorted(twins, key=lambda g: g["style_mask"])] == [
            {},
            {"style_mask": True},
        ]
    assert len({t[0]["seed"] for t in by_setup.values()}) == 84


def test_waves_run_in_order_and_each_holds_every_pair_in_both_conditions(
    plan: dict[str, Any],
) -> None:
    waves = [g["wave"] for g in plan["games"]]
    assert waves == [1] * 84 + [2] * 84
    first = plan["games"][:84]
    assert len({(g["majority"], g["minority"], g["style_mask"]) for g in first}) == 84
    # Shuffled within a wave: the conditions are interleaved.
    assert len({g["style_mask"] for g in first[:10]}) == 2


def test_the_plan_is_seeded() -> None:
    a, b = make_minority_manifest(POOL, 9), make_minority_manifest(POOL, 9)
    c = make_minority_manifest(POOL, 10)
    assert a == b
    assert [g["id"] for g in a["games"]] != [g["id"] for g in c["games"]]
    assert {g["seed"] for g in a["games"]}.isdisjoint({g["seed"] for g in c["games"]})


def broken(plan: dict[str, Any], edit: Any) -> list[str]:
    bad = copy.deepcopy(plan)
    edit(bad)
    return verify(bad)


@pytest.mark.parametrize(
    "edit, problem",
    [
        (lambda m: m["games"][0].update(impostors=[m["games"][0]["minority"]] * 2), "Impostor"),
        (
            lambda m: m["games"][0].update(
                impostors=[m["games"][0]["majority"], m["games"][0]["minority"]]
            ),
            "Impostor",
        ),
        (lambda m: m["games"].pop(), "games, expected"),
        (lambda m: m["games"][3].update(config={}), "config does not match"),
        (lambda m: m["games"][5].update(seed=m["games"][5]["seed"] + 1), "seed"),
        (lambda m: m["games"][2].update(lineup=m["games"][2]["lineup"][:9]), "lineup"),
        (lambda m: m["config"].update(style_mask=True), "run-wide config"),
        (lambda m: m["games"].reverse(), "index"),
    ],
)
def test_verify_catches_a_broken_plan(plan: dict[str, Any], edit: Any, problem: str) -> None:
    problems = broken(plan, edit)
    assert any(problem in p for p in problems), problems


def test_verify_catches_a_missing_direction(plan: dict[str, Any]) -> None:
    g = next(g for g in plan["games"] if g["majority"] == POOL[0] and g["minority"] == POOL[1])
    swapped = copy.deepcopy(plan)
    for h in swapped["games"]:
        if (h["majority"], h["minority"]) == (POOL[1], POOL[0]):
            h["majority"], h["minority"] = g["majority"], g["minority"]
    assert any("ordered pairs" in p for p in verify(swapped))


def test_the_main_plan_is_unchanged() -> None:
    manifest = make_manifest(POOL, 1, blocks=1)
    assert "experiment" not in manifest and verify(manifest) == []


# CLI and runs ---------------------------------------------------------------------------


def test_cli_schedules_the_minority_experiment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / "md"
    args = ["schedule", "--experiment", "minority", "--master-seed", "5", "--out", str(run_dir)]
    assert main(args) == 0
    assert "168 games" in capsys.readouterr().out
    assert load_manifest(run_dir) == make_minority_manifest(POOL, 5)
    with pytest.raises(SystemExit):
        main(["schedule", "--experiment", "minority", "--blocks", "2"])
    assert "do not apply to --experiment minority" in capsys.readouterr().err


@pytest.mark.skipif(HAS_MASK, reason="the engine has the Style Mask (#66)")
def test_mask_on_games_are_refused_without_the_style_mask(
    tmp_path: Path, plan: dict[str, Any]
) -> None:
    run_dir = tmp_path / "md"
    create_run(run_dir, plan)
    with pytest.raises(ValueError, match="#66"):
        execute(run_dir, mode="dry")
    assert not (run_dir / "games").exists()  # nothing was played
    masked = next(g for g in plan["games"] if g["style_mask"])
    with pytest.raises(ValueError, match="Style Mask"):
        game_setup(plan, masked, "dry")


@needs_mask
def test_live_mask_on_runs_need_the_pinned_masker(tmp_path: Path, plan: dict[str, Any]) -> None:
    run_dir = tmp_path / "md"
    create_run(run_dir, plan)
    with pytest.raises(ValueError, match="masker"):
        execute(run_dir, mode="live", spend_cap_usd=1.0, adapter_factory=lambda m: None)


@pytest.fixture(scope="module")
def off_run(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> Path:
    """Every mask-off game of the plan, played by dry-run bots."""
    run_dir = tmp_path_factory.mktemp("minority") / "off"
    create_run(run_dir, mask_off_only(plan))
    report = execute(run_dir, mode="dry")
    assert report.finished and report.complete == 84
    return run_dir


def test_a_mask_off_dry_run_plays_end_to_end(off_run: Path) -> None:
    manifest = load_manifest(off_run)
    for line in read_jsonl(off_run / "index.jsonl"):
        assert line["status"] == "complete" and line["cost_usd"] == 0.0
    for g in manifest["games"]:
        header = read_jsonl(off_run / "games" / f"{g['id']}.jsonl")[0]
        runs.check_seating(g, header)
        minority = [s for s in header["seats"] if s["controller"]["model"] == g["minority"]]
        assert len(minority) == 2 and {s["role"] for s in minority} == {"crewmate"}


def test_the_report_renders_from_a_mask_off_dry_run(
    off_run: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "report"
    assert main(["report", str(off_run), "--out", str(out), "--replicates", "200"]) == 0
    printed = capsys.readouterr().out
    assert "84/84 games (Minority Disadvantage)" in printed
    report = json.loads((out / "minority.json").read_text("utf-8"))
    assert report["models"] == ALPHA
    assert list(report["disadvantage"]["by_majority"]) == ALPHA
    assert list(report["disadvantage"]["by_minority"]) == ALPHA
    pooled = report["disadvantage"]["pooled"]
    assert pooled["off"]["disadvantage"]["n_games"] == 84
    assert pooled["off"]["minority_rate"]["n_units"] == 168
    assert pooled["off"]["majority_rate"]["n_units"] == 84 * 6
    assert pooled["mask"]["disadvantage"]["estimate"] is None  # no mask-on games yet
    assert report["verdict"]["text"].startswith("Dry run")
    assert "Out-group Bias" in report["verdict"]["text"]
    # Every ordered pair, in both directions, is in the per-pair table.
    assert len({(p["majority"], p["minority"]) for p in report["pairs"]}) == 42
    for stem in ("minority_disadvantage", "minority_rates", "scapegoating"):
        for ext in ("svg", "png"):
            assert (out / f"{stem}.{ext}").stat().st_size > 1000
    public = json.loads((out / "minority.public.json").read_text("utf-8"))
    assert "master_seed" not in public["run"] and "run_dir" not in public["run"]
    assert all("seed" not in g for g in public["games"])
    texts = [(out / "minority.md").read_text("utf-8"), (out / "minority.json").read_text("utf-8")]
    texts += [
        " ".join(re.findall(r"<text[^>]*>(.*?)</text>", p.read_text("utf-8"), flags=re.S))
        for p in out.glob("*.svg")
    ]
    for text in texts:
        assert not RANKING_WORDS.search(text), RANKING_WORDS.search(text)
    labels = re.findall(r"<text[^>]*>(.*?)</text>", (out / "minority_disadvantage.svg").read_text())
    shown = [t for t in labels if t in ALPHA]
    assert shown == ALPHA * 2  # both panels, fixed alphabetical order, top to bottom


@needs_mask
def test_a_dry_run_plays_mask_on_and_mask_off_twins_end_to_end(
    tmp_path: Path, plan: dict[str, Any]
) -> None:
    run_dir = tmp_path / "md"
    twins = [g for g in plan["games"] if g["setup"] == plan["games"][0]["setup"]]
    small = copy.deepcopy(plan)
    small["games"] = sorted(twins, key=lambda g: g["style_mask"])
    create_run(run_dir, small)
    assert execute(run_dir, mode="dry").finished
    headers = {
        g["id"]: read_jsonl(run_dir / "games" / f"{g['id']}.jsonl")[0] for g in small["games"]
    }
    off, on = (headers[g["id"]] for g in small["games"])
    assert off["config"]["style_mask"] is False and on["config"]["style_mask"] is True
    assert off["seed"] == on["seed"] and off["seats"] == on["seats"]  # matched setup
    _, report = write_minority_report(run_dir, replicates=50)
    assert report["run"]["by_condition"] == {
        "off": {"planned": 1, "complete": 1},
        "mask": {"planned": 1, "complete": 1},
    }


# Measuring one game ---------------------------------------------------------------------


def played(plan: dict[str, Any], index: int = 0) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    g = [g for g in plan["games"] if not g["style_mask"]][index]
    config, spec = game_setup(plan, g, "dry")
    game = new_game(config, spec)
    game.run()
    return g, game.transcript.records


def test_a_game_is_measured_from_its_transcript(plan: dict[str, Any]) -> None:
    for i in range(6):
        g, records = played(plan, i)
        lg = lobby_game(g, records)
        header = records[0]
        roles = {s["seat"]: s["role"] for s in header["seats"]}
        models = {s["seat"]: s["controller"]["model"] for s in header["seats"]}
        ejected = [r["data"]["seat"] for r in records if r.get("type") == "ejection"]
        assert lg.minority_crew == 2 and lg.majority_crew == 6
        assert lg.minority_ejected == sum(models[s] == g["minority"] for s in ejected)
        assert lg.majority_ejected == sum(
            models[s] == g["majority"] and roles[s] == "crewmate" for s in ejected
        )
        assert lg.condition == "off" and lg.accusations is None
        for t in lg.votes:
            assert 0 < t.base < 1 or (t.base == 0 and not t.minority)
            assert t.impostor_model == g["majority"]


def alive_before(records: list[dict[str, Any]], seq: int) -> set[int]:
    """Seats not killed or ejected before transcript record ``seq``."""
    out = {s["seat"] for s in records[0]["seats"]}
    for r in records:
        if r.get("kind") == "event" and r["seq"] < seq:
            if r["type"] == "kill":
                out.discard(r["data"]["victim"])
            elif r["type"] == "ejection":
                out.discard(r["data"]["seat"])
    return out


def test_impostor_votes_and_their_base_rate_come_from_ground_truth(plan: dict[str, Any]) -> None:
    g, records = played(plan)
    header = records[0]
    roles = {s["seat"]: s["role"] for s in header["seats"]}
    models = {s["seat"]: s["controller"]["model"] for s in header["seats"]}
    minority = {s for s, m in models.items() if m == g["minority"]}
    # Point every Impostor ballot at a living Crewmate (a minority one when there is
    # one, alternating by Meeting) and work out by hand what should be counted.
    expected: dict[tuple[int, int], tuple[bool, float]] = {}
    choice: dict[int, tuple[int, float]] = {}
    for r in records:
        if r.get("kind") != "event":
            continue
        if r["type"] == "meeting_started":
            alive = alive_before(records, r["seq"])
            crew = sorted(s for s in alive if roles[s] == "crewmate")
            living_min = [s for s in crew if s in minority]
            meeting = r["data"]["meeting"]
            pick = living_min[0] if living_min and meeting % 2 else crew[-1]
            choice[meeting] = (pick, len(living_min) / len(crew))
        elif r["type"] == "vote" and roles[r["data"]["seat"]] == "impostor":
            pick, base = choice[r["data"]["meeting"]]
            r["data"].update(value=pick, valid=True)
            expected[(r["data"]["meeting"], r["data"]["seat"])] = (pick in minority, base)
    assert expected
    lg = lobby_game(g, records)
    assert sorted((t.minority, t.base) for t in lg.votes) == sorted(expected.values())
    assert {t.impostor_model for t in lg.votes} == {g["majority"]}


def test_judged_accusations_are_read_only_from_the_same_transcript(plan: dict[str, Any]) -> None:
    g, records = played(plan)
    roles = {s["seat"]: s["role"] for s in records[0]["seats"]}
    models = {s["seat"]: s["controller"]["model"] for s in records[0]["seats"]}
    impostor = next(s for s, r in roles.items() if r == "impostor")
    minority = sorted(s for s, m in models.items() if m == g["minority"])
    start = next(r for r in records if r.get("type") == "meeting_started")
    meeting = start["data"]["meeting"]
    living_min = [s for s in minority if s in alive_before(records, start["seq"])]
    target = living_min[0] if living_min else minority[0]
    positions = [
        {"meeting": meeting, "seat": impostor, "target": target},
        {"meeting": meeting, "seat": minority[0], "target": impostor},  # not an Impostor's
        {"meeting": meeting, "seat": impostor, "target": "skip"},
    ]
    audit = {"judgments": {"transcript_sha256": transcript_digest(records), "positions": positions}}
    lg = lobby_game(g, records, audit)
    assert lg.accusations is not None
    # A dead target is not a living Crewmate, so it is not counted.
    assert [t.minority for t in lg.accusations] == ([True] if living_min else [])
    stale = {"judgments": {"transcript_sha256": "0" * 64, "positions": positions}}
    assert lobby_game(g, records, stale).accusations is None


def test_a_game_played_in_the_wrong_condition_is_refused(plan: dict[str, Any]) -> None:
    g, records = played(plan)
    wrong = dict(g, style_mask=True)
    with pytest.raises(ValueError, match="played with it off"):
        lobby_game(wrong, records)


# Planted recovery ----------------------------------------------------------------------


def planted(
    *,
    minority_off: float,
    minority_mask: float,
    majority: float = 0.10,
    only_majority: str | None = None,
    scapegoat: float | None = None,
    waves: int = 2,
    seed: int = 0,
) -> MinorityData:
    """Synthetic Lopsided Lobbies with known wrongful-ejection probabilities.

    With ``only_majority`` the minority's excess applies only when that Model is the
    majority; elsewhere the minority is ejected at the majority rate.
    """
    rng = np.random.default_rng(seed)
    manifest = make_minority_manifest(POOL, 1, games_per_pair=waves)
    games = []
    for g in manifest["games"]:
        mask = g["style_mask"]
        p_min = minority_mask if mask else minority_off
        if only_majority is not None and g["majority"] != only_majority:
            p_min = majority
        votes = []
        if scapegoat is not None:
            for _ in range(4):
                base = 0.25
                votes.append(Target(bool(rng.random() < scapegoat), base, g["majority"]))
        games.append(
            LobbyGame(
                id=g["id"],
                setup=g["setup"],
                majority=g["majority"],
                minority=g["minority"],
                condition="mask" if mask else "off",
                minority_crew=2,
                minority_ejected=int(rng.binomial(2, p_min)),
                majority_crew=6,
                majority_ejected=int(rng.binomial(6, majority)),
                votes=votes,
            )
        )
    return MinorityData(run_dir=None, manifest=manifest, games=games)


def contains(e: dict[str, Any], value: float) -> bool:
    return e["ci_low"] <= value <= e["ci_high"]


def test_a_planted_disadvantage_that_the_mask_removes_is_recovered() -> None:
    data = planted(minority_off=0.35, minority_mask=0.10)
    report = build_minority_report(data, replicates=1000, seed=3, run_mode="live")
    pooled = report["disadvantage"]["pooled"]
    assert contains(pooled["off"]["disadvantage"], 0.25)
    assert pooled["off"]["disadvantage"]["ci_low"] > 0
    assert contains(pooled["mask"]["disadvantage"], 0.0)
    assert pooled["change"]["ci_low"] > 0 and contains(pooled["change"], 0.25)
    assert contains(pooled["off"]["minority_rate"], 0.35)
    assert contains(pooled["off"]["majority_rate"], 0.10)
    assert report["verdict"]["status"] == "consistent_with_out_group_bias"


def test_a_disadvantage_the_mask_does_not_remove_is_not_called_out_group_bias() -> None:
    data = planted(minority_off=0.35, minority_mask=0.35)
    report = build_minority_report(data, replicates=1000, seed=3, run_mode="live")
    assert report["disadvantage"]["pooled"]["mask"]["disadvantage"]["ci_low"] > 0
    assert report["verdict"]["status"] == "not_removed"
    assert "not claimed" in report["verdict"]["text"]


def test_no_planted_disadvantage_claims_nothing() -> None:
    data = planted(minority_off=0.10, minority_mask=0.10)
    report = build_minority_report(data, replicates=1000, seed=3, run_mode="live")
    pooled = report["disadvantage"]["pooled"]
    assert contains(pooled["off"]["disadvantage"], 0.0)
    assert report["verdict"]["status"] == "no_disadvantage"


def test_a_disadvantage_planted_under_one_majority_model_shows_in_its_panel() -> None:
    target = POOL[2]
    data = planted(minority_off=0.60, minority_mask=0.60, only_majority=target, waves=4)
    report = build_minority_report(data, replicates=1000, seed=3, run_mode="live")
    by_majority = report["disadvantage"]["by_majority"]
    assert by_majority[target]["off"]["disadvantage"]["ci_low"] > 0
    assert contains(by_majority[target]["off"]["disadvantage"], 0.5)
    for m in POOL:
        if m != target:
            assert abs(by_majority[m]["off"]["disadvantage"]["estimate"]) < 0.2, m
    # As the minority, every Model faces the planted majority once, so no panel stands out.
    by_minority = report["disadvantage"]["by_minority"]
    assert all(by_minority[m]["off"]["disadvantage"]["estimate"] < 0.3 for m in POOL)


def test_planted_scapegoating_is_recovered_against_the_base_rate() -> None:
    data = planted(minority_off=0.1, minority_mask=0.1, scapegoat=0.55)
    report = build_minority_report(data, replicates=1000, seed=3, run_mode="live")
    votes = report["scapegoating"]["votes"]["pooled"]["off"]
    assert votes["base_rate"]["estimate"] == pytest.approx(0.25)
    assert contains(votes["excess"], 0.30) and votes["excess"]["ci_low"] > 0
    assert "accusations" not in report["scapegoating"]
    null = planted(minority_off=0.1, minority_mask=0.1, scapegoat=0.25, seed=1)
    excess = build_minority_report(null, replicates=1000, seed=3, run_mode="live")["scapegoating"]
    assert contains(excess["votes"]["pooled"]["off"]["excess"], 0.0)


def test_the_bootstrap_resamples_setups_so_twins_move_together(plan: dict[str, Any]) -> None:
    data = planted(minority_off=0.2, minority_mask=0.1)
    w = cluster_weights(data.games, 50, 0)
    by_setup: dict[str, list[int]] = {}
    for i, g in enumerate(data.games):
        by_setup.setdefault(g.setup, []).append(i)
    for idx in by_setup.values():
        assert len(idx) == 2
        assert np.array_equal(w[:, idx[0]], w[:, idx[1]])
    assert np.allclose(w.sum(axis=1), 2 * len(by_setup))


# Review round 1: matched contrast, removal, plan binding, provenance ----------------------


def report_of(data: MinorityData) -> dict[str, Any]:
    return build_minority_report(data, replicates=1000, seed=3, run_mode="live")


def test_a_partial_reduction_is_reported_as_a_reduction_not_out_group_bias() -> None:
    # The reviewer's case: the mask cuts 0.40 to 0.06, and 0.11 is still in the interval.
    data = planted(minority_off=0.50, minority_mask=0.16, seed=5)
    report = build_minority_report(data, replicates=2000, seed=0, run_mode="live")
    pooled = report["disadvantage"]["pooled"]
    assert pooled["change"]["ci_low"] > 0
    assert pooled["mask"]["disadvantage"]["ci_high"] >= 0.10
    assert report["verdict"]["status"] == "reduced"
    assert "not claimed" in report["verdict"]["text"]
    # A larger residual is a reduction too, never removal.
    bigger = report_of(planted(minority_off=0.50, minority_mask=0.25))
    assert bigger["verdict"]["status"] in ("reduced", "not_removed")


def test_a_two_game_prefix_of_different_setups_reads_nothing() -> None:
    manifest = make_minority_manifest(POOL, 0)
    first, second = manifest["games"][:2]
    assert first["setup"] != second["setup"] and first["style_mask"] != second["style_mask"]

    def game(g: dict[str, Any], ejected: int) -> LobbyGame:
        return LobbyGame(
            id=g["id"],
            setup=g["setup"],
            majority=g["majority"],
            minority=g["minority"],
            condition="mask" if g["style_mask"] else "off",
            minority_crew=2,
            minority_ejected=ejected,
            majority_crew=6,
            majority_ejected=0,
        )

    off = first if not first["style_mask"] else second
    on = second if off is first else first
    data = MinorityData(run_dir=None, manifest=manifest, games=[game(off, 2), game(on, 0)])
    report = report_of(data)
    assert report["coverage"]["twinned_setups"] == 0
    assert report["disadvantage"]["pooled"]["change"]["estimate"] is None
    assert report["verdict"]["status"] == "awaiting_complete_run"


def test_the_mask_change_uses_twinned_setups_only() -> None:
    # Unpaired setups with a different baseline must not move the matched contrast.
    data = planted(minority_off=0.35, minority_mask=0.10)
    twins = {g.setup for g in data.games[::4]}
    kept = []
    for g in data.games:
        if g.setup in twins:
            kept.append(g)
        elif g.condition == "off":  # its mask twin is missing; make it look extreme
            g.minority_ejected = 2
            kept.append(g)
    partial = MinorityData(run_dir=None, manifest=data.manifest, games=kept)
    report = report_of(partial)
    matched = MinorityData(
        run_dir=None, manifest=data.manifest, games=[g for g in kept if g.setup in twins]
    )
    want = report_of(matched)["disadvantage"]["pooled"]["change"]["estimate"]
    assert report["disadvantage"]["pooled"]["change"]["estimate"] == pytest.approx(want)
    assert report["coverage"]["twinned_setups"] == len(twins) < 84
    assert report["verdict"]["status"] == "awaiting_complete_run"


def test_transcripts_that_are_not_the_planned_game_are_left_out(
    off_run: Path, tmp_path: Path
) -> None:
    run_dir = tmp_path / "swapped"
    shutil.copytree(off_run, run_dir, ignore=shutil.ignore_patterns("report"))
    games = run_dir / "games"
    manifest = load_manifest(run_dir)
    # Two waves of the same ordered pair, each transcript under the other's name.
    a = manifest["games"][0]
    b = next(
        g
        for g in manifest["games"]
        if (g["majority"], g["minority"]) == (a["majority"], a["minority"]) and g is not a
    )
    pa, pb = games / f"{a['id']}.jsonl", games / f"{b['id']}.jsonl"
    ta, tb = pa.read_text("utf-8"), pb.read_text("utf-8")
    pa.write_text(tb, "utf-8")
    pb.write_text(ta, "utf-8")
    # A complete transcript whose minority Seats are an out-of-pool Model.
    c = manifest["games"][5]
    pc = games / f"{c['id']}.jsonl"
    pc.write_text(pc.read_text("utf-8").replace(f'"{c["minority"]}"', '"not-a-model"'), "utf-8")
    data = load_minority_run(run_dir)
    left_out = {m["game"]: m["reason"] for m in data.mismatched}
    assert set(left_out) == {a["id"], b["id"], c["id"]}
    assert "seed" in left_out[a["id"]] and "seats" in left_out[c["id"]]
    assert {g.id for g in data.games}.isdisjoint(left_out)
    report = build_minority_report(data, replicates=50)
    assert {m["game"] for m in report["run"]["transcripts_not_matching_plan"]} == set(left_out)
    # No game's seed reaches the public report, not even inside a reason.
    out, _ = write_minority_report(run_dir, replicates=50)
    public = (out / "minority.public.json").read_text("utf-8")
    assert "transcripts_not_matching_plan" in public
    for g in manifest["games"]:
        assert str(g["seed"]) not in public, g["id"]


def replay_altered(run_dir: Path, game: dict[str, Any], *, round_cap=None, swap=None) -> None:
    """Play the planned game with one thing changed and store it under the game's name."""
    manifest = load_manifest(run_dir)
    config, spec = game_setup(manifest, game, "dry")
    if round_cap is not None:
        config = dataclasses.replace(config, round_cap=round_cap)
    entries = spec.split(",")
    if swap is not None:
        i, j = swap
        entries[i], entries[j] = entries[j], entries[i]
    run = play_live(config, ",".join(entries), spend_cap_usd=runs.DRY_RUN_CAP_USD)
    assert run.aborted is None
    (run_dir / "games" / f"{game['id']}.jsonl").write_text(run.game.transcript.to_jsonl(), "utf-8")


def test_a_transcript_with_other_settings_or_seating_is_left_out(
    off_run: Path, tmp_path: Path
) -> None:
    run_dir = tmp_path / "altered"
    shutil.copytree(off_run, run_dir, ignore=shutil.ignore_patterns("report"))
    manifest = load_manifest(run_dir)
    a, b = manifest["games"][:2]
    replay_altered(run_dir, a, round_cap=1)  # the right seed and lineup, another round cap
    replay_altered(run_dir, b, swap=(2, 8))  # 8/2 and both Impostors kept, minority moved
    assert read_jsonl(run_dir / "games" / f"{b['id']}.jsonl")[0]["seed"] == b["seed"]
    data = load_minority_run(run_dir)
    assert data.binding == "planned"
    reasons = {m["game"]: m["reason"] for m in data.mismatched}
    assert set(reasons) == {a["id"], b["id"]}
    assert "round_cap" in reasons[a["id"]] and "seating" in reasons[b["id"]]
    assert {g.id for g in data.games}.isdisjoint(reasons)
    assert len(data.games) == 82


def test_twins_must_share_their_whole_setup() -> None:
    manifest = make_minority_manifest(POOL, 3)
    g = next(g for g in manifest["games"] if not g["style_mask"])
    config, spec = game_setup(manifest, g, "dry")
    played = new_game(config, spec)
    played.run()
    header = played.transcript.records[0]
    masked = copy.deepcopy(header)
    masked["config"]["style_mask"] = True
    assert setup_signature(masked) == setup_signature(header)  # only the mask differs
    other = copy.deepcopy(header)
    other["config"]["round_cap"] = 1
    assert setup_signature(other) != setup_signature(header)
    moved = copy.deepcopy(header)
    moved["seats"][0]["name"], moved["seats"][1]["name"] = (
        moved["seats"][1]["name"],
        moved["seats"][0]["name"],
    )
    assert setup_signature(moved) != setup_signature(header)


def svg_texts(out: Path) -> dict[str, str]:
    return {
        p.stem: " ".join(re.findall(r"<text[^>]*>(.*?)</text>", p.read_text("utf-8"), flags=re.S))
        for p in out.glob("*.svg")
    }


def test_every_graph_of_a_dry_run_says_so(off_run: Path, tmp_path: Path) -> None:
    out, report = write_minority_report(off_run, out_dir=tmp_path / "r", replicates=50)
    assert report["provenance"]["status"] == "dry"
    texts = svg_texts(out)
    assert set(texts) == {"minority_disadvantage", "minority_rates", "scapegoating"}
    for name, text in texts.items():
        assert "Dry run: scripted bots" in text, name
    assert "Dry run: scripted bots" in (out / "minority.md").read_text("utf-8")


@pytest.mark.parametrize(
    "run_json, status",
    [(None, "dry"), ({"mode": "live"}, "mixed")],
)
def test_unknown_or_mixed_provenance_is_never_shown_as_live(
    off_run: Path, tmp_path: Path, run_json: dict[str, str] | None, status: str
) -> None:
    run_dir = tmp_path / "copy"
    shutil.copytree(off_run, run_dir, ignore=shutil.ignore_patterns("report"))
    if run_json is None:
        (run_dir / "run.json").unlink()
    else:
        (run_dir / "run.json").write_text(json.dumps(run_json), "utf-8")
    out, report = write_minority_report(run_dir, replicates=50)
    assert report["provenance"]["status"] == status
    for name, text in svg_texts(out).items():
        assert report["provenance"]["label"].split(":")[0] in text, name


def test_live_seats_without_a_live_run_json_have_unknown_provenance() -> None:
    data = planted(minority_off=0.1, minority_mask=0.1)
    assert build_minority_report(data, replicates=20)["provenance"]["status"] == "unknown"
    live = build_minority_report(data, replicates=20, run_mode="live")
    assert live["provenance"] == {"status": "live", "label": None}
    assert build_minority_report(data, replicates=20, run_mode="dry")["provenance"]["status"] == (
        "mixed"
    )


def strip_protocol(path: Path) -> None:
    """Rewrite a transcript's header as it was written before #37 (no protocol field)."""
    lines = path.read_text("utf-8").splitlines()
    header = json.loads(lines[0])
    header["config"].pop("verification_protocol")
    lines[0] = json.dumps(header, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_run_from_before_verification_protocols_is_still_the_planned_run(
    off_run: Path, tmp_path: Path
) -> None:
    """Headers without ``verification_protocol`` were played under the optional protocol:
    an old completed run, and one resumed across #37 (mixed headers), load in full."""
    old = tmp_path / "old"
    shutil.copytree(off_run, old, ignore=shutil.ignore_patterns("report"))
    paths = sorted((old / "games").glob("*.jsonl"))
    for path in paths:
        strip_protocol(path)
    data = load_minority_run(old)
    assert data.mismatched == [] and len(data.games) == 84

    mixed = tmp_path / "mixed"
    shutil.copytree(off_run, mixed, ignore=shutil.ignore_patterns("report"))
    for path in sorted((mixed / "games").glob("*.jsonl"))[::2]:
        strip_protocol(path)
    data = load_minority_run(mixed)
    assert data.mismatched == [] and len(data.games) == 84

    # A recorded protocol other than the planned one is still a different setup.
    first = paths[0]
    lines = first.read_text("utf-8").splitlines()
    header = json.loads(lines[0])
    header["config"]["verification_protocol"] = "mandatory"
    lines[0] = json.dumps(header, sort_keys=True)
    first.write_text("\n".join(lines) + "\n", encoding="utf-8")
    data = load_minority_run(old)
    assert len(data.games) == 83
    [left_out] = data.mismatched
    assert "verification_protocol" in left_out["reason"]


def test_a_missing_protocol_signs_like_optional_and_unlike_another() -> None:
    manifest = make_minority_manifest(POOL, 3)
    g = next(g for g in manifest["games"] if not g["style_mask"])
    config, spec = game_setup(manifest, g, "dry")
    played = new_game(config, spec)
    played.run()
    header = played.transcript.records[0]
    assert header["config"]["verification_protocol"] == "optional"
    old = copy.deepcopy(header)
    old["config"].pop("verification_protocol")
    assert setup_signature(old) == setup_signature(header)
    other = copy.deepcopy(header)
    other["config"]["verification_protocol"] = "none"
    assert setup_signature(other) != setup_signature(header)


def test_baseline_is_refused_for_a_minority_report(off_run: Path, tmp_path: Path) -> None:
    out = tmp_path / "report"
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "report",
                str(off_run),
                "--baseline",
                str(tmp_path / "missing"),
                "--replicates",
                "20",
                "--out",
                str(out),
            ]
        )
    assert exc.value.code == 2
    assert not out.exists()
