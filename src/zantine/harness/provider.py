"""The thin provider-adapter interface: one request in, one normalized response out.

A Harness builds a provider-neutral ``HarnessRequest``; an adapter turns it into
one API call for its lab and normalizes the reply into a ``ProviderResponse``
with ``Usage`` (including cached and thinking tokens). Adapters know nothing
about the game, and Harnesses know nothing about any lab's API.

Adding a provider (issue #10): subclass ``ProviderAdapter`` in its own module
under ``harness/``, register a factory with ``@register_provider("name")``, set
``provider = "name"`` for its models in ``models.toml``, and import the module at
the bottom of this file. Factories must read API keys from the environment only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zantine.harness.pricing import ModelSpec


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class HarnessRequest:
    """One provider-neutral model call.

    The prompt is ``system`` + ``memory`` + ``tail``. ``system``, ``tools`` and
    every ``memory`` block are stable: later calls for the same Seat repeat them
    byte for byte and only append new memory blocks, so adapters can mark them
    for prompt caching. ``tail`` changes every call and must come last.
    """

    system: str
    tools: tuple[ToolSpec, ...]
    memory: tuple[str, ...]
    tail: str

    def text_size(self) -> int:
        """Rough size in UTF-8 bytes of everything sent, for worst-case cost estimates."""
        parts = [self.system, self.tail, *self.memory]
        parts += [t.name + t.description + repr(t.parameters) for t in self.tools]
        return sum(len(p.encode("utf-8")) for p in parts)


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    """Token counts for one call, normalized across providers.

    - ``input_tokens``: input billed at the full input price (excludes the two cache fields)
    - ``cached_input_tokens``: input read from the prompt cache
    - ``cache_write_tokens``: input written to the prompt cache
    - ``output_tokens``: all output, *including* thinking tokens
    - ``thinking_tokens``: the part of ``output_tokens`` spent thinking (None if unreported)
    """

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderResponse:
    tool_calls: tuple[ToolCall, ...]
    text: str
    # Normalized: "end_turn", "tool_use", "max_tokens", "refusal", or the provider's own value.
    stop_reason: str | None
    usage: Usage
    response_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """A call that produced no usable response.

    ``fatal`` errors (bad key, unknown model, malformed request) will fail
    again on every call, so the run stops. Others (rate limits, overload,
    network) become an Invalid Action for that attempt.
    """

    def __init__(self, message: str, *, fatal: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.fatal = fatal
        self.status = status


class ProviderAdapter(ABC):
    """Sends one ``HarnessRequest`` to one Model and normalizes the reply."""

    provider: str = "abstract"

    def __init__(self, model: ModelSpec) -> None:
        self.model = model

    @abstractmethod
    def send(self, request: HarnessRequest) -> ProviderResponse:
        """Make exactly one API call. Raise ``ProviderError`` if there's no response."""


AdapterFactory = Callable[["ModelSpec"], ProviderAdapter]

_PROVIDERS: dict[str, AdapterFactory] = {}


def register_provider(name: str) -> Callable[[AdapterFactory], AdapterFactory]:
    def decorator(factory: AdapterFactory) -> AdapterFactory:
        if name in _PROVIDERS:
            raise ValueError(f"provider {name!r} already registered")
        _PROVIDERS[name] = factory
        return factory

    return decorator


def make_adapter(model: ModelSpec) -> ProviderAdapter:
    """Build the real (paid) adapter for a Model. Callers must check the live gate first."""
    factory = _PROVIDERS.get(model.provider)
    if factory is None:
        known = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"no adapter for provider {model.provider!r} (known: {known})")
    return factory(model)


from zantine.harness import anthropic_adapter as _anthropic_adapter  # noqa: E402, F401
