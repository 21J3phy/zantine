"""Solo task calibration: run each Task kind alone, ``trials`` times per Model (default 60).

A Task kind is kept only if every Model completes all of its solo trials:
zero failures in 60 puts the one-sided 95% upper bound on the failure rate at
0.0487, under 5%. Low Productivity in games then reflects teamwork, not raw
ability. Any failure flags the kind for simplification.

Trials
    Trial ``i`` of kind ``k`` uses ``trial_seed(seed, k, i)``: the first 63 bits
    of SHA-256 over ``"zantine-calibration/v1:{seed}:{k}:{i}"``. Every trial is a
    freshly generated variant (generated exactly as ``tasks.run_solo_trial``
    does), seeds differ across kinds and trial indices, and the Model is not
    part of the seed: every Model gets the *same* variants (a paired design, so
    differences between Models are not differences between puzzles).

Results
    ``<out>/trials.jsonl`` gets one record per trial as soon as it finishes
    (``status`` ``done``), including the brief, the answer, the expected answer,
    any error, tokens and cost. A rerun with the same seed and solver skips
    every finished trial, so a crash or a spend-cap stop resumes where it left
    off. Calls that did not finish a trial are recorded too (``provider_error``
    for a transient API error, retried on the next run; ``aborted`` when the
    spend cap or a fatal API error stopped the run), so their cost is never lost.
    ``summary.json`` and ``summary.md`` are rewritten at the end of every run.

Bounds
    Exact Clopper-Pearson bounds on the failure rate ``p`` from ``k`` failures in
    ``n`` trials (computed from the binomial CDF, no SciPy):

    - one-sided 95% upper bound ``U``: ``P(X <= k | n, U) = 0.05``, i.e. the
      0.95 quantile of Beta(k+1, n-k); for ``k = 0``, ``U = 1 - 0.05**(1/n)``
      (0.0487 at n = 60). This is the bound the keep/drop rule relies on.
    - two-sided 95% interval: ``[Beta(0.025; k, n-k+1), Beta(0.975; k+1, n-k)]``;
      for ``k = 0`` the upper end is ``1 - 0.025**(1/n)`` (0.0596 at n = 60).
    - rule of three (``3/n`` for ``k = 0``, 0.05 at n = 60) is shown for reference.

Paid calls go through a ``Ledger`` (``purpose="calibration"``); building real
adapters needs ``live=True`` and ``ZANTINE_LIVE=1``, like ``play_live``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zantine.harness.ledger import Ledger, SpendCapReached, summarize
from zantine.harness.live import require_live
from zantine.harness.pricing import ModelSpec, load_models, model_spec
from zantine.harness.provider import ProviderAdapter, ProviderError, make_adapter
from zantine.harness.solo import SOLO_HARNESS_NAME, SOLO_HARNESS_VERSION, SoloOutcome, SoloSolver
from zantine.rng import GameRng
from zantine.tasks import task_kind

CALIBRATION_VERSION = 1
DEFAULT_TRIALS = 60
TRIALS_FILE = "trials.jsonl"
SUMMARY_JSON = "summary.json"
SUMMARY_MD = "summary.md"

CalibrationSolver = Callable[[Mapping[str, Any]], SoloOutcome]


@dataclass(frozen=True)
class SolverSet:
    """Makes the solver for one (Model, kind). ``name`` is recorded with every trial:
    finished trials are reused only by a run with the same solver name."""

    name: str
    make: Callable[[str, str], CalibrationSolver]


def reference_solvers() -> SolverSet:
    """The kinds' own reference ``solve`` ($0): checks the pipeline, not a Model."""

    def make(model: str, kind: str) -> CalibrationSolver:
        return lambda brief: SoloOutcome(task_kind(kind).solve(dict(brief)))

    return SolverSet("reference", make)


def model_solvers(
    models: Sequence[str],
    ledger: Ledger,
    *,
    live: bool = False,
    adapter_factory: Callable[[ModelSpec], ProviderAdapter] | None = None,
    specs: Mapping[str, ModelSpec] | None = None,
) -> SolverSet:
    """Each Model through the Solo Harness. Real adapters need ``live`` and ZANTINE_LIVE=1."""
    specs = load_models() if specs is None else dict(specs)
    if adapter_factory is None:
        require_live(live)
        adapter_factory = make_adapter
    adapters: dict[str, ProviderAdapter] = {}
    for model_id in models:
        spec = model_spec(model_id, dict(specs))
        spec.check_price_current()
        try:
            adapters[model_id] = adapter_factory(spec)
        except ProviderError as exc:
            raise ValueError(f"cannot start {model_id}: {exc}") from exc
    name = f"{SOLO_HARNESS_NAME}-v{SOLO_HARNESS_VERSION}"
    return SolverSet(name, lambda model, kind: SoloSolver(adapters[model], ledger, kind))


