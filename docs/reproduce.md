# Reproduce

> The full guide behind the website's **Reproduce** page (`#reproduce`, `Reproduce` in `web/src/components/blocks.tsx`), which summarizes the three layers and the commands and points here.

Zantine keeps its official game seeds private, so future Models can't memorize the games (see [`adr/0001`](adr/0001-layered-reproducibility-with-private-official-seeds.md)). You can still check the results in three ways:

1. **Rerun the Public Seed Set** on the open engine with your own API keys. Your results should land within the official error bars.
2. **Check the Seed Commitment.** Before any official results come out, we publish a hash of the run's seeds and transcripts. That proves no game was chosen or changed after the fact.
3. **Verify the release.** When a version is retired, its official seeds and transcripts are published, and anyone can check them against the earlier commitment.

Code: `src/zantine/repro.py`. Commands below assume `uv sync` in a checkout of the engine version the results name.

## 1. Rerun the Public Seed Set

The Public Seed Set is a full v1 schedule (12 blocks of 28 games plus 42 Mirror Matches, 378 games) in its own seed domain. It never shares a seed with an official run (see [How the seed sets stay apart](#how-the-seed-sets-stay-apart)). With your own keys:

```sh
ZANTINE_LIVE=1 ANTHROPIC_API_KEY=... OPENAI_API_KEY=... GEMINI_API_KEY=... XAI_API_KEY=... META_API_KEY=... \
  uv run zantine repro public --live --spend-cap 500 \
    --official metrics.public.json --commitment commitment.json
```

- `--official` is the published `metrics.public.json` of the official run. Use `--commitment` (the published `commitment.json`) to also check, seed by seed, that none of your seeds is an official seed.
- The run is resumable: if it stops at the spend cap or is interrupted, rerun the same command (with a higher cap if needed). It plays in `runs/public-set-1/` unless you pass `--out`.
- `--blocks N` plays only the first N blocks, and `--no-mirror` leaves out the Mirror Matches. A smaller selection is a subset of the full set and costs less, but its intervals are wider.
- `--dry-run` plays every Seat with scripted stand-ins at $0. That checks the pipeline end to end, but not the Models, and the output warns that the modes differ.

It writes the usual report (`runs/public-set-1/report/metrics.json`). It uses the official bootstrap replicates and seed, so the intervals are computed the same way. It also writes `comparison.json` and prints one line per metric and Model:

```
within   correct_vote_share  gpt-6-astra   0.61 is inside the official interval [0.55, 0.68]
outside  impostors_ejected   grok-4.7      0.32 is below the official interval [0.35, 0.47] by 0.03;
         public interval [0.24, 0.40] overlaps it, difference within sampling noise (z = 1.05)
```

**How to read it.** `within` means your point estimate lies inside the official 95% interval. Expect some misses even if everything reproduces exactly. Your run is an independent sample, and two samples of equal size put one's estimate inside the other's interval only about 83% of the time. With fewer games it happens less often. Each miss therefore also says whether the difference is within both runs' sampling noise: |difference| ≤ 1.96 × √(se_official² + se_public²), with each se read off its interval as width / 3.92. Many misses that are beyond sampling noise, or warnings that the Model Pool, engine versions, mode or round cap differ, mean the runs don't measure the same thing.

## 2. Seed Commitments

The operator commits right after an official run finishes, before anything is released:

```sh
uv run zantine commit runs/v1
# commitment_sha256 9f2c...
```

This writes two files:

- `runs/v1/commitment.json`: **published** with the results, along with its `commitment_sha256`.
- `runs/v1/commitment.secret.json`: a 256-bit nonce. It is **kept private** until the version is retired.

`commit` refuses a run that already has a commitment, a run that is not finished (unless `--allow-incomplete`, which commits the unplayed games as "no transcript"), a manifest that fails any schedule invariant, a transcript whose seed or seating isn't the scheduled one, and any official seed that is also a Public Seed Set seed.

### What is in a commitment

All hashes are SHA-256 over canonical JSON (UTF-8, keys sorted, separators `,` and `:`, no ASCII escaping, integers exact).

| Field | What it binds |
| --- | --- |
| `run` | Public facts: game count, Model Pool, blocks, engine versions, config, mode, seed domain and range. No seeds, and no run order. |
| `manifest_sha256` | The whole plan (`manifest.json`), blinded. |
| `seed_list_sha256` | `[game_id, seed]` in run order, blinded. A reordered or swapped seed changes it. |
| `games[]` | Per game, sorted by id: a blinded `seed_commit` (id, run position, seed) and a blinded `transcript_commit` (the SHA-256 of the transcript's bytes, or "none"). A failed check names the game. |
| `transcripts_sha256` | The hash of every `transcript_commit`. |
| `seed_tags` | The sorted scrypt tags of every official seed, for overlap checks before release (below). |
| `commitment_sha256` | The hash of everything else in the file. Publish this one value. |

**Blinding.** "Blinded" means the hashed object includes the secret nonce. That is what makes a commitment safe to publish. A master seed has only 63 bits, and the plan is a public algorithm. Without a nonce, a plain hash of the seed list would be an offline oracle: try master seeds until the hash matches. With the nonce, the hashes reveal nothing until release. They still bind, because changing a seed, a game or one transcript byte would need a SHA-256 collision.

**Membership tags.** You can't see official seeds before release, but you can still test whether a seed you hold is among them: `scrypt(str(seed), salt)` with the salt and cost parameters published in the commitment (default N = 2^14, r = 8, p = 1), then look it up in `seed_tags`. Each test is deliberately slow (tens of milliseconds), so checking 378 public seeds is quick. Recovering an unknown seed, though, would take about 2^63 such calls. The tags are a sorted set, so they don't link a seed to a game. The operator could publish fake tags, but `verify` recomputes them from the released seeds, so that would be caught at release.

```sh
uv run zantine repro overlap --commitment commitment.json   # anyone, before release
uv run zantine repro overlap --manifest runs/v1/manifest.json   # operator, seeds visible
```

## 3. Release on retirement, and verify

Once a version is retired (for example v1, when v2 runs), the operator packages it:

```sh
uv run zantine release runs/v1 --out release/v1 --retired --commitment published/commitment.json
```

`--retired` is required: released seeds can never be taken back. The release directory holds `manifest.json`, `run.json`, `games/<id>.jsonl`, `seeds.json` (every seed and the master seed), `commitment.json` and `commitment.secret.json`. The script then runs `verify` on the directory and prints `DO NOT PUBLISH` if anything fails.

Anyone can then check it:

```sh
uv run zantine verify commitment.json release/v1 --expect 9f2c...
```

`verify` rebuilds the commitment from the release with the revealed nonce and reports every difference. It checks:

- the file's own hash, and the hash you saw published (`--expect`);
- the manifest hash, the seed list and every per-game seed and transcript commitment, naming each game that fails (a changed transcript byte, swapped or reordered seeds, a missing or extra game);
- every schedule invariant, including that each seed derives from the released master seed in the official domain;
- that no seed is in the Public Seed Set, seed by seed, and that the published membership tags match the real seeds;
- that each transcript is complete and seats the scheduled seed, Models and Impostors;
- **determinism**: for a dry run, every game is replayed from its seed and must give a byte-identical transcript. Live transcripts hold Model output and can't be replayed, so they get the checks above.

It exits 0 only if all checks pass.

## How the seed sets stay apart

| | Official runs | Public Seed Set |
| --- | --- | --- |
| Derivation | `int(sha256('zantine-run/<master>/<game_id>')[:8]) >> 1` | `2**63 \| int(sha256('zantine-public/<set id>/<game_id>')[:8]) >> 1` |
| Range | [0, 2^63) | [2^63, 2^64) |
| Master seed | random 63 bits, private until retirement | `PUBLIC_SET_ID` = 1, published |

The ranges can't overlap, and `schedule.verify` checks every seed against its derivation. So the sets are disjoint by construction. This is also checked seed by seed at `commit` (against the full Public Seed Set), by anyone before release (`repro overlap --commitment`, and `repro public` against the membership tags or a private `metrics.json`), and at `verify`. A new benchmark version that changes what a seed generates gets a new `PUBLIC_SET_ID`.
