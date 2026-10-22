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


def reasoning_accounting(usage: dict[str, Any], reasoning_in_output: bool) -> tuple[str, str]:
    """Responses API: is ``reasoning_tokens`` inside ``output_tokens`` or on top of it?"""
    inp, out = _n(usage, "input_tokens"), _n(usage, "output_tokens")
    total = _n(usage, "total_tokens")
    reasoning = _n(usage, "output_tokens_details", "reasoning_tokens")
    if None in (inp, out, total):
        return INCONCLUSIVE, "usage has no input/output/total token counts"
    if not reasoning:
        return INCONCLUSIVE, "no reasoning tokens reported on this call"
    inside, outside = total == inp + out, total == inp + out + reasoning
    nums = f"input {inp} + output {out} (reasoning {reasoning}) vs total {total}"
    if inside == outside:
        return INCONCLUSIVE, f"total matches neither reading: {nums}"
    if inside == reasoning_in_output:
        return CONFIRMED, nums
    return CONTRADICTED, nums


def cached_in_input(usage: dict[str, Any]) -> tuple[str, str]:
    """Responses API: is ``cached_tokens`` part of ``input_tokens``?"""
    inp, out = _n(usage, "input_tokens"), _n(usage, "output_tokens")
    total = _n(usage, "total_tokens")
    cached = _n(usage, "input_tokens_details", "cached_tokens")
    reasoning = _n(usage, "output_tokens_details", "reasoning_tokens") or 0
    if not cached:
        return INCONCLUSIVE, "no cached tokens on the repeated call"
    if None in (inp, out, total):
        return INCONCLUSIVE, "usage has no input/output/total token counts"
    nums = f"input {inp}, cached {cached}, output {out}, reasoning {reasoning}, total {total}"
    in_totals = {inp + out, inp + out + reasoning}
    out_totals = {inp + cached + out, inp + cached + out + reasoning}
    if cached > inp or (total in out_totals and total not in in_totals):
        return CONTRADICTED, nums
    if total in in_totals:
        return CONFIRMED, nums
    return INCONCLUSIVE, f"total matches neither reading: {nums}"


def gemini_thoughts_accounting(usage: dict[str, Any]) -> tuple[str, str]:
    """Gemini: is ``thoughts_token_count`` outside ``candidates_token_count``?"""
    prompt, cand = _n(usage, "prompt_token_count"), _n(usage, "candidates_token_count") or 0
    thoughts = _n(usage, "thoughts_token_count") or 0
    tool = _n(usage, "tool_use_prompt_token_count") or 0
    total = _n(usage, "total_token_count")
    if prompt is None or total is None:
        return INCONCLUSIVE, "usage has no prompt/total token counts"
    if not thoughts:
        return INCONCLUSIVE, "no thought tokens reported on this call"
    nums = f"prompt {prompt} + tool {tool} + candidates {cand} + thoughts {thoughts} vs {total}"
    if total == prompt + tool + cand + thoughts:
        return CONFIRMED, nums
    if total == prompt + tool + cand:
        return CONTRADICTED, nums
    return INCONCLUSIVE, f"total matches neither reading: {nums}"


def gemini_cached_in_prompt(usage: dict[str, Any]) -> tuple[str, str]:
    prompt, cached = _n(usage, "prompt_token_count"), _n(usage, "cached_content_token_count")
    if not cached:
        return INCONCLUSIVE, "no cached tokens on the repeated call"
    if prompt is None:
        return INCONCLUSIVE, "usage has no prompt token count"
    nums = f"prompt {prompt}, cached {cached}"
    return (CONFIRMED if cached <= prompt else CONTRADICTED), nums


def output_cap_scope(
    response: ProviderResponse, cap: int, cap_includes_reasoning: bool
) -> tuple[str, str]:
    """Did a call with ``max_output_tokens=cap`` spend more than ``cap`` on reasoning + output?"""
    total = response.usage.output_tokens  # normalized: visible output + thinking
    thinking = response.usage.thinking_tokens or 0
    stop = response.stop_reason
    nums = f"cap {cap}, output incl. reasoning {total} (reasoning {thinking}), stop {stop}"
    if total > cap:
        includes = False
    elif response.stop_reason == "max_tokens" and thinking:
        includes = True
    else:
        return INCONCLUSIVE, f"the call stayed under the cap: {nums}"
    return (CONFIRMED if includes == cap_includes_reasoning else CONTRADICTED), nums


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
        return CONTRADICTED, nums
    return INCONCLUSIVE, f"neither reading matches the bill (check prices in models.toml): {nums}"


# The probe -------------------------------------------------------------------------


