"""Layered reproducibility (``docs/adr/0001``): Public Seed Set, Seed Commitments, release.

Official run seeds stay private, so results are checked in three layers.
Guide: ``docs/reproduce.md``.

**Public Seed Set.** The Public Seed Set is a full v1 schedule (12 blocks plus Mirror
Matches) built by ``schedule.make_manifest`` in the ``public`` seed domain. Its
seeds hash ``zantine-public/<PUBLIC_SET_ID>/<game_id>`` and set bit 63. Official
seeds hash ``zantine-run/...`` and are always below 2**63, and
``schedule.verify`` checks every seed against its derivation. So the two sets
cannot share a seed. We also check this explicitly, seed by seed: against an
official manifest at commit time, against a commitment's membership tags before
release, and against the released seeds at verify time.
``zantine repro public`` plays the set (or its first ``--blocks``) on the open
engine with the user's own keys, writes the usual report, and compares each
estimate with the official ``metrics.public.json`` intervals.

**Seed Commitment** (``commitment.json``, published before any results). Hashes
are SHA-256 over canonical JSON (``canonical``). Every hash that covers a seed
or transcript is *blinded*: the hashed object includes a 256-bit secret nonce,
kept in ``commitment.secret.json`` next to the run and released only at
retirement. Without the nonce, a hash would be a public oracle for the master
seed (only 63 bits, so open to brute force). With it, the published hashes reveal
nothing and still bind: changing any seed, game or transcript byte later needs a
SHA-256 collision. The commitment holds:

- ``manifest_sha256``: the blinded hash of the whole plan (``manifest.json``).
- ``seed_list_sha256``: the blinded hash of ``[game_id, seed]`` in run order, so
  a reordered or swapped seed changes it.
- ``games``: one ``seed_commit`` and ``transcript_commit`` per game, sorted by
  game id (not run order, which depends on the master seed), so a failed check
  names the game and says what changed. A game committed without a transcript
  (``--allow-incomplete``) commits to "no transcript".
- ``transcripts_sha256``: the hash of every ``transcript_commit``.
- ``seed_tags``: for the explicit overlap check before release, the sorted
  ``scrypt(str(seed), salt)`` of every official seed. The salt is public and the
  KDF slow, so anyone can test whether a seed they hold (a Public Seed Set seed)
  is in the run. Guessing an unknown seed costs about 2**63 scrypt calls. At
  release, ``verify`` recomputes the tags from the real seeds, so tags that
  did not match the seeds are caught.
- ``commitment_sha256``: the hash of everything above. This one value is what
  gets published, for example in the results announcement.

**Release** (retirement only). ``zantine release`` copies a run's manifest,
transcripts, commitment and secret into a directory and runs ``verify`` on it.
``zantine verify`` rebuilds the commitment from a release and reports every
difference. For dry runs it also replays each game from its seed and requires a
byte-identical transcript (engine determinism). Live transcripts hold Model
output, which cannot be replayed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import secrets
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zantine import runs, schedule
from zantine.schedule import PUBLIC_SEED_BIT, SEED_DERIVATIONS

COMMITMENT = "commitment.json"
SECRET = "commitment.secret.json"
SEEDS = "seeds.json"
COMMITMENT_KIND = "zantine.seed_commitment"
COMMITMENT_VERSION = 1
CANONICAL = (
    "UTF-8 JSON: keys sorted, separators (',', ':'), no ASCII escaping, integers exact; "
    "SHA-256 over those bytes"
)
# The Public Seed Set: the master seed of the public seed domain. A new benchmark
# version that changes what a seed generates gets a new id (and so a new set).
PUBLIC_SET_ID = 1
PUBLIC_BLOCKS = 12
PUBLIC_RUN_DIR = Path("runs") / f"public-set-{PUBLIC_SET_ID}"
# Membership tags (``seed_tags``): scrypt cost parameters, recorded in each commitment.
TAG_PARAMS = {"n": 2**14, "r": 8, "p": 1, "dklen": 16}
Z95 = 1.959964


# Canonical hashing --------------------------------------------------------------------


def canonical(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blinded(nonce: str, what: str, **fields: Any) -> str:
    """SHA-256 of a canonical object that includes the secret nonce."""
    return sha256_hex(canonical({"commitment": what, "nonce": nonce, **fields}))


def seed_tag(seed: int, salt: str, params: dict[str, int]) -> str:
    """A seed's membership tag: slow to compute, so it cannot be inverted by guessing."""
    return hashlib.scrypt(
        str(seed).encode("ascii"),
        salt=bytes.fromhex(salt),
        n=params["n"],
        r=params["r"],
        p=params["p"],
        dklen=params["dklen"],
        maxmem=128 * params["r"] * params["n"] * params["p"] + 2**20,
    ).hex()


