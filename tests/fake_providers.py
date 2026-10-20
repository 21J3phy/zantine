"""Fake clients for the non-Anthropic adapters: no network, replies in each API's own shape.

Each ``*_reply`` builds a response object shaped like that provider's SDK
(recorded shapes from the official API references), and each fake client
exposes the one method its adapter calls. ``fake_adapter`` is an
``adapter_factory`` for ``play_live`` that plays the polite fake player on
whichever provider serves the Model (xAI and Meta reuse the OpenAI fake).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from fake_provider import FakeClient as AnthropicFakeClient
from fake_provider import message as anthropic_message
from fake_provider import tool_use as anthropic_tool_use
from zantine.harness.anthropic_adapter import AnthropicAdapter
from zantine.harness.google_adapter import GoogleAdapter
from zantine.harness.meta_adapter import MetaAdapter
from zantine.harness.openai_adapter import OpenAIAdapter
from zantine.harness.pricing import ModelSpec
from zantine.harness.provider import ProviderAdapter
from zantine.harness.xai_adapter import XAIAdapter

Params = dict[str, Any]
Call = tuple[str, dict[str, Any]]  # (tool name, arguments)


class Recorder:
    """Records each call's params and answers with ``respond(params)`` (or raises it)."""

    def __init__(self, respond: Callable[[Params], Any]) -> None:
        self.respond = respond
        self.calls: list[Params] = []

    def create(self, **params: Any) -> Any:
        self.calls.append(params)
        reply = self.respond(params)
        if isinstance(reply, Exception):
            raise reply
        return reply


def polite_call(tail: str) -> Call:
    """Always legal: waits, says one line, skips the vote."""
    if "Call `speak`" in tail:
        return ("speak", {"text": "I saw nothing unusual."})
    if "vote now" in tail:
        return ("skip_vote", {})
    return ("wait", {})


# OpenAI Responses API ------------------------------------------------------------


def openai_reply(
    *calls: Call,
    text: str | None = None,
    status: str = "completed",
    incomplete: str | None = None,
    input_tokens: int = 6000,
    cached: int = 5000,
    output_tokens: int = 2000,
    reasoning_tokens: int | None = 1500,
    cost_ticks: int | None = None,
) -> SimpleNamespace:
    """A Responses API reply. OpenAI and Meta count reasoning inside ``output_tokens``;
    for an xAI-shaped reply pass the visible part only plus ``cost_ticks``."""
    output: list[SimpleNamespace] = [SimpleNamespace(type="reasoning", id="rs_1", summary=[])]
    if text is not None:
        part = SimpleNamespace(type="output_text", text=text, annotations=[])
        output.append(SimpleNamespace(type="message", role="assistant", content=[part]))
    for i, (name, args) in enumerate(calls):
        output.append(
            SimpleNamespace(
                type="function_call",
                id=f"fc_{i}",
                call_id=f"call_{i}",
                name=name,
                arguments=json.dumps(args),
                status="completed",
            )
        )
    details = (
        None if reasoning_tokens is None else SimpleNamespace(reasoning_tokens=reasoning_tokens)
    )
    return SimpleNamespace(
        id="resp_fake",
        object="response",
        status=status,
        incomplete_details=SimpleNamespace(reason=incomplete) if incomplete else None,
        output=output,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            input_tokens_details=SimpleNamespace(cached_tokens=cached),
            output_tokens=output_tokens,
            output_tokens_details=details,
            total_tokens=input_tokens + output_tokens,
            **({} if cost_ticks is None else {"cost_in_usd_ticks": cost_ticks}),
        ),
    )


def xai_reply(*calls: Call, **kw: Any) -> SimpleNamespace:
    """xAI's shape: reasoning reported on top of ``output_tokens``, plus its own charge."""
    kw.setdefault("output_tokens", 500)
    kw.setdefault("cost_ticks", 123_450_000)  # $0.012345
    return openai_reply(*calls, **kw)


def openai_tail(params: Params) -> str:
    return params["input"][-1]["content"][-1]["text"]


class OpenAIFake:
    """Fake ``openai.OpenAI`` client (also stands in for xAI's and Meta's endpoints)."""

    def __init__(
        self,
        respond: Callable[[Params], Any] | None = None,
        reply: Callable[..., Any] = openai_reply,
    ) -> None:
        self.responses = Recorder(respond or (lambda p: reply(polite_call(openai_tail(p)))))


# Gemini API (google-genai) -----------------------------------------------------


def google_reply(
    *calls: Call,
    text: str | None = None,
    finish: str = "STOP",
    block: str | None = None,
    prompt: int = 6000,
    cached: int | None = 5000,
    candidates: int = 500,
    thoughts: int | None = 1500,
) -> SimpleNamespace:
    parts: list[SimpleNamespace] = []
    if text is not None:
        parts.append(SimpleNamespace(text=text, thought=None, function_call=None))
    for name, args in calls:
        fc = SimpleNamespace(id="fc_1", name=name, args=args)
        parts.append(
            SimpleNamespace(text=None, thought=None, function_call=fc, thought_signature=b"sig")
        )
    candidate = SimpleNamespace(
        content=SimpleNamespace(role="model", parts=parts),
        finish_reason=SimpleNamespace(name=finish),
    )
    return SimpleNamespace(
        response_id="gen_fake",
        candidates=[] if block else [candidate],
        prompt_feedback=SimpleNamespace(block_reason=SimpleNamespace(name=block))
        if block
        else None,
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt,
            cached_content_token_count=cached,
            candidates_token_count=candidates,
            thoughts_token_count=thoughts,
            tool_use_prompt_token_count=None,
            total_token_count=prompt + candidates + (thoughts or 0),
        ),
    )


def google_tail(params: Params) -> str:
    return params["contents"][-1]["parts"][-1]["text"]


class _GoogleModels(Recorder):
    def generate_content(self, **params: Any) -> Any:
        return self.create(**params)


class GoogleFake:
    def __init__(self, respond: Callable[[Params], Any] | None = None) -> None:
        self.models = _GoogleModels(
            respond or (lambda p: google_reply(polite_call(google_tail(p))))
        )


# Lineup ------------------------------------------------------------------------


def fake_adapter(spec: ModelSpec) -> ProviderAdapter:
    """An adapter for any pool Model, on a fake client of its own provider."""
    match spec.provider:
        case "anthropic":

            def polite(p: Params) -> Any:
                name, args = polite_call(p["messages"][0]["content"][-1]["text"])
                return anthropic_message(anthropic_tool_use(name, **args))

            return AnthropicAdapter(spec, AnthropicFakeClient(polite))
        case "openai":
            return OpenAIAdapter(spec, OpenAIFake())
        case "google":
            return GoogleAdapter(spec, GoogleFake())
        case "xai":
            return XAIAdapter(spec, OpenAIFake(reply=xai_reply))
        case "meta":
            return MetaAdapter(spec, OpenAIFake())
    raise ValueError(f"no fake for provider {spec.provider!r}")


def calls_made(adapter: ProviderAdapter) -> int:
    client = adapter.client  # type: ignore[attr-defined]
    for path in ("messages", "responses", "models"):
        if hasattr(client, path):
            return len(getattr(client, path).calls)
    raise AssertionError("unknown fake client")
