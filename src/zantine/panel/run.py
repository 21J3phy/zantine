"""Running the Label Panel: qualify judges, label a sample, report agreement.

1. ``qualify(judges, planted)``: every judge labels the plain **Planted Claims**;
   its truth accuracy (Wilson 95% interval) must reach ``min_planted`` (0.95) or
   it is rejected. An item a judge failed to label counts as wrong.
2. ``label(judges, pool, n, seed, qualification)``: refuses any judge that isn't
   in the qualification report as qualified with the same model, revision and
   prompt; draws a seeded sample stratified by (Model, role); every judge labels
   the same anonymized view of each Claim.
3. ``report(labels)``: per label type, Krippendorff's alpha across judges (95%
   bootstrap interval) and the pipeline's accuracy against the panel's majority
   label (Wilson 95% interval). A label type with alpha under
   ``alpha_threshold`` (0.67) is flagged for its definition to be revised and
   relabeled.

Every output says it is validated by an AI panel, not by humans. Paid judges
share one ``Ledger`` (``purpose="label_panel"``); the spend cap stops the run.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from zantine.harness.ledger import Ledger, SpendCapReached, summarize
from zantine.harness.provider import ProviderError
from zantine.judge.batches import NO_ONE, SKIP
from zantine.panel.anonymize import View, item_view
from zantine.panel.items import Candidate, PlantedSource, strata_counts, stratified_sample
from zantine.panel.judges import PURPOSE, PanelJudge
from zantine.panel.prompt import LABEL_TYPES, LabelError, parse_labels
from zantine.panel.stats import bootstrap_alpha, krippendorff_alpha, proportion
from zantine.rng import GameRng

PANEL_SCHEMA_VERSION = 1
VALIDATED_BY = "Validated by an AI panel, not by humans."
MIN_PLANTED = 0.95
ALPHA_THRESHOLD = 0.67


def view_for(item_id: str, rec: Any, statement: Any, quote: str, seed: int) -> View:
    return item_view(rec, statement, quote, GameRng(seed).fork(f"panel/view/{item_id}"))


def _label_one(judge: PanelJudge, view: View, item_id: str, attempts: int) -> dict[str, Any]:
    """One retry for unusable output or a transient API error; the second failure stands."""
    for attempt in range(1, attempts + 1):
        try:
            return parse_labels(judge.label(view, item_id), view.push_options)
        except LabelError:
            if attempt == attempts:
                raise
        except ProviderError as exc:
            if exc.fatal or attempt == attempts:
                raise
    raise AssertionError("unreachable")


class _Runner:
    """Sends views to judges; stops everything on the spend cap or a fatal error."""

    def __init__(self, attempts: int) -> None:
        self.attempts = attempts
        self.aborted: dict[str, Any] | None = None

    def run(self, judge: PanelJudge, view: View, item_id: str) -> tuple[dict | None, str | None]:
        if self.aborted is not None:
            return None, "not processed: run aborted"
        try:
            return _label_one(judge, view, item_id, self.attempts), None
        except (SpendCapReached, ProviderError) as exc:
            if isinstance(exc, ProviderError) and not exc.fatal:
                return None, str(exc)
            reason = "spend_cap" if isinstance(exc, SpendCapReached) else "provider_error"
            self.aborted = {
                "reason": reason,
                "detail": str(exc),
                "item": item_id,
                "judge": judge.id,
            }
            return None, f"not processed: {exc}"
        except LabelError as exc:
            return None, str(exc)


def cost(ledger: Ledger | None, judges: Sequence[PanelJudge]) -> dict[str, Any] | None:
    if ledger is None:
        return None
    mine = [r for r in ledger.records if r.purpose == PURPOSE]
    return {
        **summarize(mine),
        "by_judge": {j.id: summarize(r for r in mine if r.phase == j.id) for j in judges},
        "spend_cap_usd": ledger.spend_cap_usd,
        "run_spent_usd": ledger.spent_usd,
    }


# 1. Qualification --------------------------------------------------------------------------


def qualify(
    judges: Sequence[PanelJudge],
    planted: PlantedSource,
    *,
    seed: int = 0,
    min_planted: float = MIN_PLANTED,
    ledger: Ledger | None = None,
    attempts: int = 2,
) -> dict[str, Any]:
    items = list(planted.plain_items())
    if not items:
        raise ValueError("no plain Planted Claims to qualify judges on")
    runner = _Runner(attempts)
    rows: dict[str, list[dict[str, Any]]] = {j.id: [] for j in judges}
    for p in items:
        rec, st = p.placed()
        view = view_for(p.id, rec, st, p.quote, seed)
        want = "true" if p.truth else "false"
        for j in judges:
            labels, error = runner.run(j, view, p.id)
            got = labels["truth"] if labels else None
            rows[j.id].append({"id": p.id, "label": want, "truth": got, "error": error})
    out = []
    for j in judges:
        mine = rows[j.id]
        acc = proportion(sum(r["truth"] == r["label"] for r in mine), len(mine))
        errors = sum(r["error"] is not None for r in mine)
        ok = runner.aborted is None and acc["accuracy"] is not None
        out.append(
            {
                "judge": j.info(),
                "planted": acc,
                "errors": errors,
                "qualified": bool(ok and acc["accuracy"] >= min_planted),
                "items": mine,
            }
        )
    return {
        "kind": "label_panel_qualification",
        "schema": PANEL_SCHEMA_VERSION,
        "validated_by": VALIDATED_BY,
        "min_planted": min_planted,
        "n": len(items),
        "seed": seed,
        "complete": runner.aborted is None,
        "aborted": runner.aborted,
        "judges": out,
        "rejected": [r["judge"]["id"] for r in out if not r["qualified"]],
        "cost": cost(ledger, judges),
    }


_IDENTITY = ("id", "family", "model", "revision", "prompt_sha256")


def _identity(info: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(info.get(k) for k in _IDENTITY)


def check_qualified(judges: Sequence[PanelJudge], qualification: Mapping[str, Any]) -> None:
    """Refuse a judge that wasn't qualified, or has changed since it was."""
    if qualification.get("kind") != "label_panel_qualification":
        raise ValueError("not a Label Panel qualification report")
    passed = {_identity(r["judge"]) for r in qualification["judges"] if r["qualified"]}
    for j in judges:
        if _identity(j.info()) not in passed:
            raise ValueError(
                f"judge {j.id!r} has not qualified on plain Planted Claims (accuracy >= "
                f"{qualification.get('min_planted')}) with this model, revision and prompt; "
                "replace it or re-run `zantine panel qualify`"
            )