def self_hash(commitment: dict[str, Any]) -> str:
    return sha256_hex(canonical({k: v for k, v in commitment.items() if k != "commitment_sha256"}))


# Public Seed Set ----------------------------------------------------------------------


def public_manifest(
    pool: list[str] | None = None, *, blocks: int = PUBLIC_BLOCKS, mirror: bool = True
) -> dict[str, Any]:
    """The Public Seed Set as a run manifest, or its first ``blocks`` blocks.

    Blocks and Mirror Matches don't depend on how many blocks are scheduled, so
    a smaller set is a subset of the full one.
    """
    pool = schedule.load_pool() if pool is None else pool
    return schedule.make_manifest(
        pool, PUBLIC_SET_ID, blocks=blocks, mirror=mirror, domain="public"
    )


def public_seeds(pool: list[str]) -> set[int]:
    """Every seed of the full Public Seed Set for this pool."""
    return {g["seed"] for g in public_manifest(pool)["games"]}


def official_seed_problems(manifest: dict[str, Any]) -> list[str]:
    """An official manifest must use the ``run`` seed domain and share no seed with
    the Public Seed Set (checked seed by seed, not only by construction)."""
    problems = []
    if schedule.seed_domain(manifest) != "run":
        problems.append(f"seed domain is {schedule.seed_domain(manifest)!r}, not 'run' (official)")
    seeds = {g["seed"] for g in manifest["games"]}
    if any(not 0 <= s < PUBLIC_SEED_BIT for s in seeds):
        problems.append("a seed is outside the official range [0, 2**63)")
    shared = seeds & public_seeds(manifest["pool"])
    if shared:
        problems.append(f"{len(shared)} seed(s) are also in the Public Seed Set")
    return problems


def overlap_with_tags(seeds: set[int], commitment: dict[str, Any]) -> int:
    """How many of ``seeds`` are among a commitment's official seeds (by membership tag)."""
    tags = commitment["seed_tags"]
    committed = set(tags["tags"])
    return sum(seed_tag(s, tags["salt"], tags["params"]) in committed for s in seeds)


# Seed Commitments ----------------------------------------------------------------------


def run_info(manifest: dict[str, Any], mode: str | None) -> dict[str, Any]:
    """The public facts of a run: everything in the manifest except seeds and order."""
    domain = schedule.seed_domain(manifest)
    return {
        "games": len(manifest["games"]),
        "pool": manifest["pool"],
        "blocks": manifest["blocks"],
        "mirror_per_model": manifest["mirror_per_model"],
        "manifest_version": manifest["manifest_version"],
        "engine": manifest["engine"],
        "config": manifest["config"],
        "mode": mode,
        "seed_domain": domain,
        "seed_derivation": SEED_DERIVATIONS.get(domain),
        "seed_range": "[0, 2**63)" if domain == "run" else "[2**63, 2**64)",
    }


def build_commitment(
    manifest: dict[str, Any],
    transcripts: dict[str, bytes | None],
    mode: str | None,
    *,
    nonce: str,
    tag_salt: str,
    tag_params: dict[str, int],
) -> dict[str, Any]:
    """The commitment for a manifest and its transcripts (deterministic given the nonce)."""
    games = []
    for g in manifest["games"]:
        data = transcripts.get(g["id"])
        digest = None if data is None else sha256_hex(data)
        games.append(
            {
                "game": g["id"],
                "seed_commit": blinded(
                    nonce, "seed", game=g["id"], index=g["index"], seed=g["seed"]
                ),
                "transcript_commit": blinded(nonce, "transcript", game=g["id"], sha256=digest),
            }
        )
    games.sort(key=lambda r: r["game"])
    seeds = [[g["id"], g["seed"]] for g in manifest["games"]]
    body: dict[str, Any] = {
        "kind": COMMITMENT_KIND,
        "version": COMMITMENT_VERSION,
        "algorithm": "sha256",
        "serialization": CANONICAL,
        "blinding": "each hashed object includes a secret 256-bit nonce, released at retirement",
        "run": run_info(manifest, mode),
        "manifest_sha256": blinded(nonce, "manifest", manifest=manifest),
        "seed_list_sha256": blinded(nonce, "seed_list", seeds=seeds),
        "games": games,
        "transcripts_sha256": sha256_hex(
            canonical([[r["game"], r["transcript_commit"]] for r in games])
        ),
        "games_without_transcript": sorted(k for k, v in transcripts.items() if v is None),
        "public_set": {
            "id": PUBLIC_SET_ID,
            "seed_derivation": SEED_DERIVATIONS["public"],
            "disjoint": True,  # commit refuses otherwise (official_seed_problems)
        },
        "seed_tags": {
            "kdf": "scrypt",
            "input": "the seed in decimal ASCII",
            "salt": tag_salt,
            "params": dict(tag_params),
            "tags": sorted(seed_tag(g["seed"], tag_salt, tag_params) for g in manifest["games"]),
        },
    }
    body["commitment_sha256"] = self_hash(body)
    return body