class _Probe:
    def __init__(self, adapter: ProviderAdapter, ledger: Ledger, request: HarnessRequest) -> None:
        self.adapter = adapter
        self.ledger = ledger
        self.request = request
        self.provider = adapter.provider
        self.model = adapter.model.id
        self.findings: list[Finding] = []

    def add(self, check: str, assumption: str, verdict: str, detail: str, **evidence: Any) -> None:
        self.findings.append(Finding(self.model, check, assumption, verdict, detail, evidence))

    def call(
        self, phase: str, request: HarnessRequest | None = None, **variant: Any
    ) -> tuple[ProviderResponse, dict[str, Any], Any]:
        """One metered call; returns the normalized response, raw usage and raw reply."""
        adapter, tap = _variant(self.adapter, **variant)
        response = self.ledger.send(adapter, request or self.request, purpose="probe", phase=phase)
        return response, _usage(tap.reply, self.provider), tap.reply

    def run(self) -> list[Finding]:
        try:
            first = self._serves()
        except ProviderError as exc:
            self.add("serves_request", self._serves_assumption(), FAILED, str(exc))
            return self.findings
        self._accounting(first)
        steps = [self._cache]
        if self.provider != "anthropic":
            steps.append(self._output_cap)
        if self.provider in RESPONSES_API:
            steps += [self._default_effort, self._parallel_tool_calls]
        if self.provider == "google":
            steps.append(self._validated_mode)
        for step in steps:
            try:
                step()
            except ProviderError as exc:
                name = step.__name__.lstrip("_")
                self.add(name, "", FAILED, str(exc))
        return self.findings

    def _serves_assumption(self) -> str:
        return {
            "google": "generateContent serves this Model and accepts our tools as "
            "parameters_json_schema (mode AUTO)",
            "meta": "Muse Wonder is served on Meta's Responses API with our request "
            "(store=false, strict tools, tool_choice auto)",
        }.get(self.provider, "the API accepts our Neutral Harness request for this Model")

    def _serves(self) -> tuple[ProviderResponse, dict[str, Any], Any]:
        response, usage, reply = self.call("serves_request")
        tools = [c.name for c in response.tool_calls]
        known = {t.name for t in self.request.tools}
        detail = f"{len(tools)} tool call(s) {tools}, stop {response.stop_reason}"
        verdict = CONFIRMED if tools and set(tools) <= known else INCONCLUSIVE
        if not tools:
            detail += "; accepted, but the model made no tool call"
        self.add("serves_request", self._serves_assumption(), verdict, detail, usage=usage)
        return response, usage, reply

    def _accounting(self, first: tuple[ProviderResponse, dict[str, Any], Any]) -> None:
        response, usage, _ = first
        if self.provider in RESPONSES_API:
            inside = self.adapter.reasoning_in_output  # type: ignore[attr-defined]
            where = "inside" if inside else "on top of"
            verdict, detail = reasoning_accounting(usage, inside)
            self.add(
                "reasoning_accounting",
                f"reasoning_tokens are {where} output_tokens",
                verdict,
                detail,
                usage=usage,
            )
        if self.provider == "google":
            verdict, detail = gemini_thoughts_accounting(usage)
            self.add(
                "thoughts_accounting",
                "candidates_token_count excludes thoughts_token_count",
                verdict,
                detail,
                usage=usage,
            )
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

    def _cache(self) -> None:
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
        if self.provider in RESPONSES_API:
            verdict, detail = cached_in_input(usage)
            self.add(
                "cached_in_input",
                "input_tokens includes input_tokens_details.cached_tokens",
                verdict,
                detail,
                usage=usage,
            )
            written = _n(usage, "input_tokens_details", "cache_write_tokens")
            if self.provider == "meta":
                self.add(
                    "cache_write_field",
                    "Meta reports no cache-write tokens (no write charge)",
                    OBSERVED,
                    f"cache_write_tokens = {written}",
                    usage=usage,
                )
        if self.provider == "google":
            verdict, detail = gemini_cached_in_prompt(usage)
            self.add(
                "cached_in_prompt",
                "prompt_token_count includes cached_content_token_count",
                verdict,
                detail,
                usage=usage,
            )
        if self.provider == "xai":
            self._xai_cost(response, usage)

    def _output_cap(self) -> None:
        includes = self.provider != "xai"
        response, usage, _ = self.call("output_cap", max_output_tokens=CAP_PROBE_TOKENS)
        verdict, detail = output_cap_scope(response, CAP_PROBE_TOKENS, includes)
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
        request = replace(self.request, tail=f"{self.request.tail}\n\n{PARALLEL_ASK}")
        response, usage, reply = self.call("parallel_tool_calls", request=request)
        n = len(response.tool_calls)
        echoed = (dump(reply) or {}).get("parallel_tool_calls")
        detail = f"asked for two calls, got {n}; reply echoes parallel_tool_calls={echoed}"
        verdict = CONTRADICTED if n > 1 else (CONFIRMED if n == 1 else INCONCLUSIVE)
        self.add(
            "parallel_tool_calls",
            "parallel_tool_calls=false limits a reply to one tool call",
            verdict,
            detail,
            usage=usage,
        )

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
        try:
            found = probe_model(adapter, ledger, request)
        except SpendCapReached as exc:
            found = [Finding(model_id, "spend_cap", "", FAILED, str(exc))]
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
