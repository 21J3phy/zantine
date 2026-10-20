"""A Seat played by a live Model through the Neutral Harness.

``HarnessSeat`` turns each context into a ``HarnessRequest`` (``harness.neutral``),
sends it through the run's ``Ledger`` (cost and spend cap) to a provider adapter,
and parses the reply. Unusable replies and transient API errors become Invalid
Actions, so the engine's one-retry-then-forfeit rule applies. ``SpendCapReached``
and fatal ``ProviderError`` propagate and stop the run (see ``harness.live``).

The ``live`` controller name is registered so ``--bots live:<model>`` parses,
but live Seats are built only by ``harness.live.play_live``, which needs an
explicit ``--live`` flag plus ``ZANTINE_LIVE=1``.

Alternative Harnesses subclass ``HarnessSeat``, set ``harness_name`` and
``harness_version``, and register with ``@register_harness``; a Seat picks one
with ``live:<model>@<harness>`` (default: ``neutral``). Every Harness sends
through the same ``Ledger`` and adapters, so cost logs compare directly.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from zantine.harness import neutral
from zantine.harness.ledger import Ledger
from zantine.harness.provider import (
    HarnessRequest,
    ProviderAdapter,
    ProviderError,
    ProviderResponse,
)
from zantine.observations import ActionContext, MeetingContext
from zantine.rules import Rules, default_rules
from zantine.rules.actions import Action, Invalid
from zantine.seats.base import SeatController
from zantine.seats.registry import register_controller

DEFAULT_HARNESS = neutral.HARNESS_NAME


class HarnessSeat(SeatController):
    """A live Seat played through the Neutral Harness."""

    kind = "live"
    harness_name = neutral.HARNESS_NAME
    harness_version = neutral.HARNESS_VERSION

    def __init__(
        self, adapter: ProviderAdapter, ledger: Ledger, rules: Rules | None = None
    ) -> None:
        self.adapter = adapter
        self.ledger = ledger
        # The game's Rules (for action tools and parsing); ``play_live`` binds them.
        self.rules = rules or default_rules()
        # Our own parse error from the last attempt, shown on the engine's retry.
        self._last_error: str | None = None
        # Ground-truth event recorder (``game.event``); ``play_live`` binds it.
        self.record_event: Callable[..., None] | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            **self.adapter.model.describe(),
            "harness": self.harness_label(),
        }

    @classmethod
    def harness_label(cls) -> str:
        return f"{cls.harness_name}-v{cls.harness_version}"

    def _send(
        self, request: HarnessRequest, phase: str, seat: int, round: int, attempt: int
    ) -> ProviderResponse | str:
        """The response, or the reason there is none (transient API error)."""
        try:
            return self.ledger.send(
                self.adapter,
                request,
                purpose="seat",
                phase=phase,
                seat=seat,
                round=round,
                attempt=attempt,
                harness=self.harness_name,
                harness_version=self.harness_version,
            )
        except ProviderError as exc:
            if exc.fatal:
                raise
            return f"model API error: {exc}"

    def _retry_reason(self, engine_reason: str | None) -> str | None:
        if engine_reason is None:
            self._last_error = None
            return None
        return self._last_error or engine_reason

    def choose_action(self, ctx: ActionContext) -> Action:
        retry = self._retry_reason(ctx.retry_reason)
        attempt = 2 if ctx.retry_reason else 1
        request = neutral.action_request(self.rules, ctx, retry)
        response = self._send(request, "action", ctx.seat, ctx.round, attempt)
        if isinstance(response, str):
            action: Action = Invalid(reason=response)
        else:
            action = neutral.parse_action(self.rules, response)
        self._last_error = action.reason if isinstance(action, Invalid) else None
        return action

    def speak(self, ctx: MeetingContext) -> str:
        # The engine doesn't retry speech, so the same rule is applied here:
        # one retry with the reason, then the Seat says nothing.
        retry: str | None = None
        for attempt in (1, 2):
            request = neutral.speak_request(self.rules, ctx, retry)
            response = self._send(request, "speak", ctx.seat, ctx.round, attempt)
            if isinstance(response, str):
                text, retry = None, response
            else:
                text, retry = neutral.parse_speech(response)
            if text is not None:
                return text
            self._invalid_speech(ctx, attempt, retry or "invalid speech")
        return ""

    def _invalid_speech(self, ctx: MeetingContext, attempt: int, reason: str) -> None:
        if self.record_event is not None:
            self.record_event(
                "harness.invalid_speech",
                seat=ctx.seat,
                model=self.adapter.model.id,
                meeting=ctx.meeting,
                pass_index=ctx.pass_index,
                attempt=attempt,
                reason=reason,
                forfeited=attempt == 2,
            )

    def vote(self, ctx: MeetingContext) -> int | neutral.BadVote | None:
        retry = self._retry_reason(ctx.retry_reason)
        attempt = 2 if ctx.retry_reason else 1
        request = neutral.vote_request(self.rules, ctx, retry)
        response = self._send(request, "vote", ctx.seat, ctx.round, attempt)
        vote = (
            neutral.BadVote(response) if isinstance(response, str) else neutral.parse_vote(response)
        )
        self._last_error = vote.reason if isinstance(vote, neutral.BadVote) else None
        return vote


_HARNESSES: dict[str, type[HarnessSeat]] = {}


def register_harness(cls: type[HarnessSeat]) -> type[HarnessSeat]:
    if cls.harness_name in _HARNESSES:
        raise ValueError(f"harness {cls.harness_name!r} already registered")
    _HARNESSES[cls.harness_name] = cls
    return cls


register_harness(HarnessSeat)


def harness_names() -> list[str]:
    return sorted(_HARNESSES)


def parse_live_arg(arg: str | None) -> tuple[str, type[HarnessSeat]]:
    """``<model>[@<harness>]`` -> (model ID, Seat class for that Harness)."""
    model, at, harness = (arg or "").partition("@")
    if not model:
        raise ValueError("live seats need a model, e.g. live:claude-fable-5-1")
    if at and not harness:
        raise ValueError(f"live:{arg} names no Harness; known: {', '.join(harness_names())}")
    name = harness or DEFAULT_HARNESS
    if name not in _HARNESSES:
        raise ValueError(f"unknown harness {name!r}; known: {', '.join(harness_names())}")
    return model, _HARNESSES[name]


@register_controller("live")
def _live_factory(rng: Any, arg: str | None) -> SeatController:
    raise ValueError(
        "live seats run only via `zantine play --live` with ZANTINE_LIVE=1 and a spend cap"
    )


from zantine.seats import agent_loop as _agent_loop  # noqa: E402, F401  (registers "agent-loop")
