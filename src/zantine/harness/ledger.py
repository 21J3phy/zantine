"""The cost ledger and spend cap: every paid call in a run goes through ``Ledger.send``.

``send`` refuses to start a call whose worst-case cost would take total spend
past the cap (raising ``SpendCapReached``), makes the call, prices its
``Usage`` and records a ``CallRecord``. Seats, the Claim extractor and Jev calls
share one ledger per run, so one cap covers everything. Log summarization is
pure code and makes no paid calls.

``sink`` (optional) receives every record as it is made; the live runner uses
it to write ``harness.call`` events into the game transcript.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from zantine.harness.provider import (
    HarnessRequest,
    ProviderAdapter,
    ProviderError,
    ProviderResponse,
)

if TYPE_CHECKING:
    from zantine.harness.pricing import ModelSpec


class SpendCapReached(Exception):
    """The next call could take total spend past the cap; the run must stop."""

    def __init__(self, spent: float, cap: float, needed: float) -> None:
        super().__init__(
            f"spend cap reached: ${spent:.4f} spent of ${cap:.2f}; "
            f"next call could cost up to ${needed:.4f}"
        )
        self.spent = spent
        self.cap = cap
        self.needed = needed


@dataclass(frozen=True)
class CallRecord:
    model: str
    purpose: str  # "seat" for players; "extractor", "jev", ...
    phase: str  # e.g. "action", "speak", "vote"
    seat: int | None
    round: int | None
    attempt: int
    ok: bool
    error: str | None
    stop_reason: str | None
    input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    thinking_tokens: int | None
    cost_usd: float
    total_spent_usd: float
    # The provider's own charge for the call, where its API reports one (xAI).
    provider_cost_usd: float | None = None
    # The Harness that built the request (``"neutral"``, 5); None for non-seat callers.
    harness: str | None = None
    harness_version: int | None = None
    # The call's effort level (low / medium / high) and the lab's own value sent for it.
    effort: str | None = None
    effort_setting: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Ledger:
    def __init__(
        self, spend_cap_usd: float, sink: Callable[[CallRecord], None] | None = None
    ) -> None:
        if spend_cap_usd <= 0:
            raise ValueError("spend cap must be positive")
        self.spend_cap_usd = spend_cap_usd
        self.sink = sink
        self.records: list[CallRecord] = []
        self.spent_usd = 0.0

    def send(
        self,
        adapter: ProviderAdapter,
        request: HarnessRequest,
        *,
        purpose: str,
        phase: str,
        seat: int | None = None,
        round: int | None = None,
        attempt: int = 1,
        harness: str | None = None,
        harness_version: int | None = None,
        on_start: Callable[[float], None] | None = None,
    ) -> ProviderResponse:
        """Make one metered call. Raises ``SpendCapReached`` or ``ProviderError``.

        Seats pass their ``harness`` name and version, so calls made by different
        Harnesses for the same Model are logged with the same fields side by side.
        ``on_start`` gets the call's worst-case cost once the cap allows it, just
        before the request is sent (so a caller can journal the call as in flight).
        """
        model = adapter.model
        needed = model.worst_case_cost(request.text_size())
        if self.spent_usd + needed > self.spend_cap_usd:
            raise SpendCapReached(self.spent_usd, self.spend_cap_usd, needed)
        meta = {
            "purpose": purpose,
            "phase": phase,
            "seat": seat,
            "round": round,
            "harness": harness,
            "harness_version": harness_version,
        }
        self._reserve(model, needed, attempt)
        if on_start is not None:
            on_start(needed)
        try:
            response = adapter.send(request)
        except ProviderError as exc:
            # No HTTP status: the request may have been processed and billed (a reply
            # lost in transit or without trustworthy usage), so the outcome is uncertain.
            self._record(model, meta, attempt, None, error=str(exc), uncertain=exc.status is None)
            raise
        self._record(model, meta, attempt, response, error=None)
        return response

    def hold(self, amount_usd: float) -> None:
        """Count ``amount_usd`` against the cap with no call record: e.g. the worst
        case of a call lost in transit, which the provider may still have billed."""
        self.spent_usd += amount_usd

    def _reserve(self, model: ModelSpec, needed: float, attempt: int) -> None:
        """Called once a call has passed the cap check, before it is sent; every
        reservation is followed by exactly one ``_record`` unless the process dies.
        ``runs.RunLedger`` persists it so a crash mid-call cannot hide spend."""

    def _record(
        self,
        model: ModelSpec,
        meta: dict[str, Any],
        attempt: int,
        response: ProviderResponse | None,
        error: str | None,
        uncertain: bool = False,
    ) -> None:
        """Price and keep one call. ``uncertain`` (a failure without an HTTP status)
        is for journaling ledgers; callers that may have been billed ``hold``."""
        usage = response.usage if response else None
        cost = model.cost(usage) if usage is not None else 0.0
        self.spent_usd += cost
        record = CallRecord(
            model=model.id,
            attempt=attempt,
            ok=response is not None,
            error=error,
            stop_reason=response.stop_reason if response else None,
            input_tokens=usage.input_tokens if usage else 0,
            cached_input_tokens=usage.cached_input_tokens if usage else 0,
            cache_write_tokens=usage.cache_write_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            thinking_tokens=usage.thinking_tokens if usage else None,
            cost_usd=round(cost, 8),
            total_spent_usd=round(self.spent_usd, 8),
            provider_cost_usd=response.raw.get("provider_cost_usd") if response else None,
            effort=model.effort,
            effort_setting=model.effort_setting,
            **meta,
        )
        self.records.append(record)
        if self.sink is not None:
            self.sink(record)


_TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "output_tokens",
    "thinking_tokens",
)


def summarize(records: Iterable[CallRecord]) -> dict[str, Any]:
    """Totals for a set of calls: calls, failed calls, every token count and dollars."""
    out: dict[str, Any] = {"calls": 0, "failed_calls": 0, "cost_usd": 0.0}
    out |= {f: 0 for f in _TOKEN_FIELDS}
    for r in records:
        out["calls"] += 1
        out["failed_calls"] += not r.ok
        out["cost_usd"] += r.cost_usd
        for f in _TOKEN_FIELDS:
            out[f] += getattr(r, f) or 0
    out["cost_usd"] = round(out["cost_usd"], 6)
    return out
