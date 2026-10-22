"""Provider probe: settle the API details ``docs/model-pool.md`` lists as unverified.

``zantine probe --models <ids> --live`` makes a handful of small, metered calls
per Model (the real Neutral Harness request for one Seat in Round 1, plus a few
variants) and checks each assumption the adapters make against what the API
actually returns. Each check ends as one ``Finding``:

- ``confirmed``: the reply matches what the adapter assumes.
- ``contradicted``: the reply shows the assumption is wrong; fix the adapter.
- ``observed``: the docs state nothing, so the value seen is recorded (e.g. a
  default effort).
- ``inconclusive``: the reply can't tell (e.g. no reasoning tokens to count).
- ``failed``: the call itself failed; the error is the evidence.

The checks read the provider's raw usage, not our normalized ``Usage``, so a
wrong normalization can't confirm itself. Every call goes through a ``Ledger``
with its own spend cap per Model, like any other paid call.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from zantine.config import GameConfig
from zantine.engine import Game
from zantine.harness import neutral
from zantine.harness.ledger import CallRecord, Ledger, SpendCapReached
from zantine.harness.pricing import PER_TOKEN
from zantine.harness.provider import (
    HarnessRequest,
    ProviderAdapter,
    ProviderError,
    ProviderResponse,
)
from zantine.rng import GameRng
from zantine.scenario import build_scenario
from zantine.seats.bots import IdleBot

CONFIRMED = "confirmed"
CONTRADICTED = "contradicted"
OBSERVED = "observed"
INCONCLUSIVE = "inconclusive"
FAILED = "failed"

# The one SDK method each provider's adapter calls: (client attribute, method).
CLIENT_METHOD = {
    "anthropic": ("messages", "create"),
    "openai": ("responses", "create"),
    "xai": ("responses", "create"),
    "meta": ("responses", "create"),
    "google": ("models", "generate_content"),
}
RESPONSES_API = frozenset({"openai", "xai", "meta"})
# Labs whose output cap may leave reasoning out, so no reservation bounds a call.
UNBOUNDED_REASONING = frozenset({"xai"})

# max_output_tokens for the output-cap check: low enough that medium-effort
# reasoning on a game turn usually runs past it.
CAP_PROBE_TOKENS = 256
# Documented defaults for the lab's effort parameter; None = not documented.
DOCUMENTED_DEFAULT_EFFORT = {"gpt-6.1-sol": "medium", "grok-4.7": "high"}
# Our price table and xAI's own charge may differ by rounding only.
COST_TOLERANCE = 0.02
PARALLEL_ASK = "Probe instruction: for this turn only, call two different tools in this one reply."


@dataclass(frozen=True)
class Finding:
    model: str
    check: str
    assumption: str
    verdict: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probe_request(seed: int = 1) -> HarnessRequest:
    """The real Neutral Harness action request for Seat 0 in Round 1 of a seeded game."""
    config = GameConfig(seed=seed)
    rng = GameRng(config.seed)
    scenario = build_scenario(config, rng.fork("scenario"))
    game = Game(config, [IdleBot() for _ in range(config.num_seats)], rng=rng, scenario=scenario)
    game.start()
    return neutral.action_request(game.rules, game.action_context(game.state.player(0)))


class _Tap:
    """Stands in for an SDK client: forwards the adapter's one call and keeps the raw reply."""

    def __init__(self, client: Any, provider: str) -> None:
        group, method = CLIENT_METHOD[provider]
        inner = getattr(getattr(client, group), method)
        self.params: dict[str, Any] = {}
        self.reply: Any = None

        def call(**params: Any) -> Any:
            self.params = params
            self.reply = inner(**params)
            return self.reply

        setattr(self, group, SimpleNamespace(**{method: call}))


