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
    ``<out>/trials.jsonl`` is an append-only journal, flushed line by line:

    - ``call_started`` just before each paid call is sent, with its worst-case
      cost (``reserved_usd``), and ``call`` when it returns, with tokens, cost and
      the parsed outcome (``answer``, ``unusable`` with the reason, or
      ``provider_error``). So no charge is ever lost: ``spent_usd`` counts every
      ``call`` plus, conservatively, the reservation of any call that started but
      never returned (the process died mid-call).
    - ``done`` once a trial is graded: the brief, the answer, the expected answer,
      any error, attempts, and the totals of the trial's calls.

    Every record carries the trial's identity: kind, Model, master seed, trial
    index, seed, and ``config_id``, a fingerprint of the effective inference
    configuration (solver and harness version, provider, effort, the lab's effort
    setting, output-token limit). A rerun reuses only finished trials with the same
    identity, so a crash or a spend-cap stop resumes where it left off, and a
    changed configuration never inherits another configuration's results. An
    unfinished trial resumes mid-trial: an answer already received is graded
    without a new call, and an unusable first reply still counts as the spent
    first attempt (the retry carries its reason). ``summary.json`` and
    ``summary.md`` are rewritten at the end of every run.

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
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zantine.harness.ledger import CallRecord, Ledger, SpendCapReached
from zantine.harness.live import require_live
from zantine.harness.pricing import ModelSpec, load_models, model_spec
from zantine.harness.provider import ProviderAdapter, ProviderError, make_adapter
from zantine.harness.solo import (
    MAX_ATTEMPTS,
    SOLO_HARNESS_NAME,
    SOLO_HARNESS_VERSION,
    Journal,
    SoloOutcome,
    SoloSolver,
)
from zantine.rng import GameRng
from zantine.tasks import task_kind

CALIBRATION_VERSION = 1
# Bump when the shape of journal records changes; older journals are refused.
JOURNAL_VERSION = 2
DEFAULT_TRIALS = 60
TRIALS_FILE = "trials.jsonl"
SUMMARY_JSON = "summary.json"
SUMMARY_MD = "summary.md"

# solver(brief, history, journal): see ``harness.solo.SoloSolver``.
CalibrationSolver = Callable[[Mapping[str, Any], Sequence[str], Journal | None], SoloOutcome]


@dataclass(frozen=True)
class SolverSet:
    """Makes the solver for one (Model, kind). ``config(model)`` is that Model's
    effective inference configuration; its fingerprint is part of every trial's
    identity, so finished trials are reused only under the same configuration."""

    name: str
    make: Callable[[str, str], CalibrationSolver]
    config: Callable[[str], dict[str, Any]]


