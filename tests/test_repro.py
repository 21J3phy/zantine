"""Reproducibility (ADR 0001): Public Seed Set, Seed Commitments, release and verify."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from zantine import repro
from zantine.analysis.report import write_report
from zantine.cli import main
from zantine.runs import create_run, execute, transcript_path
from zantine.schedule import PUBLIC_SEED_BIT, game_seed, load_pool, make_manifest, verify

POOL = load_pool()
# Cheap scrypt for tests; the parameters are recorded in each commitment.
FAST_TAGS = {"n": 2, "r": 1, "p": 1, "dklen": 16}


@pytest.fixture(autouse=True)
def _fast_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repro, "TAG_PARAMS", FAST_TAGS)


@pytest.fixture(scope="module")
def official(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A finished official dry run (one block) with its report and commitment."""
    run = tmp_path_factory.mktemp("official") / "run"
    create_run(run, make_manifest(POOL, 42, blocks=1))
    execute(run, mode="dry")
    write_report(run, replicates=200, graphs=False)
    repro.commit(run, tag_params=FAST_TAGS)
    return run


@pytest.fixture(scope="module")
def released(official: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("release") / "v1"
    result = repro.release(official, out)
    assert result.ok, result.problems
    return out


@pytest.fixture
def copy(released: Path, tmp_path: Path) -> Path:
    """A private copy of the release to tamper with."""
    return Path(shutil.copytree(released, tmp_path / "release"))


def commitment(run: Path) -> dict[str, Any]:
    return json.loads((run / repro.COMMITMENT).read_text("utf-8"))


def edit_manifest(release: Path, change: Any) -> None:
    path = release / "manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    change(manifest)
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")


# Public Seed Set ------------------------------------------------------------------------


def test_public_seeds_are_disjoint_from_official_seeds_by_construction() -> None:
    public = repro.public_manifest(POOL)
    assert public["seed_domain"] == "public" and not verify(public)
    assert len(public["games"]) == 12 * 28 + 6 * 7
    assert all(g["seed"] >= PUBLIC_SEED_BIT for g in public["games"])
    for master in (0, 1, 42, 2**63 - 1, repro.PUBLIC_SET_ID):
        official = make_manifest(POOL, master, blocks=2, mirror=True)
        assert all(0 <= g["seed"] < PUBLIC_SEED_BIT for g in official["games"])
        assert repro.official_seed_problems(official) == []
    # Same master seed and game id, different domains: different seeds.
    assert game_seed(1, "b01-lobby-00") != game_seed(1, "b01-lobby-00", "public") & ~PUBLIC_SEED_BIT


def test_a_smaller_public_selection_is_a_subset_of_the_full_set() -> None:
    small = repro.public_manifest(POOL, blocks=2)
    full = {g["id"]: g["seed"] for g in repro.public_manifest(POOL)["games"]}
    assert all(full[g["id"]] == g["seed"] for g in small["games"])


def test_overlap_is_checked_seed_by_seed_not_only_by_range() -> None:
    official = make_manifest(POOL, 9, blocks=1)
    leaked = repro.public_manifest(POOL)["games"][5]["seed"]
    official["games"][0]["seed"] = leaked
    problems = repro.official_seed_problems(official)
    assert any("Public Seed Set" in p for p in problems)
    assert any("official range" in p for p in problems)
    assert verify(official)  # also not derived from the master seed


def test_membership_tags_find_a_public_seed_without_revealing_the_rest() -> None:
    official = make_manifest(POOL, 9, blocks=1)
    public = repro.public_seeds(POOL)
    clean = repro.build_commitment(
        official, {}, "dry", nonce="00" * 32, tag_salt="11" * 16, tag_params=FAST_TAGS
    )
    assert repro.overlap_with_tags(public, clean) == 0
    official["games"][3]["seed"] = sorted(public)[0]
    forged = repro.build_commitment(
        official, {}, "dry", nonce="00" * 32, tag_salt="11" * 16, tag_params=FAST_TAGS
    )
    assert repro.overlap_with_tags(public, forged) == 1
    # Tags are a sorted set: they do not say which game a seed belongs to.
    assert forged["seed_tags"]["tags"] == sorted(forged["seed_tags"]["tags"])


# Commit -> release -> verify ---------------------------------------------------------------


def test_commit_round_trip_verifies_and_replays_byte_identically(
    official: Path, released: Path
) -> None:
    c = commitment(official)
    result = repro.verify(c, released, expect=c["commitment_sha256"])
    assert result.ok, result.problems
    assert result.replayed == 28
    assert c["run"]["seed_range"] == "[0, 2**63)" and c["run"]["mode"] == "dry"
    seeds = json.loads((released / repro.SEEDS).read_text("utf-8"))
    assert seeds["master_seed"] == 42 and len(seeds["games"]) == 28


def test_published_commitment_reveals_no_seed(official: Path) -> None:
    text = (official / repro.COMMITMENT).read_text("utf-8")
    manifest = json.loads((official / "manifest.json").read_text("utf-8"))
    for value in [manifest["master_seed"], *(g["seed"] for g in manifest["games"])]:
        assert not re.search(rf"\b{value}\b", text)
    nonce = json.loads((official / repro.SECRET).read_text("utf-8"))["nonce"]
    assert nonce not in text


def test_commitments_are_deterministic_given_the_nonce(official: Path, tmp_path: Path) -> None:
    """A second dry run of the same plan writes byte-identical transcripts, so it
    commits to the same hashes."""
    again = tmp_path / "again"
    create_run(again, json.loads((official / "manifest.json").read_text("utf-8")))
    execute(again, mode="dry")
    c = commitment(official)
    secret = json.loads((official / repro.SECRET).read_text("utf-8"))
    manifest = json.loads((again / "manifest.json").read_text("utf-8"))
    rebuilt = repro.build_commitment(
        manifest,
        repro.read_transcripts(again, manifest),
        "dry",
        nonce=secret["nonce"],
        tag_salt=c["seed_tags"]["salt"],
        tag_params=c["seed_tags"]["params"],
    )
    assert rebuilt == c


def test_a_wrong_published_hash_fails(official: Path, released: Path) -> None:
    result = repro.verify(commitment(official), released, expect="0" * 64, replay=False)
    assert any("is not the published" in p for p in result.problems)


def test_one_changed_transcript_byte_is_caught(official: Path, copy: Path) -> None:
    game = json.loads((copy / "manifest.json").read_text("utf-8"))["games"][4]["id"]
    path = transcript_path(copy, game)
    data = bytearray(path.read_bytes())
    i = data.index(b'"round": ') + len(b'"round": ')
    data[i] = ord("9") if data[i] != ord("9") else ord("8")
    path.write_bytes(bytes(data))
    result = repro.verify(commitment(official), copy)
    assert f"{game}: transcript differs from the commitment" in result.problems
    assert "transcripts_sha256 does not match the commitment" in result.problems
    assert f"{game}: replaying its seed does not give a byte-identical transcript" in (
        result.problems
    )


def test_swapped_seeds_are_caught(official: Path, copy: Path) -> None:
    def swap(m: dict[str, Any]) -> None:
        g = m["games"]
        g[0]["seed"], g[1]["seed"] = g[1]["seed"], g[0]["seed"]

    edit_manifest(copy, swap)
    result = repro.verify(commitment(official), copy, replay=False)
    games = json.loads((copy / "manifest.json").read_text("utf-8"))["games"]
    for g in games[:2]:
        assert f"{g['id']}: seed or run position differs from the commitment" in result.problems
        assert f"manifest: {g['id']}: seed is not derived from the master seed" in result.problems
    assert "seed_list_sha256 does not match the commitment" in result.problems


def test_reordered_games_are_caught(official: Path, copy: Path) -> None:
    edit_manifest(copy, lambda m: m["games"].reverse())
    result = repro.verify(commitment(official), copy, replay=False)
    assert "seed_list_sha256 does not match the commitment" in result.problems
    assert "manifest_sha256 does not match the commitment" in result.problems


def test_a_missing_game_is_caught(official: Path, copy: Path) -> None:
    game = json.loads((copy / "manifest.json").read_text("utf-8"))["games"][7]["id"]
    transcript_path(copy, game).unlink()
    result = repro.verify(commitment(official), copy)
    assert result.problems == [
        "transcripts_sha256 does not match the commitment",
        f"{game}: transcript missing from the release",
    ]


def test_a_game_dropped_from_the_plan_is_caught(official: Path, copy: Path) -> None:
    dropped: list[str] = []
    edit_manifest(copy, lambda m: dropped.append(m["games"].pop(3)["id"]))
    result = repro.verify(commitment(official), copy, replay=False)
    assert f"{dropped[0]}: committed game missing from the release" in result.problems
    assert "run does not match the commitment" in result.problems


def test_an_edited_commitment_or_wrong_nonce_is_caught(official: Path, copy: Path) -> None:
    c = commitment(official)
    c["seed_tags"]["tags"][0] = "00" * 16
    assert "commitment.json was changed after it was hashed (commitment_sha256)" in (
        repro.verify(c, copy, replay=False).problems
    )
    (copy / repro.SECRET).write_text(json.dumps({"nonce": "ab" * 32}), encoding="utf-8")
    problems = repro.verify(commitment(official), copy, replay=False).problems
    assert "manifest_sha256 does not match the commitment" in problems
    assert len([p for p in problems if "differs from the commitment" in p]) == 2 * 28


def test_seeds_json_must_match_the_manifest(official: Path, copy: Path) -> None:
    seeds = json.loads((copy / repro.SEEDS).read_text("utf-8"))
    seeds["games"][0]["seed"] += 1
    (copy / repro.SEEDS).write_text(json.dumps(seeds), encoding="utf-8")
    result = repro.verify(commitment(official), copy, replay=False)
    assert result.problems == ["seeds.json does not match the released manifest"]


def test_commit_refuses_incomplete_twice_and_public_runs(tmp_path: Path) -> None:
    run = tmp_path / "run"
    create_run(run, make_manifest(POOL, 5, blocks=1))
    execute(run, mode="dry", max_games=3)
    with pytest.raises(ValueError, match="have no transcript"):
        repro.commit(run)
    _, c = repro.commit(run, allow_incomplete=True)
    assert len(c["games_without_transcript"]) == 25
    with pytest.raises(ValueError, match="committed once"):
        repro.commit(run, allow_incomplete=True)
    out = tmp_path / "release"
    result = repro.release(run, out)
    assert result.ok and result.replayed == 3, result.problems
    # A game committed as missing cannot appear later.
    missing = c["games_without_transcript"][0]
    shutil.copyfile(transcript_path(out, c["games"][0]["game"]), transcript_path(out, missing))
    problems = repro.verify(c, out, replay=False).problems
    assert f"{missing}: committed without a transcript, but the release has one" in problems

    public = tmp_path / "public"
    create_run(public, repro.public_manifest(POOL, blocks=1, mirror=False))
    execute(public, mode="dry", max_games=1)
    with pytest.raises(ValueError, match="not 'run'"):
        repro.commit(public, allow_incomplete=True)


def test_commit_refuses_a_transcript_of_another_seed(tmp_path: Path) -> None:
    run = tmp_path / "run"
    create_run(run, make_manifest(POOL, 5, blocks=1))
    execute(run, mode="dry", max_games=2)
    games = json.loads((run / "manifest.json").read_text("utf-8"))["games"]
    shutil.copyfile(transcript_path(run, games[0]["id"]), transcript_path(run, games[1]["id"]))
    with pytest.raises(ValueError, match="not the scheduled seed"):
        repro.commit(run, allow_incomplete=True)


# Public Seed Set rerun and comparison ---------------------------------------------------------


def test_dry_public_rerun_compares_with_official_intervals(official: Path, tmp_path: Path) -> None:
    result = repro.run_public(
        official / "report" / "metrics.public.json",
        mode="dry",
        out=tmp_path / "public",
        blocks=1,
        mirror=False,
        commitment_path=official / repro.COMMITMENT,
    )
    assert result["warnings"] == []  # same pool, engine, mode and round caps
    assert result["overlap"] == {
        "by_construction": True,
        "checked_against": ["commitment seed_tags"],
    }
    counts, rows = result["counts"], result["rows"]
    assert counts["within"] + counts["outside"] + counts["n/a"] == len(rows) > 0
    assert counts["within"] > 0
    for r in rows:
        assert r["reason"]
        if r["status"] == "within":
            lo, hi = r["official"][1:]
            assert lo <= r["public"][0] <= hi
    saved = json.loads(Path(result["path"]).read_text("utf-8"))
    assert saved["counts"] == counts and saved["run"]["complete"] == 28
    # Rerunning resumes the same directory; another selection there is refused.
    again = repro.run_public(
        official / "report" / "metrics.public.json",
        mode="dry",
        out=tmp_path / "public",
        blocks=1,
        mirror=False,
    )
    assert again["counts"] == counts
    with pytest.raises(ValueError, match="another run"):
        repro.run_public(
            official / "report" / "metrics.public.json", mode="dry", out=tmp_path / "public"
        )


def test_private_metrics_seeds_are_checked_too(official: Path, tmp_path: Path) -> None:
    result = repro.run_public(
        official / "report" / "metrics.json",
        mode="dry",
        out=tmp_path / "public",
        blocks=1,
        mirror=False,
        max_games=1,
    )
    assert result["overlap"]["checked_against"] == ["official metrics.json seeds"]


def metric(est: float | None, lo: float | None, hi: float | None) -> dict[str, Any]:
    return {"label": "x", "per_model": {"m": {"estimate": est, "ci_low": lo, "ci_high": hi}}}


@pytest.mark.parametrize(
    ("public", "status", "consistent", "words"),
    [
        ((0.5, 0.4, 0.6), "within", True, "inside"),
        ((0.65, 0.5, 0.8), "outside", True, "above the official interval"),
        ((0.95, 0.9, 1.0), "outside", False, "beyond sampling noise"),
        ((None, None, None), "n/a", None, "no public estimate"),
    ],
)
def test_comparison_says_why(
    public: tuple[Any, Any, Any], status: str, consistent: bool | None, words: str
) -> None:
    official = {"run": {}, "metrics": {"k": metric(0.5, 0.4, 0.6)}}
    result = repro.compare(official, {"run": {}, "metrics": {"k": metric(*public)}})
    (row,) = result["rows"]
    assert (row["status"], row["consistent"]) == (status, consistent)
    assert words in row["reason"]


def test_comparison_warns_when_runs_differ() -> None:
    o = {"run": {"pool": ["a"], "engine": {"v": 1}, "mode": "live", "round_caps": [30]}}
    p = {"run": {"pool": ["b"], "engine": {"v": 2}, "mode": "dry", "round_caps": [20]}}
    warnings = repro.comparability(o, p)
    assert len(warnings) == 4 and "checks the pipeline" in warnings[2]


@pytest.mark.parametrize(("mode", "warned"), [("live", False), ("dry", True)])
def test_only_the_pins_a_mode_plays_on_are_compared(mode: str, warned: bool) -> None:
    """Live Seats never play as the scripted bots, so a new bot version alone doesn't
    make two live runs incomparable; for dry runs it does."""
    engine = {"scenario_version": 6, "transcript_schema": 2, "harness": "neutral-v8"}
    o = {"run": {"pool": ["a"], "engine": engine, "mode": mode}}
    p = {"run": {"pool": ["a"], "engine": engine | {"scripted_bots": 2}, "mode": mode}}
    warnings = repro.comparability(o, p)
    assert any("engine versions differ" in w for w in warnings) == warned


# CLI ------------------------------------------------------------------------------------------


def test_cli_verify_overlap_and_release_guard(
    official: Path, released: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    c = official / repro.COMMITMENT
    expect = commitment(official)["commitment_sha256"]
    assert main(["verify", str(c), str(released), "--expect", expect, "--no-replay"]) == 0
    assert "OK: the release matches" in capsys.readouterr().out
    assert main(["repro", "overlap", "--commitment", str(c)]) == 0
    assert main(["repro", "overlap", "--manifest", str(official / "manifest.json")]) == 0
    assert "OK: no Public Seed Set seed" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["release", str(official), "--out", str(tmp_path / "r")])
    assert "can never be taken back" in capsys.readouterr().err
    assert not (tmp_path / "r").exists()
    broken = Path(shutil.copytree(released, tmp_path / "broken"))
    (broken / repro.SECRET).unlink()
    assert main(["verify", str(c), str(broken)]) == 1
    assert "release has no commitment.secret.json" in capsys.readouterr().out


def test_cli_commit_and_public_dry_run(
    official: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = tmp_path / "run"
    create_run(run, make_manifest(POOL, 6, blocks=1))
    execute(run, mode="dry")
    assert main(["commit", str(run)]) == 0
    assert "commitment_sha256" in capsys.readouterr().out
    official_metrics = official / "report" / "metrics.public.json"
    args = ["repro", "public", "--dry-run", "--official", str(official_metrics)]
    args += ["--out", str(tmp_path / "pub"), "--blocks", "1", "--no-mirror", "--max-games", "2"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "Public Seed Set: 2/28 games" in out and "comparison:" in out


def test_transcripts_must_be_played_in_the_committed_mode(official: Path, copy: Path) -> None:
    (copy / "run.json").write_text(json.dumps({"mode": "live"}), encoding="utf-8")
    problems = repro.verify(commitment(official), copy).problems
    assert "run does not match the commitment" in problems
    assert sum("but the run is live" in p for p in problems) == 28


def test_a_malformed_release_is_reported_not_raised(official: Path, copy: Path) -> None:
    edit_manifest(copy, lambda m: m.pop("pool"))
    result = repro.verify(commitment(official), copy, replay=False)
    assert len(result.problems) == 1 and "malformed" in result.problems[0]


def test_experiment_runs_commit_and_verify_too(tmp_path: Path) -> None:
    """An effort sweep (only Mirror Matches) is an official run too: it commits and verifies."""
    from zantine.schedule import make_effort_manifest

    run = tmp_path / "effort"
    create_run(run, make_effort_manifest(POOL, 9, per_level=1))
    execute(run, mode="dry", max_games=3)
    _, c = repro.commit(run, allow_incomplete=True)
    assert c["run"]["experiment"] == "effort"
    result = repro.release(run, tmp_path / "release")
    assert result.ok and result.replayed == 3, result.problems