def read_transcripts(directory: Path, manifest: dict[str, Any]) -> dict[str, bytes | None]:
    """Raw bytes of each planned game's ``games/<id>.jsonl`` (None when missing)."""
    out: dict[str, bytes | None] = {}
    for g in manifest["games"]:
        path = runs.transcript_path(directory, g["id"])
        out[g["id"]] = path.read_bytes() if path.is_file() else None
    return out


def transcript_problems(game: dict[str, Any], data: bytes, mode: str | None) -> list[str]:
    """A transcript must be complete and be the scheduled game: its seed, seating and
    run mode (every Seat ``dry`` or every Seat ``live``)."""
    try:
        records = [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
    except ValueError:
        return [f"{game['id']}: transcript does not parse"]
    if not _complete(records):
        return [f"{game['id']}: transcript is not complete"]
    header = records[0]
    if header.get("seed") != game["seed"]:
        return [f"{game['id']}: transcript seed {header.get('seed')} is not the scheduled seed"]
    try:
        runs.check_seating(game, header)
        kinds = {s["controller"]["kind"] for s in header["seats"]}
    except (RuntimeError, KeyError, TypeError) as exc:
        return [f"{game['id']}: {exc}"]
    if mode is not None and kinds != {mode}:
        return [f"{game['id']}: Seats are {sorted(kinds)}, but the run is {mode}"]
    return []


def _complete(records: list[dict[str, Any]]) -> bool:
    return (
        bool(records)
        and records[0].get("kind") == "header"
        and any(r.get("kind") == "result" for r in records)
        and records[-1].get("kind") == "cost"
        and records[-1].get("complete") is True
    )


def commit(
    run_dir: Path, *, allow_incomplete: bool = False, tag_params: dict[str, int] | None = None
) -> tuple[Path, dict[str, Any]]:
    """Write ``commitment.json`` (publish it) and ``commitment.secret.json`` (keep it private
    until the version is retired) for an official run directory."""
    manifest = runs.load_manifest(run_dir)
    out, secret_path = run_dir / COMMITMENT, run_dir / SECRET
    if out.exists() or secret_path.exists():
        raise ValueError(f"{run_dir} already has a commitment; a run is committed once")
    problems = schedule.verify(manifest) + official_seed_problems(manifest)
    transcripts = read_transcripts(run_dir, manifest)
    plan = {g["id"]: g for g in manifest["games"]}
    for game_id, data in transcripts.items():
        if data is not None:
            problems += transcript_problems(plan[game_id], data, runs_mode(run_dir))
    missing = sorted(k for k, v in transcripts.items() if v is None)
    if missing and not allow_incomplete:
        problems.append(
            f"{len(missing)} game(s) have no transcript (e.g. {missing[0]}); finish the run, "
            "or pass --allow-incomplete to commit them as missing"
        )
    if problems:
        raise ValueError("cannot commit: " + "; ".join(problems[:5]))
    nonce = secrets.token_hex(32)
    commitment = build_commitment(
        manifest,
        transcripts,
        runs_mode(run_dir),
        nonce=nonce,
        tag_salt=secrets.token_hex(16),
        tag_params=tag_params or TAG_PARAMS,
    )
    secret = {"nonce": nonce, "commitment_sha256": commitment["commitment_sha256"]}
    runs._write_atomic(secret_path, json.dumps(secret, indent=1, sort_keys=True) + "\n")
    runs._write_atomic(out, json.dumps(commitment, indent=1, sort_keys=True) + "\n")
    return out, commitment


def runs_mode(run_dir: Path) -> str | None:
    path = run_dir / runs.RUN
    return json.loads(path.read_text("utf-8"))["mode"] if path.is_file() else None


# Verifying a release -------------------------------------------------------------------


@dataclass
class Verification:
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    replayed: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def verify(
    commitment: dict[str, Any],
    release_dir: Path,
    *,
    expect: str | None = None,
    replay: bool = True,
) -> Verification:
    """Check a released run against its earlier commitment; every difference is reported."""
    v = Verification()
    if commitment.get("kind") != COMMITMENT_KIND or commitment.get("version") != COMMITMENT_VERSION:
        v.problems.append("not a version-1 zantine Seed Commitment")
        return v
    published = commitment.get("commitment_sha256")
    if self_hash(commitment) != published:
        v.problems.append("commitment.json was changed after it was hashed (commitment_sha256)")
    if expect is not None and expect.lower() != published:
        v.problems.append(f"commitment_sha256 {published} is not the published {expect}")
    secret_path, manifest_path = release_dir / SECRET, release_dir / runs.MANIFEST
    for path in (secret_path, manifest_path):
        if not path.is_file():
            v.problems.append(f"release has no {path.name}")
    if not v.ok and not (secret_path.is_file() and manifest_path.is_file()):
        return v
    try:
        _verify_release(commitment, release_dir, v, replay)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        v.problems.append(f"release is malformed: {exc!r}")
    return v


def _verify_release(
    commitment: dict[str, Any], release_dir: Path, v: Verification, replay: bool
) -> None:
    nonce = json.loads((release_dir / SECRET).read_text("utf-8"))["nonce"]
    manifest = json.loads((release_dir / runs.MANIFEST).read_text("utf-8"))
    v.problems += [f"manifest: {p}" for p in schedule.verify(manifest)]
    v.problems += [f"manifest: {p}" for p in official_seed_problems(manifest)]

    mode = runs_mode(release_dir) or commitment["run"]["mode"]
    transcripts = read_transcripts(release_dir, manifest)
    rebuilt = build_commitment(
        manifest,
        transcripts,
        mode,
        nonce=nonce,
        tag_salt=commitment["seed_tags"]["salt"],
        tag_params=commitment["seed_tags"]["params"],
    )
    for key in ("run", "manifest_sha256", "seed_list_sha256", "transcripts_sha256"):
        if rebuilt[key] != commitment[key]:
            v.problems.append(f"{key} does not match the commitment")
    if rebuilt["seed_tags"]["tags"] != commitment["seed_tags"]["tags"]:
        v.problems.append("seed_tags do not match the released seeds")
    v.problems += _game_differences(commitment, rebuilt)

    plan = {g["id"]: g for g in manifest["games"]}
    for game_id, data in transcripts.items():
        if data is not None:
            v.problems += transcript_problems(plan[game_id], data, mode)
    seeds_path = release_dir / SEEDS
    if seeds_path.is_file() and json.loads(seeds_path.read_text("utf-8")) != seed_list(manifest):
        v.problems.append(f"{SEEDS} does not match the released manifest")
    if replay and mode == "dry":
        v.problems += _replay(manifest, transcripts, v)
    elif mode == "dry":
        v.notes.append("byte-identical replay skipped (--no-replay)")
    else:
        v.notes.append(
            f"{mode} transcripts hold Model output and cannot be replayed; "
            "each was checked for its scheduled seed, seating and mode"
        )


def _game_differences(commitment: dict[str, Any], rebuilt: dict[str, Any]) -> list[str]:
    want = {r["game"]: r for r in commitment["games"]}
    have = {r["game"]: r for r in rebuilt["games"]}
    out = [f"{g}: committed game missing from the release" for g in sorted(want.keys() - have)]
    out += [f"{g}: game not in the commitment" for g in sorted(have.keys() - want)]
    for g in sorted(want.keys() & have.keys()):
        if want[g]["seed_commit"] != have[g]["seed_commit"]:
            out.append(f"{g}: seed or run position differs from the commitment")
        if want[g]["transcript_commit"] != have[g]["transcript_commit"]:
            if g in commitment["games_without_transcript"]:
                out.append(f"{g}: committed without a transcript, but the release has one")
            elif g in rebuilt["games_without_transcript"]:
                out.append(f"{g}: transcript missing from the release")
            else:
                out.append(f"{g}: transcript differs from the commitment")
    return out


def _replay(
    manifest: dict[str, Any], transcripts: dict[str, bytes | None], v: Verification
) -> list[str]:
    """Replay each dry game from its seed, as ``zantine run --dry-run`` plays it (through a
    ``RunLedger`` on an empty spend log); the transcript must be byte-identical."""
    from zantine.harness.live import play_live

    out = []
    with tempfile.TemporaryDirectory() as tmp:
        cap = runs.DRY_RUN_CAP_USD
        for g in manifest["games"]:
            data = transcripts.get(g["id"])
            if data is None:
                continue
            config, spec = runs.game_setup(manifest, g, "dry")
            ledger = runs.RunLedger(cap, Path(tmp) / runs.SPEND)
            run = play_live(config, spec, spend_cap_usd=cap, ledger=ledger)
            if run.game.transcript.to_jsonl().encode("utf-8") != data:
                out.append(
                    f"{g['id']}: replaying its seed does not give a byte-identical transcript"
                )
            v.replayed += 1
    return out


# Release (retired versions only) ---------------------------------------------------------


def release(
    run_dir: Path,
    out: Path,
    *,
    commitment_path: Path | None = None,
    replay: bool = True,
) -> Verification:
    """Package a retired run's seeds and transcripts, then verify them against the
    commitment published earlier. Publish ``out`` only if the result is ok."""
    commitment_path = commitment_path or run_dir / COMMITMENT
    if not commitment_path.is_file():
        raise ValueError(f"no commitment at {commitment_path}; a run is released only if committed")
    if not (run_dir / SECRET).is_file():
        raise ValueError(f"no {SECRET} in {run_dir}; without its nonce nothing can be verified")
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} is not empty")
    commitment = json.loads(commitment_path.read_text("utf-8"))
    manifest = runs.load_manifest(run_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name in (runs.MANIFEST, runs.RUN, SECRET):
        if (run_dir / name).is_file():
            shutil.copyfile(run_dir / name, out / name)
    shutil.copyfile(commitment_path, out / COMMITMENT)
    for g in manifest["games"]:
        src = runs.transcript_path(run_dir, g["id"])
        if src.is_file():
            dst = runs.transcript_path(out, g["id"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    (out / SEEDS).write_text(json.dumps(seed_list(manifest), indent=1) + "\n", encoding="utf-8")
    return verify(commitment, out, replay=replay)


def seed_list(manifest: dict[str, Any]) -> dict[str, Any]:
    """``seeds.json`` of a release: every official seed, readable without the manifest."""
    return {
        "seed_domain": schedule.seed_domain(manifest),
        "seed_derivation": manifest["seed_derivation"],
        "master_seed": manifest["master_seed"],
        "games": [
            {"game": g["id"], "index": g["index"], "seed": g["seed"]} for g in manifest["games"]
        ],
    }


# Comparing a Public Seed Set run with official results ---------------------------------


def compare(official: dict[str, Any], public: dict[str, Any]) -> dict[str, Any]:
    """Each public estimate against the official 95% interval for the same metric and Model.

    ``within`` is the check the website reports: the public point estimate lies in
    the official interval. Two independent samples of equal size land like that
    only about 83% of the time even when the method reproduces exactly, so every
    miss also says whether the difference is within both runs' sampling noise
    (``consistent``: |difference| <= 1.96 * sqrt(se_official**2 + se_public**2), each
    se taken from its interval as width / 3.92).
    """
    rows = []
    for section in ("metrics", "mirror"):
        for key, metric in official.get(section, {}).items():
            mine = public.get(section, {}).get(key, {}).get("per_model", {})
            for model, off in metric["per_model"].items():
                rows.append(
                    _compare_one(section, key, metric["label"], model, off, mine.get(model))
                )
    counts = {s: sum(r["status"] == s for r in rows) for s in ("within", "outside", "n/a")}
    counts["consistent"] = sum(bool(r["consistent"]) for r in rows)
    return {
        "official_run": {k: official.get("run", {}).get(k) for k in ("mode", "pool", "engine")},
        "public_run": {
            k: public.get("run", {}).get(k) for k in ("mode", "pool", "engine", "games_complete")
        },
        "warnings": comparability(official, public),
        "counts": counts,
        "rows": rows,
        "note": (
            "within = the public estimate lies inside the official 95% interval. Independent "
            "runs of equal size land inside only about 83% of the time even when the method "
            "reproduces exactly (fewer public games: less often), so some misses are expected; "
            "consistent = the difference is within both runs' sampling noise."
        ),
    }


def _compare_one(
    section: str,
    key: str,
    label: str,
    model: str,
    off: dict[str, Any],
    pub: dict[str, Any] | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "section": section,
        "metric": key,
        "label": label,
        "model": model,
        "official": [off.get("estimate"), off.get("ci_low"), off.get("ci_high")],
        "public": None
        if pub is None
        else [pub.get("estimate"), pub.get("ci_low"), pub.get("ci_high")],
        "status": "n/a",
        "consistent": None,
    }
    lo, hi = off.get("ci_low"), off.get("ci_high")
    est = pub.get("estimate") if pub else None
    if lo is None or hi is None:
        row["reason"] = "no official interval (not estimable in the official run)"
        return row
    if est is None:
        row["reason"] = "no public estimate (no public game measured it yet)"
        return row
    if lo <= est <= hi:
        row["status"], row["consistent"] = "within", True
        row["reason"] = f"{est:.4g} is inside the official interval [{lo:.4g}, {hi:.4g}]"
        return row
    row["status"] = "outside"
    side, gap = ("above", est - hi) if est > hi else ("below", lo - est)
    reason = f"{est:.4g} is {side} the official interval [{lo:.4g}, {hi:.4g}] by {gap:.4g}"
    se_off = (hi - lo) / (2 * Z95)
    plo, phi = pub.get("ci_low"), pub.get("ci_high")
    if plo is not None and phi is not None and off.get("estimate") is not None:
        se = math.hypot(se_off, (phi - plo) / (2 * Z95))
        z = abs(est - off["estimate"]) / se if se > 0 else math.inf
        row["consistent"] = z <= Z95
        overlap = plo <= hi and lo <= phi
        reason += (
            f"; public interval [{plo:.4g}, {phi:.4g}] "
            f"{'overlaps' if overlap else 'does not overlap'} it, difference "
            + (
                f"within sampling noise (z = {z:.2f})"
                if row["consistent"]
                else f"beyond sampling noise (z = {z:.2f})"
            )
        )
    else:
        reason += "; no public interval to judge sampling noise"
    row["reason"] = reason
    return row


def comparability(official: dict[str, Any], public: dict[str, Any]) -> list[str]:
    """Reasons the two runs may not measure the same thing."""
    o, p = official.get("run", {}), public.get("run", {})
    out = []
    if o.get("pool") != p.get("pool"):
        out.append(f"Model Pools differ: official {o.get('pool')}, public {p.get('pool')}")
    if o.get("engine") is None:
        out.append("official results do not record engine versions; cannot check them")
    elif o.get("engine") != p.get("engine"):
        out.append(f"engine versions differ: official {o.get('engine')}, public {p.get('engine')}")
    if o.get("mode") != p.get("mode"):
        out.append(
            f"official run is {o.get('mode')}, this run is {p.get('mode')}: "
            "the comparison checks the pipeline, not the Models"
        )
    if o.get("round_caps") != p.get("round_caps"):
        out.append(
            f"round caps differ: official {o.get('round_caps')}, public {p.get('round_caps')}"
        )
    return out


def run_public(
    official_path: Path,
    *,
    mode: str,
    out: Path = PUBLIC_RUN_DIR,
    blocks: int = PUBLIC_BLOCKS,
    mirror: bool = True,
    spend_cap_usd: float | None = None,
    live: bool = False,
    max_games: int | None = None,
    commitment_path: Path | None = None,
    progress: Any = None,
    **play: Any,
) -> dict[str, Any]:
    """Play (or resume) the Public Seed Set, report it, and compare with official results."""
    from zantine.analysis.report import strip_private, write_report

    official = json.loads(Path(official_path).read_text("utf-8"))
    pool = official.get("run", {}).get("pool") or schedule.load_pool()
    manifest = public_manifest(pool, blocks=blocks, mirror=mirror)
    overlap = _public_overlap(manifest, official, commitment_path)
    if (out / runs.MANIFEST).is_file():
        if runs.load_manifest(out) != manifest:
            raise ValueError(f"{out} holds another run; it is not this Public Seed Set selection")
    else:
        runs.create_run(out, manifest)
    run_report = runs.execute(
        out,
        mode=mode,
        spend_cap_usd=spend_cap_usd,
        live=live,
        max_games=max_games,
        progress=progress,
        **play,
    )
    method = official.get("method", {})
    report_dir, report = write_report(
        out,
        replicates=method.get("replicates", 2000),
        seed=method.get("seed", 0),
        graphs=False,
    )
    result = compare(official, strip_private(report))
    result["overlap"] = overlap
    result["run"] = {
        "dir": out.as_posix(),
        "complete": run_report.complete,
        "planned": run_report.planned,
        "stopped": run_report.stopped,
        "spent_usd": run_report.spent_usd,
    }
    path = report_dir / "comparison.json"
    path.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    result["path"] = path.as_posix()
    return result


def _public_overlap(
    manifest: dict[str, Any], official: dict[str, Any], commitment_path: Path | None
) -> dict[str, Any]:
    """Check the selected public seeds against every official seed we can see."""
    seeds = {g["seed"] for g in manifest["games"]}
    out: dict[str, Any] = {
        "by_construction": all(s >= PUBLIC_SEED_BIT for s in seeds),
        "checked_against": [],
    }
    official_seeds = {g["seed"] for g in official.get("games", []) if "seed" in g}
    if official_seeds:  # a private metrics.json still holds the seeds
        out["checked_against"].append("official metrics.json seeds")
        if seeds & official_seeds:
            raise ValueError("the Public Seed Set shares seeds with the official run")
    if commitment_path is not None:
        commitment = json.loads(Path(commitment_path).read_text("utf-8"))
        out["checked_against"].append("commitment seed_tags")
        if overlap_with_tags(seeds, commitment):
            raise ValueError("the Public Seed Set shares seeds with the committed official run")
    if not out["by_construction"]:
        raise ValueError("a Public Seed Set seed is in the official seed range")
    return out


def comparison_text(result: dict[str, Any]) -> str:
    c, run = result["counts"], result["run"]
    lines = [f"Public Seed Set: {run['complete']}/{run['planned']} games in {run['dir']}"]
    if run["stopped"]:
        lines.append(f"stopped by {run['stopped']}; rerun the same command to resume")
    lines += [f"warning: {w}" for w in result["warnings"]]
    lines.append(
        f"{c['within']} estimates within the official 95% intervals, {c['outside']} outside "
        f"({c['consistent'] - c['within']} of them within sampling noise), {c['n/a']} n/a"
    )
    for r in result["rows"]:
        if r["status"] != "n/a":
            lines.append(f"  {r['status']:<8} {r['metric']:<24} {r['model']:<20} {r['reason']}")
    lines.append(result["note"])
    lines.append(f"comparison: {result['path']}")
    return "\n".join(lines)


# CLI ------------------------------------------------------------------------------------


def add_commands(sub: Any) -> None:
    """``zantine repro``, ``zantine commit``, ``zantine verify`` and ``zantine release``."""
    r = sub.add_parser("repro", help="the Public Seed Set: rerun it and compare with official CIs")
    rsub = r.add_subparsers(dest="repro_command", required=True)
    p = rsub.add_parser("public", help="play the Public Seed Set and compare with official results")
    how = p.add_mutually_exclusive_group(required=True)
    how.add_argument("--dry-run", action="store_true", help="scripted stand-ins, $0")
    how.add_argument(
        "--live", action="store_true", help="your own API keys (also needs ZANTINE_LIVE=1)"
    )
    p.add_argument("--official", type=Path, required=True, help="the official metrics.public.json")
    p.add_argument("--commitment", type=Path, help="the official commitment.json (overlap check)")
    p.add_argument("--out", type=Path, default=PUBLIC_RUN_DIR, help=f"default {PUBLIC_RUN_DIR}")
    p.add_argument(
        "--blocks", type=int, default=PUBLIC_BLOCKS, help="first N blocks only (default all 12)"
    )
    p.add_argument("--no-mirror", action="store_true", help="leave out the Mirror Matches")
    p.add_argument("--spend-cap", type=float, help="USD limit for the whole run (live only)")
    p.add_argument("--max-games", type=int, help="play at most this many games now")
    o = rsub.add_parser("overlap", help="check official seeds against the Public Seed Set")
    src = o.add_mutually_exclusive_group(required=True)
    src.add_argument("--manifest", type=Path, help="an official manifest.json (seeds visible)")
    src.add_argument("--commitment", type=Path, help="a commitment.json (by membership tag)")
    c = sub.add_parser("commit", help="write the Seed Commitment of an official run directory")
    c.add_argument("run_dir", type=Path)
    c.add_argument(
        "--allow-incomplete", action="store_true", help="commit unplayed games as missing"
    )
    v = sub.add_parser("verify", help="check a released run against its Seed Commitment")
    v.add_argument("commitment", type=Path)
    v.add_argument("release_dir", type=Path)
    v.add_argument("--expect", help="the commitment_sha256 published with the results")
    v.add_argument("--no-replay", action="store_true", help="skip byte-identical dry-run replay")
    s = sub.add_parser("release", help="package a RETIRED run's seeds and transcripts, then verify")
    s.add_argument("run_dir", type=Path)
    s.add_argument("--out", type=Path, required=True, help="empty directory to write")
    s.add_argument("--commitment", type=Path, help="the published commitment.json")
    s.add_argument(
        "--retired",
        action="store_true",
        help="confirm this version is retired; released seeds cannot be taken back",
    )
    s.add_argument("--no-replay", action="store_true", help="skip byte-identical dry-run replay")


COMMANDS = ("repro", "commit", "verify", "release")


def command(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        if args.command == "commit":
            return _commit(args)
        if args.command == "verify":
            commitment = json.loads(args.commitment.read_text("utf-8"))
            result = verify(
                commitment, args.release_dir, expect=args.expect, replay=not args.no_replay
            )
            return _show(result, commitment)
        if args.command == "release":
            return _release(parser, args)
        if args.repro_command == "overlap":
            return _overlap(args)
        return _public(args)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


def _commit(args: argparse.Namespace) -> int:
    path, commitment = commit(args.run_dir, allow_incomplete=args.allow_incomplete)
    run = commitment["run"]
    print(f"Seed Commitment for {run['games']} games ({run['mode']}): {path}")
    print(f"commitment_sha256 {commitment['commitment_sha256']}")
    print(
        f"Publish {COMMITMENT} and this hash before any results. Keep {SECRET} private "
        "until the version is retired."
    )
    return 0


def _show(result: Verification, commitment: dict[str, Any]) -> int:
    for note in result.notes:
        print(f"note: {note}")
    if result.replayed:
        print(f"replayed {result.replayed} games: byte-identical transcripts")
    if result.ok:
        print(f"OK: the release matches commitment {commitment['commitment_sha256']}")
        return 0
    print(f"FAILED: {len(result.problems)} problem(s)")
    for p in result.problems:
        print(f"  {p}")
    return 1


def _release(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if not args.retired:
        parser.error(
            "release publishes official seeds, which can never be taken back "
            "(docs/adr/0001); pass --retired once this version is retired"
        )
    commitment = args.commitment or args.run_dir / COMMITMENT
    result = release(args.run_dir, args.out, commitment_path=commitment, replay=not args.no_replay)
    code = _show(result, json.loads(commitment.read_text("utf-8")))
    print(f"release: {args.out}" if code == 0 else f"DO NOT PUBLISH {args.out}")
    return code


def _overlap(args: argparse.Namespace) -> int:
    if args.manifest is not None:
        manifest = json.loads(args.manifest.read_text("utf-8"))
        problems = official_seed_problems(manifest)
        source = f"{len(manifest['games'])} official seeds"
    else:
        commitment = json.loads(args.commitment.read_text("utf-8"))
        shared = overlap_with_tags(public_seeds(commitment["run"]["pool"]), commitment)
        problems = [f"{shared} Public Seed Set seed(s) are committed"] if shared else []
        if commitment["run"]["seed_domain"] != "run":
            problems.append("the commitment is not for an official (run) seed domain")
        source = f"{commitment['run']['games']} committed official seeds (membership tags)"
    if problems:
        print("OVERLAP: " + "; ".join(problems))
        return 1
    print(f"OK: no Public Seed Set seed is among {source}")
    return 0


def _public(args: argparse.Namespace) -> int:
    def show(line: dict[str, Any]) -> None:
        status = line["status"] if line["status"] != "complete" else line["winner"]
        print(f"  {line['game']:<16} {status}, ${line['cost_usd']:.4f}")

    result = run_public(
        args.official,
        mode="dry" if args.dry_run else "live",
        out=args.out,
        blocks=args.blocks,
        mirror=not args.no_mirror,
        spend_cap_usd=args.spend_cap,
        live=args.live,
        max_games=args.max_games,
        commitment_path=args.commitment,
        progress=show,
    )
    print(comparison_text(result))
    return 0