# Seeds -----------------------------------------------------------------------------


def trial_seed(seed: int, kind: str, index: int) -> int:
    """Seed of trial ``index`` of ``kind``; the same for every Model (paired design)."""
    text = f"zantine-calibration/v{CALIBRATION_VERSION}:{seed}:{kind}:{index}"
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big") >> 1


def variant(kind: str, seed: int) -> tuple[Any, dict[str, Any]]:
    """(content, solo brief) of one trial, generated exactly as ``run_solo_trial`` does."""
    k = task_kind(kind)
    content = k.generate(GameRng(seed).fork(f"solo/{kind}"))
    return content, k.solo_brief(content)


def solo_unavailable(kind: str, seed: int = 0) -> str | None:
    """Why ``kind`` has no solo form (its ``solo_brief`` raises), or None."""
    try:
        variant(kind, trial_seed(seed, kind, 0))
    except NotImplementedError as exc:
        return str(exc) or f"{kind} has no solo form"
    return None


# Bounds ----------------------------------------------------------------------------


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


def _solve_decreasing(f: Callable[[float], float], target: float) -> float:
    """p in [0, 1] with f(p) = target, for f decreasing in p (bisection to 1e-12)."""
    lo, hi = 0.0, 1.0
    while hi - lo > 1e-12:
        mid = (lo + hi) / 2
        if f(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def cp_upper(k: int, n: int, alpha: float) -> float:
    """Exact Clopper-Pearson upper bound: P(X <= k | n, U) = alpha."""
    if k >= n:
        return 1.0
    if k == 0:
        return 1 - alpha ** (1 / n)
    return _solve_decreasing(lambda p: binom_cdf(k, n, p), alpha)


def cp_lower(k: int, n: int, alpha: float) -> float:
    """Exact Clopper-Pearson lower bound: P(X >= k | n, L) = alpha."""
    if k <= 0:
        return 0.0
    if k == n:
        return alpha ** (1 / n)
    return _solve_decreasing(lambda p: binom_cdf(k - 1, n, p), 1 - alpha)


def failure_bounds(failures: int, n: int) -> dict[str, Any]:
    """Exact 95% bounds on the failure rate from ``failures`` in ``n`` trials."""
    if n == 0:
        return {"upper_95_one_sided": None, "ci_95_two_sided": None, "rule_of_three": None}
    return {
        "upper_95_one_sided": round(cp_upper(failures, n, 0.05), 6),
        "ci_95_two_sided": [
            round(cp_lower(failures, n, 0.025), 6),
            round(cp_upper(failures, n, 0.025), 6),
        ],
        "rule_of_three": round(3 / n, 6) if failures == 0 else None,
    }


# Running ---------------------------------------------------------------------------


@dataclass
class CalibrationRun:
    summary: dict[str, Any]
    # None if every requested trial ran; else "spend_cap" or "provider_error".
    aborted: str | None = None
    detail: str | None = None
    new_records: list[dict[str, Any]] = field(default_factory=list)


def calibrate(
    models: Sequence[str],
    kinds: Sequence[str],
    *,
    out: Path,
    solvers: SolverSet,
    trials: int = DEFAULT_TRIALS,
    seed: int = 0,
    ledger: Ledger | None = None,
) -> CalibrationRun:
    """Run every missing trial of ``kinds`` x ``models``, then write the summary.

    Stops cleanly on ``SpendCapReached`` or a fatal ``ProviderError``; a transient
    ``ProviderError`` leaves that trial unfinished for the next run.
    """
    if trials < 1:
        raise ValueError("trials must be at least 1")
    for kind in kinds:
        task_kind(kind)  # unknown kinds fail before any call
    out.mkdir(parents=True, exist_ok=True)
    path = out / TRIALS_FILE
    done = {_key(r) for r in read_trials(path) if r["status"] == "done"}
    run = CalibrationRun(summary={})
    unavailable = {kind: solo_unavailable(kind, seed) for kind in kinds}

    with _Appender(path) as write:
        for kind in kinds:
            if unavailable[kind] or run.aborted:
                continue
            for model in models:
                if run.aborted:
                    break
                solver = solvers.make(model, kind)
                for index in range(trials):
                    tseed = trial_seed(seed, kind, index)
                    meta = {
                        "kind": kind,
                        "model": model,
                        "solver": solvers.name,
                        "master_seed": seed,
                        "trial": index,
                        "seed": tseed,
                    }
                    if _key(meta) in done:
                        continue
                    record = _run_trial(meta, solver, ledger, run)
                    write(record)
                    run.new_records.append(record)
                    if run.aborted:
                        break

    run.summary = summarize_trials(
        read_trials(path),
        models,
        kinds,
        trials=trials,
        seed=seed,
        solver=solvers.name,
        unavailable=unavailable,
    )
    run.summary["run"] |= {
        "aborted": run.aborted,
        "detail": run.detail,
        "new_trials": sum(r["status"] == "done" for r in run.new_records),
        "spent_this_run_usd": round(sum(r["cost_usd"] for r in run.new_records), 6),
        "spend_cap_usd": ledger.spend_cap_usd if ledger else None,
    }
    (out / SUMMARY_JSON).write_text(json.dumps(run.summary, indent=2, sort_keys=True) + "\n")
    (out / SUMMARY_MD).write_text(render_markdown(run.summary))
    return run


def _run_trial(
    meta: dict[str, Any], solver: CalibrationSolver, ledger: Ledger | None, run: CalibrationRun
) -> dict[str, Any]:
    kind = meta["kind"]
    content, brief = variant(kind, meta["seed"])
    first = len(ledger.records) if ledger else 0
    record: dict[str, Any] = {
        **meta,
        "brief": brief,
        "expected": content.answer,
        "answer": None,
        "correct": None,
        "error": None,
        "attempts": 0,
    }
    try:
        outcome = solver(brief)
    except SpendCapReached as exc:
        run.aborted, run.detail = "spend_cap", str(exc)
        record |= {"status": "aborted", "error": str(exc)}
    except ProviderError as exc:
        if exc.fatal:
            run.aborted, run.detail = "provider_error", str(exc)
            record |= {"status": "aborted", "error": str(exc)}
        else:
            record |= {"status": "provider_error", "error": str(exc)}
    else:
        correct = outcome.error is None and task_kind(kind).grade(content, outcome.answer)
        record |= {
            "status": "done",
            "answer": outcome.answer,
            "correct": bool(correct),
            "error": outcome.error,
            "attempts": outcome.attempts,
        }
    calls = ledger.records[first:] if ledger else []
    return record | summarize(calls)


def _key(r: Mapping[str, Any]) -> tuple[Any, ...]:
    return (r["solver"], r["master_seed"], r["kind"], r["model"], r["trial"], r["seed"])


class _Appender:
    """Appends one JSON line per record and flushes it, so a crash loses nothing."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> Callable[[dict[str, Any]], None]:
        # A crash mid-write can leave a partial last line; start on a fresh one.
        broken = self.path.exists() and self.path.read_bytes()[-1:] not in (b"", b"\n")
        self.file = self.path.open("a", encoding="utf-8")
        if broken:
            self.file.write("\n")
        return self.write

    def write(self, record: dict[str, Any]) -> None:
        self.file.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        self.file.flush()

    def __exit__(self, *exc: object) -> None:
        self.file.close()


def read_trials(path: Path) -> list[dict[str, Any]]:
    """Every intact record in a results file (a truncated last line is skipped)."""
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def spent_usd(path: Path) -> float:
    """Total recorded spend in a results file, every status and solver included."""
    return sum(r.get("cost_usd", 0.0) for r in read_trials(path))


# Summary ---------------------------------------------------------------------------


def summarize_trials(
    records: Sequence[Mapping[str, Any]],
    models: Sequence[str],
    kinds: Sequence[str],
    *,
    trials: int,
    seed: int,
    solver: str,
    unavailable: Mapping[str, str | None],
) -> dict[str, Any]:
    """Per kind x Model verdicts and bounds, per-kind decisions and cost per Model."""
    mine = [
        r
        for r in records
        if r["solver"] == solver
        and r["master_seed"] == seed
        and r["kind"] in kinds
        and r["model"] in models
        and r["trial"] < trials
    ]
    finished: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for r in mine:
        if r["status"] == "done":
            finished.setdefault(_key(r), r)

    cells = []
    kind_summary: dict[str, Any] = {}
    for kind in kinds:
        if unavailable.get(kind):
            kind_summary[kind] = {
                "decision": "not_solo_calibratable",
                "reason": unavailable[kind],
                "failing_models": [],
            }
            continue
        verdicts = []
        failing = []
        for model in models:
            got = [r for r in finished.values() if r["kind"] == kind and r["model"] == model]
            n = len(got)
            failures = sum(not r["correct"] for r in got)
            verdict = "fail" if failures else ("pass" if n >= trials else "incomplete")
            verdicts.append(verdict)
            if failures:
                failing.append(model)
            spent = [r for r in mine if r["kind"] == kind and r["model"] == model]
            cells.append(
                {
                    "kind": kind,
                    "model": model,
                    "trials": trials,
                    "n": n,
                    "failures": failures,
                    "unusable": sum(r["error"] is not None for r in got),
                    "failed_trials": sorted(r["trial"] for r in got if not r["correct"]),
                    "verdict": verdict,
                    **failure_bounds(failures, n),
                    "cost_usd": round(sum(r["cost_usd"] for r in spent), 6),
                    "calls": sum(r["calls"] for r in spent),
                }
            )
        if failing:
            decision = "simplify"
        elif all(v == "pass" for v in verdicts):
            decision = "keep"
        else:
            decision = "incomplete"
        kind_summary[kind] = {"decision": decision, "reason": None, "failing_models": failing}

    model_summary = {}
    for model in models:
        spent = [r for r in mine if r["model"] == model]
        model_summary[model] = {
            "cost_usd": round(sum(r["cost_usd"] for r in spent), 6),
            "calls": sum(r["calls"] for r in spent),
            "failed_calls": sum(r["failed_calls"] for r in spent),
            "input_tokens": sum(r["input_tokens"] for r in spent),
            "cached_input_tokens": sum(r["cached_input_tokens"] for r in spent),
            "output_tokens": sum(r["output_tokens"] for r in spent),
            "thinking_tokens": sum(r["thinking_tokens"] or 0 for r in spent),
        }

    return {
        "run": {
            "calibration_version": CALIBRATION_VERSION,
            "solver": solver,
            "master_seed": seed,
            "trials": trials,
            "models": list(models),
            "kinds": list(kinds),
            "pairing": "every Model gets the same trial seeds (paired design)",
            "bound": (
                "exact Clopper-Pearson on the failure rate; upper_95_one_sided solves "
                "P(X <= k | n, U) = 0.05; ci_95_two_sided uses 0.025 in each tail"
            ),
            "rule": "pass iff 0 failures in all trials; a kind is kept iff it passes for "
            "every Model, and flagged 'simplify' if any Model fails it",
        },
        "cells": cells,
        "kinds": kind_summary,
        "models": model_summary,
        "total_cost_usd": round(sum(m["cost_usd"] for m in model_summary.values()), 6),
    }


def _fmt(x: float | None) -> str:
    return "-" if x is None else f"{x:.4f}"


def render_markdown(summary: Mapping[str, Any]) -> str:
    run = summary["run"]
    lines = [
        "# Solo task calibration",
        "",
        f"Solver `{run['solver']}`, master seed {run['master_seed']}, "
        f"{run['trials']} trials per kind per Model (paired: same variants for every Model).",
        "",
        "Bounds are exact Clopper-Pearson on the failure rate: one-sided 95% upper bound "
        "(P(X <= k) = 0.05) and the two-sided 95% interval. A kind is kept only if every Model "
        "passes all trials with zero failures.",
    ]
    if run.get("aborted"):
        lines += ["", f"**Run stopped early ({run['aborted']}):** {run.get('detail')}"]
    lines += [
        "",
        "| Kind | Model | n | Failures | Unusable | Upper 95% (1-sided) "
        "| 95% CI (2-sided) | Verdict | Cost (USD) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in summary["cells"]:
        ci = c["ci_95_two_sided"]
        ci_text = "-" if ci is None else f"[{ci[0]:.4f}, {ci[1]:.4f}]"
        lines.append(
            f"| {c['kind']} | {c['model']} | {c['n']}/{c['trials']} | {c['failures']} "
            f"| {c['unusable']} | {_fmt(c['upper_95_one_sided'])} | {ci_text} "
            f"| {c['verdict'].upper()} | {c['cost_usd']:.4f} |"
        )
    lines += ["", "| Kind | Decision | Failing Models / reason |", "|---|---|---|"]
    for kind, k in summary["kinds"].items():
        detail = k["reason"] or ", ".join(k["failing_models"]) or "-"
        lines.append(f"| {kind} | {k['decision']} | {detail} |")
    lines += ["", "| Model | Calls | Cost (USD) |", "|---|---|---|"]
    for model, m in summary["models"].items():
        lines.append(f"| {model} | {m['calls']} | {m['cost_usd']:.4f} |")
    lines += ["", f"Total cost: ${summary['total_cost_usd']:.4f}", ""]
    return "\n".join(lines)
