"""A fake Anthropic client for harness tests: no network, scripted replies."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from zantine.harness.anthropic_adapter import AnthropicAdapter
from zantine.harness.pricing import ModelSpec

Params = dict[str, Any]


def tool_use(name: str, **arguments: Any) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id="toolu_x", name=name, input=arguments)


def text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def message(
    *blocks: SimpleNamespace,
    stop_reason: str = "tool_use",
    input_tokens: int = 1000,
    cache_read: int = 5000,
    cache_write: int = 50,
    output_tokens: int = 2000,
    thinking_tokens: int | None = 1500,
) -> SimpleNamespace:
    details = None if thinking_tokens is None else SimpleNamespace(thinking_tokens=thinking_tokens)
    return SimpleNamespace(
        id="msg_fake",
        type="message",
        content=list(blocks),
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
            output_tokens=output_tokens,
            output_tokens_details=details,
        ),
    )


def request_text(params: Params) -> str:
    return "\n".join(b["text"] for b in params["messages"][0]["content"])


def phase_of(params: Params) -> str:
    tail = params["messages"][0]["content"][-1]["text"]
    if "Call `speak`" in tail:
        return "speak"
    if "vote now" in tail:
        return "vote"
    return "action"


def polite_player(params: Params) -> SimpleNamespace:
    """Always legal: waits, says one line, skips the vote."""
    match phase_of(params):
        case "speak":
            return message(tool_use("speak", text="I saw nothing unusual."))
        case "vote":
            return message(tool_use("skip_vote"))
    return message(tool_use("wait"))


class FakeMessages:
    def __init__(self, respond: Callable[[Params], Any]) -> None:
        self.respond = respond
        self.calls: list[Params] = []

    def create(self, **params: Any) -> Any:
        self.calls.append(params)
        reply = self.respond(params)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeClient:
    def __init__(self, respond: Callable[[Params], Any] = polite_player) -> None:
        self.messages = FakeMessages(respond)


def fake_factory(client: FakeClient) -> Callable[[ModelSpec], AnthropicAdapter]:
    return lambda spec: AnthropicAdapter(spec, client)
