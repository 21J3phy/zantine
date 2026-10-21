"""The thin provider-adapter interface: one request in, one normalized response out.

A Harness builds a provider-neutral ``HarnessRequest``; an adapter turns it into
one API call for its lab and normalizes the reply into a ``ProviderResponse``
with ``Usage`` (including cached and thinking tokens). Adapters know nothing
about the game, and Harnesses know nothing about any lab's API.

Adding a provider: subclass ``ProviderAdapter`` in its own module under
``harness/`` (set ``provider`` and ``key_env``), register a factory with
``register_provider("name")``, set ``provider = "name"`` for its models in
``models.toml``, and import the module at the bottom of this file. Factories
get the key via ``live_api_key(key_env)``, which refuses unless ``ZANTINE_LIVE=1``;
send the lab's own effort value, ``model.effort_setting``, and list the values the
lab documents in ``effort_settings`` (an adapter refuses a Model whose setting is
not one of them).
"""

from __future__ import annotations

import os
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
    full ``memory`` blocks are cacheable: between log compactions later calls for
    the same Seat repeat them byte for byte and append blocks. Compaction may
    replace older blocks. ``tail`` changes every call and must come last.
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


# HTTP statuses that will fail the same way on every call: stop the run.
FATAL_STATUSES = frozenset({400, 401, 403, 404, 413, 422})


def status_error(name: str, status: int, message: str) -> ProviderError:
    """A ``ProviderError`` for an HTTP error status, fatal if retrying can't help."""
    return ProviderError(
        f"{name} ({status}): {message}", fatal=status in FATAL_STATUSES, status=status
    )


def is_transport_error(exc: BaseException) -> bool:
    """A network failure or timeout (``httpx``/``httpx2``/stdlib), worth another attempt."""
    if isinstance(exc, ConnectionError | TimeoutError):
        return True
    return any(c.__name__ == "TransportError" for c in type(exc).__mro__)


def live_api_key(env_var: str) -> str:
    """The API key from ``env_var``, only when live calls are enabled (``ZANTINE_LIVE=1``).

    Every real adapter factory calls this first, so no client is built otherwise.
    """
    if os.environ.get("ZANTINE_LIVE") != "1":
        raise ProviderError("live calls are disabled; set ZANTINE_LIVE=1", fatal=True)
    key = os.environ.get(env_var)
    if not key:
        raise ProviderError(f"{env_var} is not set", fatal=True)
    return key


class ProviderAdapter(ABC):
    """Sends one ``HarnessRequest`` to one Model and normalizes the reply."""

    provider: str = "abstract"
    # Environment variable holding this provider's API key (keys come from env only).
    key_env: str = ""
    # The lab's own effort values, as its docs list them (``docs/model-pool.md``).
    # Empty means unchecked (test adapters); otherwise a Model whose
    # ``effort_setting`` is not listed is refused before any call is made.
    effort_settings: tuple[str, ...] = ()

    def __init__(self, model: ModelSpec) -> None:
        setting = model.effort_setting
        if self.effort_settings and setting not in self.effort_settings:
            raise ValueError(
                f"{model.id}: {self.provider} does not accept effort {setting!r} "
                f"(it takes {', '.join(self.effort_settings)}); fix `efforts` in models.toml"
            )
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
from zantine.harness import google_adapter as _google_adapter  # noqa: E402, F401
from zantine.harness import meta_adapter as _meta_adapter  # noqa: E402, F401
from zantine.harness import openai_adapter as _openai_adapter  # noqa: E402, F401
from zantine.harness import xai_adapter as _xai_adapter  # noqa: E402, F401