# Where each provider's request carries its output cap.
def _set_output_cap(params: dict[str, Any], provider: str, cap: int) -> dict[str, Any]:
    if provider == "google":
        return {**params, "config": {**params["config"], "max_output_tokens": cap}}
    return {**params, ("max_tokens" if provider == "anthropic" else "max_output_tokens"): cap}


def _variant(
    adapter: ProviderAdapter,
    edit: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    max_output_tokens: int | None = None,
) -> tuple[ProviderAdapter, _Tap]:
    """A copy of ``adapter`` whose request is changed by ``edit`` and whose reply is kept.

    ``max_output_tokens`` changes only the cap sent: the Ledger still reserves the
    Model's full cap, since a lab whose cap leaves out reasoning (xAI, as assumed)
    can bill far more than the cap sent. The SDK's own retries are turned off, so
    each billed request is exactly one metered call.
    """
    client = adapter.client  # type: ignore[attr-defined]
    if hasattr(client, "with_options"):  # openai / anthropic SDK clients
        client = client.with_options(max_retries=0)
    tap = _Tap(client, adapter.provider)
    variant = copy.copy(adapter)
    variant.client = tap  # type: ignore[attr-defined]
    edits = [e for e in (edit,) if e is not None]
    if max_output_tokens is not None:
        edits.append(lambda p: _set_output_cap(p, adapter.provider, max_output_tokens))
    if edits:
        build = variant.build_params  # type: ignore[attr-defined]

        def build_params(request: HarnessRequest) -> dict[str, Any]:
            params = build(request)
            for e in edits:
                params = e(params)
            return params

        variant.build_params = build_params  # type: ignore[attr-defined]
    return variant, tap


def dump(obj: Any) -> Any:
    """An SDK object (pydantic, or a test's SimpleNamespace) as plain JSON-able data."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    if isinstance(obj, SimpleNamespace):
        obj = vars(obj)
    if isinstance(obj, dict):
        return {k: dump(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list | tuple):
        return [dump(v) for v in obj]
    if isinstance(obj, bytes):
        return obj.hex()
    return obj


def _usage(reply: Any, provider: str) -> dict[str, Any]:
    name = "usage_metadata" if provider == "google" else "usage"
    if isinstance(reply, dict):
        return dump(reply.get(name)) or {}
    return dump(getattr(reply, name, None)) or {}


def _n(d: dict[str, Any], *path: str) -> int | None:
    for key in path:
        if not isinstance(d, dict) or key not in d:
            return None
        d = d[key]
    return d if isinstance(d, int) else None


# Verdicts from raw usage -----------------------------------------------------------
# Pure functions over the provider's own numbers, so they can be tested offline.


# Each call's raw counts admit some of four readings: (hidden_inside, cache_inside),
# i.e. whether reasoning (thought) tokens sit inside the output count and whether
# cached tokens sit inside the input count. A reading fits when it reproduces the
# reported total; a counter the reply leaves out is unknown (any value >= 0), never 0.
Reading = tuple[bool, bool]
READINGS: frozenset[Reading] = frozenset((h, c) for h in (True, False) for c in (True, False))


@dataclass(frozen=True)
class Counts:
    inp: int | None
    cached: int | None
    out: int | None
    hidden: int | None
    total: int | None

    def fits(self) -> frozenset[Reading]:
        if None in (self.inp, self.out, self.total):
            return READINGS  # nothing to check against
        found = set()
        for h, c in READINGS:
            # A part can't be larger than the count it sits inside.
            if (h and (self.hidden or 0) > self.out) or (c and (self.cached or 0) > self.inp):
                continue
            terms = [self.inp, self.out]
            unknown = False
            for outside, value in ((not c, self.cached), (not h, self.hidden)):
                if outside:
                    unknown |= value is None
                    terms.append(value or 0)
            rest = self.total - sum(terms)  # type: ignore[arg-type]
            if rest == 0 or (unknown and rest > 0):
                found.add((h, c))
        return frozenset(found)

    def __str__(self) -> str:
        return (
            f"input {self.inp}, cached {self.cached}, output {self.out}, "
            f"reasoning {self.hidden}, total {self.total}"
        )


def responses_counts(usage: dict[str, Any]) -> Counts:
    """Responses API usage (OpenAI, xAI, Meta)."""
    return Counts(
        _n(usage, "input_tokens"),
        _n(usage, "input_tokens_details", "cached_tokens"),
        _n(usage, "output_tokens"),
        _n(usage, "output_tokens_details", "reasoning_tokens"),
        _n(usage, "total_tokens"),
    )


def gemini_counts(usage: dict[str, Any]) -> Counts:
    """Gemini ``usage_metadata``: tool-use prompt tokens count as input."""
    prompt = _n(usage, "prompt_token_count")
    tool = _n(usage, "tool_use_prompt_token_count") or 0
    return Counts(
        None if prompt is None else prompt + tool,
        _n(usage, "cached_content_token_count"),
        _n(usage, "candidates_token_count"),
        _n(usage, "thoughts_token_count"),
        _n(usage, "total_token_count"),
    )


def decide(readings: frozenset[Reading]) -> tuple[bool | None, bool | None]:
    """Each question's answer if every admissible reading agrees on it (None otherwise)."""

    def one(i: int) -> bool | None:
        values = {r[i] for r in readings}
        return values.pop() if len(values) == 1 else None

    return one(0), one(1)


