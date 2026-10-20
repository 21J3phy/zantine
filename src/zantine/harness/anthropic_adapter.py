"""Anthropic Messages API adapter (Claude Fable 5.1, Claude Opus 5.5).

Request shape, per the current API:

- ``output_config={"effort": ...}`` sets reasoning effort. Thinking is always on
  for these models, so ``thinking`` is omitted (sending it disabled is a 400).
- ``tool_choice`` is ``auto`` with parallel calls disabled: forcing a tool
  (``any`` / ``tool``) is a 400 on Fable 5.1 and Opus 5.5, so the prompt asks
  for exactly one call and tools are ``strict`` to keep arguments schema-valid.
- Prompt caching: one breakpoint at the end of the system prompt (covers tools
  and system, shared by every Seat on this model) and one on the last memory
  block (the Seat's append-only log). The volatile tail comes after both.
- No refusal ``fallbacks``: a fallback would let a different Model play the
  Seat. A refusal is returned as-is and the Harness treats it as an Invalid Action.

The SDK is imported lazily, so the rest of the package works without it.
"""

from __future__ import annotations

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

EPHEMERAL = {"type": "ephemeral"}


class AnthropicAdapter(ProviderAdapter):
    provider = "anthropic"
    key_env = "ANTHROPIC_API_KEY"

    def __init__(self, model: ModelSpec, client: Any) -> None:
        """``client`` is an ``anthropic.Anthropic`` (or a test fake with ``messages.create``)."""
        super().__init__(model)
        self.client = client

    @classmethod
    def from_env(cls, model: ModelSpec) -> AnthropicAdapter:
        key = live_api_key(cls.key_env)
        import anthropic

        return cls(model, anthropic.Anthropic(api_key=key))

    def build_params(self, request: HarnessRequest) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": m} for m in request.memory if m]
        if content:
            content[-1]["cache_control"] = EPHEMERAL
        content.append({"type": "text", "text": request.tail})
        return {
            "model": self.model.id,
            "max_tokens": self.model.max_output_tokens,
            "output_config": {"effort": self.model.effort_setting},
            "system": [{"type": "text", "text": request.system, "cache_control": EPHEMERAL}],
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                    "strict": True,
                }
                for t in request.tools
            ],
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
            "messages": [{"role": "user", "content": content}],
        }

    def send(self, request: HarnessRequest) -> ProviderResponse:
        params = self.build_params(request)
        try:
            message = self.client.messages.create(**params)
        except Exception as exc:
            raise _provider_error(exc) from exc
        return normalize(message)


def normalize(message: Any) -> ProviderResponse:
    calls: list[ToolCall] = []
    texts: list[str] = []
    for block in message.content:
        if block.type == "tool_use":
            args = block.input if isinstance(block.input, dict) else {}
            calls.append(ToolCall(block.name, dict(args)))
        elif block.type == "text":
            texts.append(block.text)
    u = message.usage
    details = getattr(u, "output_tokens_details", None)
    usage = Usage(
        input_tokens=u.input_tokens or 0,
        cached_input_tokens=getattr(u, "cache_read_input_tokens", None) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", None) or 0,
        output_tokens=u.output_tokens or 0,
        thinking_tokens=getattr(details, "thinking_tokens", None),
    )
    raw: dict[str, Any] = {}
    stop_details = getattr(message, "stop_details", None)
    if stop_details is not None:
        raw["stop_details"] = {
            "category": getattr(stop_details, "category", None),
            "explanation": getattr(stop_details, "explanation", None),
        }
    return ProviderResponse(
        tool_calls=tuple(calls),
        text="\n".join(texts),
        stop_reason=message.stop_reason,
        usage=usage,
        response_id=getattr(message, "id", None),
        raw=raw,
    )


def _provider_error(exc: Exception) -> ProviderError:
    import anthropic

    if isinstance(exc, anthropic.APIStatusError):
        return status_error(type(exc).__name__, exc.status_code, exc.message)
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError(f"{type(exc).__name__}: {exc}", fatal=False)
    raise exc


register_provider("anthropic")(AnthropicAdapter.from_env)
