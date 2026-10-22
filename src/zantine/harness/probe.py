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
from zantine.harness.ledger import Ledger, SpendCapReached
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


def _variant(
    adapter: ProviderAdapter,
    edit: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    max_output_tokens: int | None = None,
) -> tuple[ProviderAdapter, _Tap]:
    """A copy of ``adapter`` whose request is changed by ``edit`` and whose reply is kept."""
    tap = _Tap(adapter.client, adapter.provider)  # type: ignore[attr-defined]
    variant = copy.copy(adapter)
    variant.client = tap  # type: ignore[attr-defined]
    if max_output_tokens is not None:
        variant.model = replace(adapter.model, max_output_tokens=max_output_tokens)
    if edit is not None:
        build = variant.build_params  # type: ignore[attr-defined]
        variant.build_params = lambda request: edit(build(request))  # type: ignore[attr-defined]
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


@dataclass(frozen=True)
class Accounting:
    """What one call's raw counts say about two yes/no questions (None = can't tell).

    ``hidden_inside``: reasoning (thought) tokens are counted inside the output count.
    ``cache_inside``: cached tokens are counted inside the input count. Both are
    decided together from the reported total, so a total that two different
    combinations explain decides nothing. ``generated`` is everything the model
    produced (visible output plus reasoning), when ``hidden_inside`` is known.
    """

    hidden_inside: bool | None
    cache_inside: bool | None
    generated: int | None
    hidden: int
    detail: str


def resolve(
    inp: int,
    cached: int,
    out: int,
    hidden: int,
    total: int | None,
    *,
    hidden_inside: bool | None = None,
    cache_inside: bool | None = None,
) -> Accounting:
    """Which (hidden_inside, cache_inside) pairs reproduce ``total``; known answers narrow it."""
    nums = f"input {inp}, cached {cached}, output {out}, reasoning {hidden}, total {total}"
    fits = [
        (h, c)
        for h in (True, False)
        for c in (True, False)
        if hidden_inside in (None, h)
        and cache_inside in (None, c)
        and total == inp + (0 if c else cached) + out + (0 if h else hidden)
    ]

    def decided(i: int, count: int) -> bool | None:
        values = {f[i] for f in fits}
        return values.pop() if len(values) == 1 and count else None

    h, c = decided(0, hidden), decided(1, cached)
    if total is None:
        detail = f"no total to check against: {nums}"
    elif not fits:
        detail = f"no reading reproduces the total: {nums}"
    else:
        detail = nums
    if not hidden:  # nothing hidden: the output count is everything generated
        generated: int | None = out
    elif h is None:
        generated = None
    else:
        generated = out + (0 if h else hidden)
    return Accounting(h, c, generated, hidden, detail)


def responses_accounting(usage: dict[str, Any], **known: bool | None) -> Accounting:
    """Responses API usage (OpenAI, xAI, Meta)."""
    return resolve(
        _n(usage, "input_tokens") or 0,
        _n(usage, "input_tokens_details", "cached_tokens") or 0,
        _n(usage, "output_tokens") or 0,
        _n(usage, "output_tokens_details", "reasoning_tokens") or 0,
        _n(usage, "total_tokens"),
        **known,
    )


def gemini_accounting(usage: dict[str, Any], **known: bool | None) -> Accounting:
    """Gemini ``usage_metadata``: tool-use prompt tokens count as input."""
    return resolve(
        (_n(usage, "prompt_token_count") or 0) + (_n(usage, "tool_use_prompt_token_count") or 0),
        _n(usage, "cached_content_token_count") or 0,
        _n(usage, "candidates_token_count") or 0,
        _n(usage, "thoughts_token_count") or 0,
        _n(usage, "total_token_count"),
        **known,
    )


def judge(found: bool | None, assumed: bool) -> str:
    if found is None:
        return INCONCLUSIVE
    return CONFIRMED if found == assumed else CONTRADICTED


def output_cap_scope(
    acc: Accounting, stop: str | None, cap: int, cap_includes_reasoning: bool
) -> tuple[str, str]:
    """Did a call with ``max_output_tokens=cap`` generate more than ``cap`` tokens?"""
    nums = f"cap {cap}, generated {acc.generated} (reasoning {acc.hidden}), stop {stop}"
    if acc.generated is None:
        return INCONCLUSIVE, f"how reasoning is counted is unresolved: {acc.detail}"
    if acc.generated > cap:
        includes = False
    elif stop == "max_tokens" and acc.hidden:
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


class _Probe:
    def __init__(self, adapter: ProviderAdapter, ledger: Ledger, request: HarnessRequest) -> None:
        self.adapter = adapter
        self.ledger = ledger
        self.request = request
        self.provider = adapter.provider
        self.model = adapter.model.id
        self.findings: list[Finding] = []
        # What the raw counts settle, carried from call to call (None = unresolved).
        self.hidden_inside: bool | None = None
        self.cache_inside: bool | None = None
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
            raise
        return response, _usage(tap.reply, self.provider), tap.reply

    def accounting(self, usage: dict[str, Any]) -> Accounting:
        known = {"hidden_inside": self.hidden_inside, "cache_inside": self.cache_inside}
        if self.provider == "google":
            return gemini_accounting(usage, **known)
        return responses_accounting(usage, **known)

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
        if self.provider == "xai":
            self._xai_cost(response, usage)

    def _xai_cost(self, response: ProviderResponse, usage: dict[str, Any]) -> None:
        ours = self.ledger.records[-1].cost_usd
        visible = _n(usage, "output_tokens") or 0
        alternative = self.adapter.model.cost(replace(response.usage, output_tokens=visible))
        verdict, detail = provider_cost_match(
            ours, response.raw.get("provider_cost_usd"), alternative
        )
        self.add(
            "xai_billed_cost",
            "our token normalization and prices reproduce xAI's own charge",
            verdict,
            detail,
            usage=usage,
        )

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
        if self.provider == "xai":
            self._xai_cost(response, usage)
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
        """Decide both questions from the two identical calls, each narrowing the other."""
        for _ in range(2):  # an answer from one call can settle the other call's question
            for usage in (first, second):
                acc = self.accounting(usage)
                self.hidden_inside = (
                    self.hidden_inside if acc.hidden_inside is None else acc.hidden_inside
                )
                self.cache_inside = (
                    self.cache_inside if acc.cache_inside is None else acc.cache_inside
                )
        details = (
            f"first call: {self.accounting(first).detail}; repeat: {self.accounting(second).detail}"
        )
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
            (hidden, self.hidden_inside),
            (cache, self.cache_inside),
        ):
            self.add(check, assumption, judge(found, assumed), details, first=first, repeat=second)

    def _output_cap_scope(self) -> None:
        includes = self.provider != "xai"
        response, usage, _ = self.call("output_cap", max_output_tokens=CAP_PROBE_TOKENS)
        acc = self.accounting(usage)
        verdict, detail = output_cap_scope(acc, response.stop_reason, CAP_PROBE_TOKENS, includes)
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