def config_id(config: Mapping[str, Any]) -> str:
    """Fingerprint of an effective configuration (first 16 hex of SHA-256)."""
    text = json.dumps(config, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def reference_solvers() -> SolverSet:
    """The kinds' own reference ``solve`` ($0): checks the pipeline, not a Model."""

    def make(model: str, kind: str) -> CalibrationSolver:
        return lambda brief, history, journal: SoloOutcome(task_kind(kind).solve(dict(brief)))

    return SolverSet("reference", make, lambda model: {"solver": "reference"})


def model_solvers(
    models: Sequence[str],
    ledger: Ledger,
    *,
    live: bool = False,
    adapter_factory: Callable[[ModelSpec], ProviderAdapter] | None = None,
    specs: Mapping[str, ModelSpec] | None = None,
) -> SolverSet:
    """Each Model through the Solo Harness. Real adapters need ``live`` and ZANTINE_LIVE=1."""
    check_selection("models", models)
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

    def config(model: str) -> dict[str, Any]:
        spec = adapters[model].model
        return {
            "solver": name,
            "provider": spec.provider,
            "effort": spec.effort,
            "effort_setting": spec.effort_setting,
            "max_output_tokens": spec.max_output_tokens,
        }

    return SolverSet(name, lambda model, kind: SoloSolver(adapters[model], ledger, kind), config)


def check_selection(what: str, items: Sequence[str]) -> None:
    """Reject an empty selection or one that names the same Model or kind twice."""
    if not items:
        raise ValueError(f"no {what} selected")
    repeated = sorted({x for x in items if list(items).count(x) > 1})
    if repeated:
        raise ValueError(f"{what} listed more than once: {', '.join(repeated)}")


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
    """P(X <= k) for X ~ Binomial(n, p), summed in log space (no overflow at large n)."""
    if k < 0:
        return 0.0
    if k >= n or p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    log_p, log_q, log_n = math.log(p), math.log1p(-p), math.lgamma(n + 1)
    logs = [
        log_n - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * log_p + (n - i) * log_q
        for i in range(k + 1)
    ]
    top = max(logs)
    return min(1.0, math.exp(top) * math.fsum(math.exp(x - top) for x in logs))


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
    # Every record this run appended (calls and finished trials).
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
    check_selection("models", models)
    check_selection("kinds", kinds)
    for kind in kinds:
        task_kind(kind)  # unknown kinds fail before any call
    configs = {model: solvers.config(model) for model in models}
    ids = {model: config_id(c) for model, c in configs.items()}
    out.mkdir(parents=True, exist_ok=True)
    path = out / TRIALS_FILE
    done: set[tuple[Any, ...]] = set()
    calls: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for r in read_trials(path):
        if r["status"] == "done":
            done.add(_key(r))
        elif r["status"] == "call":
            calls.setdefault(_key(r), []).append(r)
    run = CalibrationRun(summary={})
    unavailable = {kind: solo_unavailable(kind, seed) for kind in kinds}

    with _Appender(path) as write:

        def emit(record: dict[str, Any]) -> None:
            write(record)
            run.new_records.append(record)

        for kind in kinds:
            if unavailable[kind] or run.aborted:
                continue
            for model in models:
                if run.aborted:
                    break
                solver = solvers.make(model, kind)
                for index in range(trials):
                    meta = {
                        "kind": kind,
                        "model": model,
                        "solver": solvers.name,
                        "config_id": ids[model],
                        "master_seed": seed,
                        "trial": index,
                        "seed": trial_seed(seed, kind, index),
                    }
                    key = _key(meta)
                    if key in done:
                        continue
                    record = _run_trial(meta, solver, calls.setdefault(key, []), emit, run)
                    if record is not None:
                        emit(record | {"config": configs[model]})
                        done.add(key)
                    if run.aborted:
                        break

    run.summary = summarize_trials(
        read_trials(path),
        models,
        kinds,
        trials=trials,
        seed=seed,
        solver=solvers.name,
        configs=configs,
        unavailable=unavailable,
    )
    new_calls = [r for r in run.new_records if r["status"] == "call"]
    run.summary["run"] |= {
        "aborted": run.aborted,
        "detail": run.detail,
        "new_trials": sum(r["status"] == "done" for r in run.new_records),
        "spent_this_run_usd": round(math.fsum(r["cost_usd"] + r["held_usd"] for r in new_calls), 6),
        "spent_in_out_dir_usd": round(spent_usd(path), 6),
        "spend_cap_usd": ledger.spend_cap_usd if ledger else None,
    }
    (out / SUMMARY_JSON).write_text(json.dumps(run.summary, indent=2, sort_keys=True) + "\n")
    (out / SUMMARY_MD).write_text(render_markdown(run.summary))
    return run


def _run_trial(
    meta: dict[str, Any],
    solver: CalibrationSolver,
    calls: list[dict[str, Any]],
    emit: Callable[[dict[str, Any]], None],
    run: CalibrationRun,
) -> dict[str, Any] | None:
    """Finish one trial, resuming from its journaled ``calls``; None if it stays unfinished."""
    content, brief = variant(meta["kind"], meta["seed"])
    answered = [c for c in calls if c["outcome"] == "answer"]
    if answered:
        # The answer arrived but the process stopped before grading: no new call.
        outcome = SoloOutcome(answered[-1]["answer"], None, answered[-1]["attempt"])
    else:
        history = [c["reason"] for c in calls if c["outcome"] == "unusable"][:MAX_ATTEMPTS]
        in_flight: list[str] = []

        def journal(event: Mapping[str, Any]) -> None:
            if event["event"] == "started":
                in_flight.append(uuid.uuid4().hex)
                record = {
                    **meta,
                    "status": "call_started",
                    "call_id": in_flight[-1],
                    "attempt": event["attempt"],
                    "reserved_usd": round(event["reserved_usd"], 8),
                }
            else:
                call: CallRecord = event["call"]
                record = call.to_dict() | {
                    **meta,
                    "status": "call",
                    "call_id": in_flight.pop() if in_flight else None,
                    "attempt": event["attempt"],
                    "outcome": event["outcome"],
                    "answer": event["answer"],
                    "reason": event["reason"],
                    "held_usd": round(event.get("held_usd", 0.0), 8),
                }
                calls.append(record)
            emit(record)

        try:
            outcome = solver(brief, history, journal)
        except SpendCapReached as exc:
            run.aborted, run.detail = "spend_cap", str(exc)
            return None
        except ProviderError as exc:
            if exc.fatal:
                run.aborted, run.detail = "provider_error", str(exc)
            return None
    correct = outcome.error is None and task_kind(meta["kind"]).grade(content, outcome.answer)
    return {
        **meta,
        "status": "done",
        "brief": brief,
        "expected": content.answer,
        "answer": outcome.answer,
        "correct": bool(correct),
        "error": outcome.error,
        "attempts": outcome.attempts,
        **call_totals(calls),
    }


def _key(r: Mapping[str, Any]) -> tuple[Any, ...]:
    return (r["config_id"], r["master_seed"], r["kind"], r["model"], r["trial"], r["seed"])


_TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "thinking_tokens")


