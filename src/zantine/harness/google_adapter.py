"""Gemini API adapter (Gemini 4 Argon), via the ``google-genai`` SDK.

Request shape, per the current API:

- ``thinking_config.thinking_level`` sets reasoning effort (the lab's own
  ``medium``). Thoughts are not returned (``include_thoughts`` off); their token
  count still is.
- The Neutral Harness system prompt goes in ``system_instruction``; memory
  blocks and the tail are the parts of one user turn, in order.
- Tools are function declarations with ``parameters_json_schema`` (full JSON
  Schema, incl. ``anyOf``); ``function_calling_config.mode`` is ``AUTO``, the
  same free choice as the other adapters (the prompt asks for exactly one call).
- Caching is implicit: a repeated prefix is discounted automatically, with no
  write charge. Explicit caches are not used (they bill storage per hour).
- Each call is single-turn, so thought signatures never need to be sent back.

Usage: ``prompt_token_count`` *includes* ``cached_content_token_count``, so the
cached part is subtracted; ``candidates_token_count`` *excludes*
``thoughts_token_count``, so output is their sum (thinking is billed as output).

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
    is_transport_error,
    live_api_key,
    register_provider,
    status_error,
)

if TYPE_CHECKING:
    from zantine.harness.pricing import ModelSpec

# finish_reason values that mean the model declined or was blocked.
_BLOCKED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}


class GoogleAdapter(ProviderAdapter):
    provider = "google"
    key_env = "GEMINI_API_KEY"

    def __init__(self, model: ModelSpec, client: Any) -> None:
        """``client`` is a ``google.genai.Client`` (or a fake with ``models.generate_content``)."""
        super().__init__(model)
        self.client = client

    @classmethod
    def from_env(cls, model: ModelSpec) -> GoogleAdapter:
        key = live_api_key(cls.key_env)
        from google import genai

        return cls(model, genai.Client(api_key=key))

    def build_params(self, request: HarnessRequest) -> dict[str, Any]:
        parts = [{"text": m} for m in request.memory if m]
        parts.append({"text": request.tail})
        return {
            "model": self.model.id,
            "contents": [{"role": "user", "parts": parts}],
            "config": {
                "system_instruction": request.system,
                "tools": [
                    {
                        "function_declarations": [
                            {
                                "name": t.name,
                                "description": t.description,
                                "parameters_json_schema": t.parameters,
                            }
                            for t in request.tools
                        ]
                    }
                ],
                "tool_config": {"function_calling_config": {"mode": "AUTO"}},
                "thinking_config": {"thinking_level": self.model.effort_setting},
                "max_output_tokens": self.model.max_output_tokens,
                "automatic_function_calling": {"disable": True},
            },
        }

    def send(self, request: HarnessRequest) -> ProviderResponse:
        params = self.build_params(request)
        try:
            response = self.client.models.generate_content(**params)
        except Exception as exc:
            raise _provider_error(exc) from exc
        return normalize(response)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _name(value: Any) -> str | None:
    """Enum or string -> its name (``FinishReason.STOP`` -> ``"STOP"``)."""
    if value is None:
        return None
    return getattr(value, "name", None) or str(value).rsplit(".", 1)[-1]


def normalize(response: Any) -> ProviderResponse:
    calls: list[ToolCall] = []
    texts: list[str] = []
    candidates = _get(response, "candidates", None) or []
    finish = _name(_get(candidates[0], "finish_reason")) if candidates else None
    if candidates:
        for part in _get(_get(candidates[0], "content"), "parts", None) or []:
            if _get(part, "thought"):
                continue
            fc = _get(part, "function_call")
            if fc is not None:
                args = _get(fc, "args", None) or {}
                calls.append(ToolCall(_get(fc, "name"), dict(args)))
            elif _get(part, "text"):
                texts.append(_get(part, "text"))
    u = _get(response, "usage_metadata")
    prompt = _get(u, "prompt_token_count", 0) or 0
    cached = _get(u, "cached_content_token_count", 0) or 0
    tool_prompt = _get(u, "tool_use_prompt_token_count", 0) or 0
    thoughts = _get(u, "thoughts_token_count")
    usage = Usage(
        input_tokens=prompt + tool_prompt - cached,
        cached_input_tokens=cached,
        cache_write_tokens=0,
        output_tokens=(_get(u, "candidates_token_count", 0) or 0) + (thoughts or 0),
        thinking_tokens=thoughts,
    )
    block = _name(_get(_get(response, "prompt_feedback"), "block_reason"))
    return ProviderResponse(
        tool_calls=tuple(calls),
        text="\n".join(texts),
        stop_reason=_stop_reason(finish, block, calls),
        usage=usage,
        response_id=_get(response, "response_id"),
        raw={"finish_reason": finish, "block_reason": block},
    )


def _stop_reason(finish: str | None, block: str | None, calls: list[ToolCall]) -> str | None:
    if block or finish in _BLOCKED:
        return "refusal"
    if finish in ("MAX_TOKENS", "CONTINUATION"):
        return "max_tokens"
    if finish in (None, "STOP"):
        return "tool_use" if calls else "end_turn"
    return finish.lower()


def _provider_error(exc: Exception) -> ProviderError:
    from google.genai import errors

    if isinstance(exc, errors.APIError):
        return status_error(type(exc).__name__, exc.code, str(exc.message or exc))
    if is_transport_error(exc):
        return ProviderError(f"{type(exc).__name__}: {exc}", fatal=False)
    raise exc


register_provider("google")(GoogleAdapter.from_env)