def judge(found: bool | None, assumed: bool) -> str:
    if found is None:
        return INCONCLUSIVE
    return CONFIRMED if found == assumed else CONTRADICTED


def generated(counts: Counts, readings: frozenset[Reading]) -> tuple[int, int] | None:
    """(visible output, reasoning) under the admissible readings, if they agree."""
    hidden_inside, _ = decide(readings)
    if counts.out is None or counts.hidden is None:
        return None
    if counts.hidden == 0:
        return counts.out, 0
    if hidden_inside is None:
        return None
    visible = counts.out - counts.hidden if hidden_inside else counts.out
    return (visible, counts.hidden) if visible >= 0 else None


def output_cap_scope(
    split: tuple[int, int] | None, stop: str | None, cap: int, cap_includes_reasoning: bool
) -> tuple[str, str]:
    """Did a call sent ``max_output_tokens=cap`` generate more than ``cap`` tokens?"""
    if split is None:
        return INCONCLUSIVE, "how reasoning is counted is unresolved"
    visible, reasoning = split
    nums = f"cap {cap}, visible {visible} + reasoning {reasoning}, stop {stop}"
    if visible > cap:
        return INCONCLUSIVE, f"visible output alone exceeded the cap sent: {nums}"
    if not reasoning:
        return INCONCLUSIVE, f"no reasoning on this call: {nums}"
    if visible + reasoning > cap:
        includes = False
    elif stop == "max_tokens":
        includes = True
    else:
        return INCONCLUSIVE, f"the call stayed under the cap: {nums}"
    return judge(includes, cap_includes_reasoning), nums


def provider_cost_match(ours: float, theirs: float | None, alternative: float) -> tuple[str, str]:
    """xAI bills each call itself; compare with our price table under each reading."""
    if theirs is None:
        return INCONCLUSIVE, "no cost_in_usd_ticks in the reply"
    nums = (
        f"ours ${ours:.6f}, xAI's ${theirs:.6f}, if reasoning were inside output ${alternative:.6f}"
    )

    def close(a: float) -> bool:
        return abs(a - theirs) <= max(COST_TOLERANCE * theirs, 1e-6)

    if close(ours):
        return CONFIRMED, nums
    if close(alternative):
        return CONTRADICTED, f"reasoning is inside output_tokens: {nums}"
    return CONTRADICTED, f"neither reading matches the bill (check models.toml prices): {nums}"


# The probe -------------------------------------------------------------------------


class _Halt(Exception):
    """Stop probing this Model: further calls could not be capped safely."""