def call_totals(calls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Calls, failed calls, tokens and dollars over journaled ``call`` records."""
    out: dict[str, Any] = {
        "calls": len(calls),
        "failed_calls": sum(not c["ok"] for c in calls),
        "cost_usd": round(math.fsum(c["cost_usd"] for c in calls), 6),
        "held_usd": round(math.fsum(c["held_usd"] for c in calls), 6),
    }
    return out | {f: sum(c[f] or 0 for c in calls) for f in _TOKEN_FIELDS}


class _Appender:
    """Appends one JSON line per record and flushes it, so a crash loses nothing.

    Records are pure ASCII, so a torn write never splits a character. A torn last
    line left by an earlier crash is moved to ``<file>.torn`` before appending.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> Callable[[dict[str, Any]], None]:
        if self.path.exists():
            data = self.path.read_bytes()
            cut = data.rfind(b"\n") + 1
            tail = data[cut:]
            if tail and _parse(tail) is not None:
                with self.path.open("ab") as f:  # complete record, only its newline lost
                    f.write(b"\n")
            elif tail:
                with self.path.with_name(self.path.name + ".torn").open("ab") as f:
                    f.write(tail + b"\n")
                os.truncate(self.path, cut)
        self.file = self.path.open("a", encoding="ascii")
        return self.write

    def write(self, record: dict[str, Any]) -> None:
        record = {"journal_version": JOURNAL_VERSION, **record}
        self.file.write(json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n")
        self.file.flush()

    def __exit__(self, *exc: object) -> None:
        self.file.close()


def _parse(line: bytes) -> dict[str, Any] | None:
    try:
        record = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def read_trials(path: Path) -> list[dict[str, Any]]:
    """Every record in a results file. Only the last line may be torn (a crash
    mid-write); it is skipped. A damaged earlier line is an error, and so is a
    record from another journal version (its charges could not be counted)."""
    if not path.exists():
        return []
    *lines, tail = path.read_bytes().split(b"\n")
    numbered = [(number, line) for number, line in enumerate(lines, 1) if line.strip()]
    if tail.strip() and _parse(tail) is not None:
        numbered.append((len(lines) + 1, tail))
    records = []
    for number, line in numbered:
        record = _parse(line)
        if record is None:
            raise ValueError(f"{path}:{number}: damaged record; only the last line may be torn")
        if record.get("journal_version") != JOURNAL_VERSION:
            raise ValueError(
                f"{path}:{number}: written by calibration journal version "
                f"{record.get('journal_version', 1)}, not {JOURNAL_VERSION}; its spend can't be "
                "counted, so use a new --out directory"
            )
        records.append(record)
    return records


def spent_usd(path: Path) -> float:
    """Total spend journaled in a results file, every configuration included.

    Each returned call counts at its actual cost plus anything held for it (a
    call lost in transit counts at its worst case); a call that started but never
    returned (the process died mid-call) counts at its worst-case reservation.
    """
    records = read_trials(path)
    returned = {r["call_id"] for r in records if r["status"] == "call"}
    actual = math.fsum(r["cost_usd"] + r["held_usd"] for r in records if r["status"] == "call")
    reserved = math.fsum(
        r["reserved_usd"]
        for r in records
        if r["status"] == "call_started" and r["call_id"] not in returned
    )
    return actual + reserved


# Summary ---------------------------------------------------------------------------


def summarize_trials(
    records: Sequence[Mapping[str, Any]],
    models: Sequence[str],
    kinds: Sequence[str],
    *,
    trials: int,
    seed: int,
    solver: str,
    configs: Mapping[str, Mapping[str, Any]],
    unavailable: Mapping[str, str | None],
) -> dict[str, Any]:
    """Per kind x Model verdicts and bounds, per-kind decisions and cost per Model."""
    ids = {model: config_id(configs[model]) for model in models}
    mine = [
        r
        for r in records
        if r["model"] in ids
        and r.get("config_id") == ids[r["model"]]
        and r["master_seed"] == seed
        and r["kind"] in kinds
        and r["trial"] < trials
    ]
    finished: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for r in mine:
        if r["status"] == "done":
            finished.setdefault(_key(r), r)
    paid = [r for r in mine if r["status"] == "call"]

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
            spent = call_totals([r for r in paid if r["kind"] == kind and r["model"] == model])
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
                    "cost_usd": spent["cost_usd"],
                    "calls": spent["calls"],
                }
            )
        if failing:
            decision = "simplify"
        elif all(v == "pass" for v in verdicts):
            decision = "keep"
        else:
            decision = "incomplete"
        kind_summary[kind] = {"decision": decision, "reason": None, "failing_models": failing}

    model_summary = {
        model: call_totals([r for r in paid if r["model"] == model]) for model in models
    }

    return {
        "run": {
            "calibration_version": CALIBRATION_VERSION,
            "solver": solver,
            "master_seed": seed,
            "trials": trials,
            "models": list(models),
            "configs": {m: {**configs[m], "config_id": ids[m]} for m in models},
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
    lines += [
        "",
        "| Model | Calls | Cost (USD) | Held for lost calls (USD) |",
        "|---|---|---|---|",
    ]
    for model, m in summary["models"].items():
        lines.append(f"| {model} | {m['calls']} | {m['cost_usd']:.4f} | {m['held_usd']:.4f} |")
    lines += ["", f"Total cost: ${summary['total_cost_usd']:.4f}", ""]
    return "\n".join(lines)
