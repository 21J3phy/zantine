"""OpenAI Responses API adapter (GPT-6 Astra, GPT-6.1 Sol).

Request shape, per the current API:

- ``reasoning={"effort": ...}`` sets reasoning effort (the lab's own ``medium``).
- The Neutral Harness system prompt goes in ``instructions``; memory blocks and
  the tail are one user message of ``input_text`` parts, in order, so the stable
  prefix (tools, instructions, memory) is byte-identical across a Seat's calls.
- Tool calling on these models needs the Responses API (Chat Completions has none).
- Prompt caching is implicit (the default) for prefixes of 1,024+ tokens.
- Function tools are ``strict``; ``tool_choice`` is ``auto`` with
  ``parallel_tool_calls`` off, the same as the other adapters (the prompt asks
  for exactly one call).
- ``store=False``: nothing is kept server-side; each call is self-contained.

Usage: ``input_tokens`` *includes* ``input_tokens_details.cached_tokens`` (cache
reads) and ``cache_write_tokens`` (cache writes, billed at 1.25x input), so both
are subtracted for the normalized ``Usage``. ``output_tokens`` already includes
``output_tokens_details.reasoning_tokens`` (billed as output).

Other labs serving an OpenAI-compatible Responses API (xAI, Meta) subclass this
adapter with their own ``base_url`` and ``key_env``; ``reasoning_in_output = False``
handles APIs that report reasoning tokens outside ``output_tokens``.

The SDK is imported lazily, so the rest of the package works without it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from zantine.harness.provider import (
    HarnessRequest,
    ProviderAdapter,
    ProviderError,
    ProviderResponse,
    ToolCall,
    Usage,
    live_api_key,
    register_provider,
    status_error,
)

if TYPE_CHECKING:
    from zantine.harness.pricing import ModelSpec


class OpenAIAdapter(ProviderAdapter):
    provider = "openai"
    key_env = "OPENAI_API_KEY"
    # None = the SDK's default (api.openai.com). Labs serving an OpenAI-compatible
    # Responses API (xAI, Meta) subclass this adapter and set their own.
    base_url: str | None = None
    # Whether ``output_tokens`` already includes ``reasoning_tokens`` (OpenAI: yes).
    reasoning_in_output = True

    def __init__(self, model: ModelSpec, client: Any) -> None:
        """``client`` is an ``openai.OpenAI`` (or a test fake with ``responses.create``)."""
        super().__init__(model)
        self.client = client

    @classmethod
    def from_env(cls, model: ModelSpec) -> OpenAIAdapter:
        key = live_api_key(cls.key_env)
        import openai

        return cls(model, openai.OpenAI(api_key=key, base_url=cls.base_url))

    def build_params(self, request: HarnessRequest) -> dict[str, Any]:
        parts = [{"type": "input_text", "text": m} for m in request.memory if m]
        parts.append({"type": "input_text", "text": request.tail})
        return {
            "model": self.model.id,
            "instructions": request.system,
            "input": [{"role": "user", "content": parts}],
            "tools": [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "strict": True,
                }
                for t in request.tools
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "reasoning": {"effort": self.model.effort_setting},
            "max_output_tokens": self.model.max_output_tokens,
            "store": False,
        }

    def send(self, request: HarnessRequest) -> ProviderResponse:
        params = self.build_params(request)
        try:
            response = self.client.responses.create(**params)
        except Exception as exc:
            raise _provider_error(exc) from exc
        return normalize(response, reasoning_in_output=self.reasoning_in_output)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def normalize(response: Any, *, reasoning_in_output: bool = True) -> ProviderResponse:
    calls: list[ToolCall] = []
    texts: list[str] = []
    refused = False
    for item in _get(response, "output", None) or []:
        kind = _get(item, "type")
        if kind == "function_call":
            calls.append(ToolCall(_get(item, "name"), _arguments(_get(item, "arguments"))))
        elif kind == "message":
            for part in _get(item, "content", None) or []:
                if _get(part, "type") == "output_text":
                    texts.append(_get(part, "text", ""))
                elif _get(part, "type") == "refusal":
                    refused = True
                    texts.append(_get(part, "refusal", ""))
    u = _get(response, "usage")
    total_in = _get(u, "input_tokens", 0) or 0
    details = _get(u, "input_tokens_details")
    cached = _get(details, "cached_tokens", 0) or 0
    written = _get(details, "cache_write_tokens", 0) or 0
    reasoning = _get(_get(u, "output_tokens_details"), "reasoning_tokens")
    output = _get(u, "output_tokens", 0) or 0
    if not reasoning_in_output:
        output += reasoning or 0
    usage = Usage(
        input_tokens=total_in - cached - written,
        cached_input_tokens=cached,
        cache_write_tokens=written,
        output_tokens=output,
        thinking_tokens=reasoning,
    )
    raw: dict[str, Any] = {"status": _get(response, "status")}
    ticks = _get(u, "cost_in_usd_ticks")
    if ticks is not None:
        # xAI reports its own charge (1e10 ticks = $1); kept to cross-check our pricing.
        raw["provider_cost_usd"] = ticks / 1e10
    return ProviderResponse(
        tool_calls=tuple(calls),
        text="\n".join(texts),
        stop_reason=_stop_reason(response, calls, refused),
        usage=usage,
        response_id=_get(response, "id"),
        raw=raw,
    )


def _arguments(raw: Any) -> dict[str, Any]:
    """Function arguments arrive as a JSON string; anything unparseable is ``{}``."""
    if isinstance(raw, dict):
        return dict(raw)
    try:
        args = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return args if isinstance(args, dict) else {}


def _stop_reason(response: Any, calls: list[ToolCall], refused: bool) -> str | None:
    if refused:
        return "refusal"
    if _get(response, "status") == "incomplete":
        reason = _get(_get(response, "incomplete_details"), "reason")
        if reason == "max_output_tokens":
            return "max_tokens"
        if reason == "content_filter":
            return "refusal"
        return reason
    return "tool_use" if calls else "end_turn"


def _provider_error(exc: Exception) -> ProviderError:
    import openai

    if isinstance(exc, openai.APIStatusError):
        return status_error(type(exc).__name__, exc.status_code, exc.message)
    if isinstance(exc, openai.APIConnectionError):
        return ProviderError(f"{type(exc).__name__}: {exc}", fatal=False)
    raise exc


register_provider("openai")(OpenAIAdapter.from_env)