class _Probe:
    def __init__(self, adapter: ProviderAdapter, ledger: Ledger, request: HarnessRequest) -> None:
        self.adapter = adapter
        self.ledger = ledger
        self.request = request
        self.provider = adapter.provider
        self.model = adapter.model.id
        self.findings: list[Finding] = []
        # Readings every call so far admits (intersected call by call).
        self.readings: frozenset[Reading] = READINGS
        self.first: tuple[ProviderResponse, dict[str, Any]] | None = None

    def add(self, check: str, assumption: str, verdict: str, detail: str, **evidence: Any) -> None:
        self.findings.append(Finding(self.model, check, assumption, verdict, detail, evidence))

    def call(
        self, phase: str, request: HarnessRequest | None = None, **variant: Any
    ) -> tuple[ProviderResponse, dict[str, Any], Any]:
        """One metered call; returns the normalized response, raw usage and raw reply."""
        adapter, tap = _variant(self.adapter, **variant)
        reserved: list[float] = []
        try:
            response = self.ledger.send(
                adapter,
                request or self.request,
                purpose="probe",
                phase=phase,
                on_start=reserved.append,
            )
        except ProviderError as exc:
            # No HTTP status: the call may still have been billed; keep its worst case.
            if exc.status is None and reserved:
                self.ledger.hold(reserved[0])
                if self.provider in UNBOUNDED_REASONING:
                    # Its bill is unknown and may exceed the reservation held for it,
                    # so this counts as the one call that may overspend.
                    raise _Halt(
                        f"{phase} failed and may have been billed past its reservation ({exc})"
                    ) from exc
            raise
        record = self.ledger.records[-1]
        usage = _usage(tap.reply, self.provider)
        # Count the most this call can have cost against the cap: tokens the raw total
        # shows but our normalization left unpriced, and the lab's own bill (xAI) if higher.
        charge = record.cost_usd + self._unpriced(usage, record, reserved[0])
        billed = response.raw.get("provider_cost_usd")
        charge = max(charge, billed or 0.0)
        if charge > record.cost_usd:
            self.ledger.hold(charge - record.cost_usd)
        if self.provider == "xai":
            self._check_bill(phase, response, usage, record.cost_usd)
        if reserved and charge > reserved[0]:
            # The Ledger priced this call's worst case from the output cap; a lab whose
            # cap leaves reasoning out (xAI, as assumed) can bill past it, and then no
            # later call can be bounded either. So at most this one call overspends.
            raise _Halt(
                f"{phase} cost ${charge:.4f}, more than its ${reserved[0]:.4f} worst case; "
                "later calls can't be bounded"
            )
        return response, usage, tap.reply

    def _unpriced(self, usage: dict[str, Any], record: CallRecord, reserved: float) -> float:
        """Dollars for tokens the raw usage shows but the record didn't price (at the
        dearest output rate). Without a raw total that can't be ruled out, so the rest
        of the call's reservation stays held."""
        if self.provider == "anthropic":  # no total to check; its fields are all priced
            return 0.0
        total = self.counts(usage).total
        if total is None:
            return max(reserved - record.cost_usd, 0.0)
        priced = (
            record.input_tokens
            + record.cached_input_tokens
            + record.cache_write_tokens
            + record.output_tokens
        )
        model = self.adapter.model
        top = max(model.output, model.long_context.output if model.long_context else 0.0)
        return max(total - priced, 0) * PER_TOKEN * top

    def _check_bill(
        self, phase: str, response: ProviderResponse, usage: dict[str, Any], ours: float
    ) -> None:
        """Every xAI reply carries xAI's own charge; one that our prices can't reproduce
        stops the probe, since every reservation is priced from models.toml."""
        visible = _n(usage, "output_tokens") or 0
        alternative = self.adapter.model.cost(replace(response.usage, output_tokens=visible))
        verdict, detail = provider_cost_match(
            ours, response.raw.get("provider_cost_usd"), alternative
        )
        if phase in ("serves_request", "cache_read") or verdict == CONTRADICTED:
            self.add(
                "xai_billed_cost",
                "our token normalization and prices reproduce xAI's own charge",
                verdict,
                f"{phase}: {detail}",
                usage=usage,
            )
        if verdict == CONTRADICTED:
            raise _Halt("xAI's bill disagrees with our prices, so reservations can't be trusted")

    def counts(self, usage: dict[str, Any]) -> Counts:
        return gemini_counts(usage) if self.provider == "google" else responses_counts(usage)

    def run(self) -> list[Finding]:
        steps = [self._serves_request, self._cache_read]
        if self.provider != "anthropic":
            steps.append(self._output_cap_scope)
        if self.provider in RESPONSES_API:
            steps += [self._default_effort, self._parallel_tool_calls]
        if self.provider == "google":
            steps.append(self._validated_mode)
        for step in steps:
            name = step.__name__.lstrip("_")
            try:
                step()
            except SpendCapReached as exc:
                self.add("spend_cap", "", FAILED, str(exc))
                break
            except _Halt as exc:
                self.add("stopped", "", FAILED, f"no further calls: {exc}")
                break
            except ProviderError as exc:
                self.add(
                    name,
                    self._serves_assumption() if step == self._serves_request else "",
                    FAILED,
                    str(exc),
                )
                if step == self._serves_request:
                    break
        return self.findings

    def _serves_assumption(self) -> str:
        return {
            "google": "generateContent serves this Model and accepts our tools as "
            "parameters_json_schema (mode AUTO)",
            "meta": "Muse Wonder is served on Meta's Responses API with our request "
            "(store=false, strict tools, tool_choice auto)",
        }.get(self.provider, "the API accepts our Neutral Harness request for this Model")

    def _serves_request(self) -> None:
        response, usage, _ = self.call("serves_request")
        tools = [c.name for c in response.tool_calls]
        known = {t.name for t in self.request.tools}
        detail = f"{len(tools)} tool call(s) {tools}, stop {response.stop_reason}"
        verdict = CONFIRMED if tools and set(tools) <= known else INCONCLUSIVE
        if not tools:
            detail += "; accepted, but the model made no tool call"
        self.add("serves_request", self._serves_assumption(), verdict, detail, usage=usage)
        self.first = (response, usage)

    def _cache_read(self) -> None:
        # The first call wrote (or warmed) the prefix; an identical call should read it.
        response, usage, _ = self.call("cache_read")
        cached = response.usage.cached_input_tokens
        size = cached + response.usage.input_tokens + response.usage.cache_write_tokens
        if cached:
            verdict, detail = CONFIRMED, f"{cached} of {size} prompt tokens read from cache"
        else:
            verdict, detail = INCONCLUSIVE, f"no cache read on a repeated {size}-token prompt"
        self.add(
            "cache_read",
            "an identical repeated prompt is (partly) served from the prompt cache",
            verdict,
            detail,
            usage=usage,
            prompt_tokens=size,
        )
        if self.first is not None and (self.provider in RESPONSES_API or self.provider == "google"):
            self._settle_accounting(self.first[1], usage)
        if self.provider == "meta":
            written = _n(usage, "input_tokens_details", "cache_write_tokens")
            self.add(
                "cache_write_field",
                "Meta reports no cache-write tokens (no write charge)",
                OBSERVED,
                f"cache_write_tokens = {written}",
                usage=usage,
            )

    def _settle_accounting(self, first: dict[str, Any], second: dict[str, Any]) -> None:
        """Decide both questions from the readings both identical calls admit."""
        a, b = self.counts(first), self.counts(second)
        self.readings = a.fits() & b.fits()
        details = f"first call: {a}; repeat: {b}"
        if not self.readings:
            details = f"the two calls admit no common reading ({details})"
        hidden_inside, cache_inside = decide(self.readings)
        if self.provider == "google":
            hidden = (
                "thoughts_accounting",
                "candidates_token_count excludes thoughts_token_count",
                False,
            )
            cache = (
                "cached_in_prompt",
                "prompt_token_count includes cached_content_token_count",
                True,
            )
        else:
            inside = self.adapter.reasoning_in_output  # type: ignore[attr-defined]
            where = "inside" if inside else "on top of"
            hidden = ("reasoning_accounting", f"reasoning_tokens are {where} output_tokens", inside)
            cache = (
                "cached_in_input",
                "input_tokens includes input_tokens_details.cached_tokens",
                True,
            )
        for (check, assumption, assumed), found in (
            (hidden, hidden_inside),
            (cache, cache_inside),
        ):
            self.add(check, assumption, judge(found, assumed), details, first=first, repeat=second)

    def _output_cap_scope(self) -> None:
        includes = self.provider != "xai"
        response, usage, _ = self.call("output_cap", max_output_tokens=CAP_PROBE_TOKENS)
        counts = self.counts(usage)
        split = generated(counts, self.readings & counts.fits())
        verdict, detail = output_cap_scope(split, response.stop_reason, CAP_PROBE_TOKENS, includes)
        what = "includes" if includes else "excludes"
        self.add(
            "output_cap_scope",
            f"max_output_tokens {what} reasoning tokens",
            verdict,
            detail,
            usage=usage,
        )

    def _default_effort(self) -> None:
        def no_reasoning(params: dict[str, Any]) -> dict[str, Any]:
            return {k: v for k, v in params.items() if k != "reasoning"}

        _, usage, reply = self.call("default_effort", edit=no_reasoning)
        echoed = (dump(reply) or {}).get("reasoning", {}) or {}
        seen = echoed.get("effort") if isinstance(echoed, dict) else None
        documented = DOCUMENTED_DEFAULT_EFFORT.get(self.model)
        if seen is None:
            verdict, detail = INCONCLUSIVE, "the reply does not echo reasoning.effort"
        elif documented is None:
            verdict, detail = OBSERVED, f"default effort echoed as {seen!r}"
        else:
            verdict = CONFIRMED if seen == documented else CONTRADICTED
            detail = f"echoed {seen!r}, documented {documented!r}"
        self.add(
            "default_effort",
            f"the default effort is {documented!r}" if documented else "not documented",
            verdict,
            detail,
            usage=usage,
            reasoning_tokens=_n(usage, "output_tokens_details", "reasoning_tokens"),
        )

    def _parallel_tool_calls(self) -> None:
        """Ask outright for two calls with the flag off; if it holds, check the model would
        otherwise have made two (a positive control with the flag on)."""
        request = replace(self.request, tail=f"{self.request.tail}\n\n{PARALLEL_ASK}")

        def parallel(on: bool) -> Callable[[dict[str, Any]], dict[str, Any]]:
            return lambda params: {**params, "parallel_tool_calls": on}

        off, usage, _ = self.call("parallel_tool_calls", request=request)
        n_off = len(off.tool_calls)
        assumption = "parallel_tool_calls=false limits a reply to one tool call"
        if n_off > 1:
            detail = f"asked for two calls with the flag off, got {n_off}"
            self.add("parallel_tool_calls", assumption, CONTRADICTED, detail, usage=usage)
            return
        if n_off != 1 or off.stop_reason in ("max_tokens", "refusal"):
            # A cut-off or refused reply (or no call at all) says nothing about the flag.
            detail = f"flag off: {n_off} call(s), stop {off.stop_reason}; no control needed"
            self.add("parallel_tool_calls", assumption, INCONCLUSIVE, detail, usage=usage)
            return
        on, control, _ = self.call("parallel_control", request=request, edit=parallel(True))
        n_on = len(on.tool_calls)
        detail = f"asked for two calls: {n_off} with the flag off, {n_on} with it on"
        verdict = CONFIRMED if n_on > 1 else INCONCLUSIVE
        if verdict == INCONCLUSIVE:
            detail += " (the model made one call even when allowed more)"
        self.add("parallel_tool_calls", assumption, verdict, detail, usage=usage, control=control)

    def _validated_mode(self) -> None:
        def validated(params: dict[str, Any]) -> dict[str, Any]:
            config = dict(params["config"])
            config["tool_config"] = {"function_calling_config": {"mode": "VALIDATED"}}
            return {**params, "config": config}

        assumption = "function_calling_config mode VALIDATED accepts our tool schemas"
        try:
            response, usage, _ = self.call("validated_mode", edit=validated)
        except ProviderError as exc:
            if exc.status in (400, 422):
                self.add("validated_mode", assumption, CONTRADICTED, str(exc))
                return
            raise
        tools = [c.name for c in response.tool_calls]
        self.add(
            "validated_mode",
            assumption,
            CONFIRMED,
            f"accepted; {len(tools)} tool call(s) {tools}, stop {response.stop_reason}",
            usage=usage,
        )