def _distinct(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if dict(r) not in out:
            out.append(dict(r))
    return out


def check_independent(judges: Sequence[PanelJudge], pipeline: Sequence[Mapping[str, Any]]) -> None:
    """Refuse a judge from the family of an extractor that actually produced the audits."""
    families = {
        str(p["extractor"]["family"]).casefold()
        for p in pipeline
        if (p.get("extractor") or {}).get("family")
    }
    for j in judges:
        family = str(j.info().get("family") or "").casefold()
        if family in families:
            raise ValueError(f"judge {j.id!r} is from the family of the audits' extractor")


# 2. Labeling -------------------------------------------------------------------------------


def _pipeline_push(c: Candidate, view: View) -> str | None:
    if c.push is None:
        return None
    if isinstance(c.push, tuple):
        return view.token(c.push[1])
    return c.push if c.push in (SKIP, NO_ONE) else None


def label(
    judges: Sequence[PanelJudge],
    pool: Sequence[Candidate],
    *,
    n: int,
    seed: int,
    qualification: Mapping[str, Any],
    ledger: Ledger | None = None,
    alpha_threshold: float = ALPHA_THRESHOLD,
    attempts: int = 2,
    pipeline: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """``pipeline``: the extractor and judgment-model identities of the audits in ``pool``."""
    if len(judges) != 3:
        raise ValueError("the Label Panel has exactly three judges")
    check_qualified(judges, qualification)
    pipeline = _distinct(pipeline)
    check_independent(judges, pipeline)
    sample = stratified_sample(pool, n, seed)
    runner = _Runner(attempts)
    items = []
    for c in sample:
        view = view_for(c.id, c.rec, c.statement, c.quote, seed)
        row: dict[str, Any] = {
            "id": c.id,
            "game": c.game,
            "claim_id": c.claim_id,
            "statement_seq": c.statement.seq,
            "model": c.model,  # for stratified reporting; never sent to a judge
            "role": c.role,
            "pipeline": {
                "truth": c.truth,
                "assertion": c.assertion,
                "challenged": c.challenged,
                "push": _pipeline_push(c, view),
            },
            "labels": {},
            "errors": {},
        }
        for j in judges:
            labels, error = runner.run(j, view, c.id)
            row["labels"][j.id] = labels
            if error is not None:
                row["errors"][j.id] = error
        items.append(row)
    return {
        "kind": "label_panel_labels",
        "schema": PANEL_SCHEMA_VERSION,
        "validated_by": VALIDATED_BY,
        "seed": seed,
        "n_requested": n,
        "n": len(items),
        "alpha_threshold": alpha_threshold,
        "strata": strata_counts(pool, sample),
        "judges": [j.info() for j in judges],
        "pipeline": pipeline,
        "complete": runner.aborted is None and not any(r["errors"] for r in items),
        "aborted": runner.aborted,
        "items": items,
        "cost": cost(ledger, judges),
    }


# 3. Report ---------------------------------------------------------------------------------


def majority(values: Iterable[Any]) -> Any:
    """The value at least two judges gave, else None."""
    counts = Counter(v for v in values if v is not None)
    if not counts:
        return None
    value, k = counts.most_common(1)[0]
    return value if k >= 2 else None


def report(labels: Mapping[str, Any], *, seed: int | None = None) -> dict[str, Any]:
    if labels.get("kind") != "label_panel_labels":
        raise ValueError("not a Label Panel labels file")
    judges = [j["id"] for j in labels["judges"]]
    threshold = labels.get("alpha_threshold", ALPHA_THRESHOLD)
    seed = labels["seed"] if seed is None else seed
    items = labels["items"]
    out: dict[str, Any] = {}
    for t in LABEL_TYPES:
        units = [[(r["labels"].get(j) or {}).get(t) for j in judges] for r in items]
        alpha = krippendorff_alpha(units)
        lo, hi = bootstrap_alpha(units, GameRng(seed).fork(f"panel/alpha/{t}"))
        hits = defaultdict(list)
        no_majority = 0
        for r, unit in zip(items, units, strict=True):
            want = majority(unit)
            got = r["pipeline"][t]
            if want is None:
                no_majority += sum(v is not None for v in unit) >= 2
                continue
            if got is None:
                continue
            for key in ("all", f"{r['model']}/{r['role']}"):
                hits[key].append(got == want)
        overall = hits.pop("all", [])
        out[t] = {
            "alpha": None if alpha is None else round(alpha, 6),
            "alpha_ci95": [lo, hi],
            "units": sum(sum(v is not None for v in u) >= 2 for u in units),
            "revise": alpha is None or alpha < threshold,
            "pipeline_vs_majority": proportion(sum(overall), len(overall)),
            "by_stratum": {k: proportion(sum(v), len(v)) for k, v in sorted(hits.items())},
            "no_majority": no_majority,
        }
    return {
        "kind": "label_panel_report",
        "schema": PANEL_SCHEMA_VERSION,
        "validated_by": VALIDATED_BY,
        "judges": labels["judges"],
        "n": len(items),
        "complete": labels.get("complete", False),
        "alpha_threshold": threshold,
        "strata": labels.get("strata"),
        "labels": out,
        "revise": [t for t in LABEL_TYPES if out[t]["revise"]],
        "cost": labels.get("cost"),
    }