def probe_model(
    adapter: ProviderAdapter, ledger: Ledger, request: HarnessRequest | None = None
) -> list[Finding]:
    """Run every check that applies to this adapter's provider."""
    return _Probe(adapter, ledger, request or probe_request()).run()


# CLI: `zantine probe` --------------------------------------------------------------


def add_command(sub: Any) -> None:
    p = sub.add_parser(
        "probe",
        help="check the unverified API details (docs/model-pool.md) with a few paid calls",
    )
    p.add_argument("--models", default="all", help="comma-separated Model IDs, or all (default)")
    p.add_argument(
        "--live", action="store_true", help="allow paid API calls (also needs ZANTINE_LIVE=1)"
    )
    p.add_argument("--spend-cap", type=float, default=2.0, help="USD cap per Model (default 2)")
    p.add_argument("--out", type=Path, default=Path("runs/probe.json"), help="JSON report")


def command(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    from zantine.harness.live import LiveDisabled, require_live
    from zantine.harness.pricing import load_models
    from zantine.harness.provider import make_adapter

    pool = load_models()
    ids = sorted(pool) if args.models == "all" else [m for m in args.models.split(",") if m]
    if len(set(ids)) != len(ids):
        parser.error("a Model is listed twice; each gets one probe and one spend cap")
    unknown = [m for m in ids if m not in pool]
    if unknown:
        parser.error(f"unknown model(s) {', '.join(unknown)}; configured: {', '.join(pool)}")
    try:
        require_live(args.live)
    except LiveDisabled as exc:
        parser.error(str(exc))
    request = probe_request()
    findings: list[Finding] = []
    skipped: dict[str, str] = {}
    spent: dict[str, float] = {}
    for model_id in ids:
        spec = pool[model_id]
        spec.check_price_current()
        try:
            adapter = make_adapter(spec)
        except (ProviderError, ImportError) as exc:
            skipped[model_id] = str(exc)
            print(f"{model_id}: skipped ({exc})")
            continue
        ledger = Ledger(args.spend_cap)
        found = probe_model(adapter, ledger, request)
        findings += found
        spent[model_id] = round(ledger.spent_usd, 6)
        print(f"\n{model_id} (${ledger.spent_usd:.4f})")
        for f in found:
            print(f"  {f.verdict:<12} {f.check:<22} {f.detail}")
    report = {
        "date": dt.date.today().isoformat(),
        "request_bytes": request.text_size(),
        "spent_usd": spent,
        "skipped": skipped,
        "findings": [f.to_dict() for f in findings],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    print(f"\nprobe: {args.out} (${sum(spent.values()):.4f} total)")
    return 1 if any(f.verdict in (CONTRADICTED, FAILED) for f in findings) else 0
