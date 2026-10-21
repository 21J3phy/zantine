"""The scoring judge: TypeSafe's Jev, a typed-decision model from a lab outside the Model Pool.

API (from docs.typesafe.ai, checked 2026-10-20; not yet exercised against the
live endpoint from this repo): ``POST https://api.typesafe.ai/v1/systemone``
with ``Authorization: Bearer $TYPESAFE_API_KEY`` and a JSON body
``{"model", "state", "questions"}``; ``questions`` maps names to
``{"type": "noul"|"choice", "instructions", "criteria"}``. The reply is
``{"model", "answers", "usage": {"input_tokens", "output_tokens"}}``; a Noul
answer is ``{"noul": p}``, a Choice answer ``{"choice", "confidence",
"probabilities"}``. Input costs $0.042 per 1M tokens; output is free.

Every call: the pinned model ``jev-1.13.0`` (not the moving ``jev-latest``
alias), one ``Batch`` per request, metered through the run's ``Ledger`` with
``purpose="jev"``. The audit records the requested model, the model the
endpoint says answered, the prompt version and hash.

Safety:
- Redirects are refused, so the API key is never sent to another host.
- Every 200 reply is priced before its answers are read: a reply without valid
  ``usage`` (including one that isn't JSON or has no ``answers``) is charged at
  the worst-case estimate for its request (and flagged ``usage_estimated``),
  never as free, so retries and later batches still draw on the spend cap.
- A call that fails with no HTTP status (a network error or a reply lost to a
  read timeout) may still have been billed, so its worst-case cost stays held
  against the cap (``Ledger.hold``). An HTTP error status is a rejection and
  costs nothing.

Live gate: like live Seats, a Jev call needs an explicit flag plus
``ZANTINE_LIVE=1``, and the key comes from ``TYPESAFE_API_KEY``.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from zantine.harness.ledger import Ledger
from zantine.harness.pricing import ModelSpec
from zantine.harness.provider import (
    HarnessRequest,
    ProviderAdapter,
    ProviderError,
    ProviderResponse,
    Usage,
)
from zantine.judge.batches import PROMPT_VERSION, prompt_hash
from zantine.judge.judges import Judge
from zantine.judge.questions import Batch, JudgmentError

_FATAL_STATUSES = {400, 401, 403, 404, 413, 415, 422}
_BYTES_PER_TOKEN = 3  # same conservative estimate as ModelSpec.worst_case_cost

# POST a JSON payload; returns the raw body of a 200 reply (parsed by the adapter).
Post = Callable[[str, dict[str, str], dict[str, Any]], str]


@dataclass(frozen=True)
class JevSpec:
    model: str = "jev-1.13.0"
    base_url: str = "https://api.typesafe.ai/v1"
    api_key_env: str = "TYPESAFE_API_KEY"
    # USD per 1M tokens (docs.typesafe.ai/models, 2026-10-20).
    input: float = 0.042
    output: float = 0.0
    # Jev returns no text; output tokens are free, so this only bounds the estimate.
    max_output_tokens: int = 4096
    price_valid_until: dt.date | None = dt.date(2027, 1, 20)

    def model_spec(self) -> ModelSpec:
        """A ``ModelSpec`` so the shared ``Ledger`` can price and cap Jev calls."""
        return ModelSpec(
            id=self.model,
            lab="TypeSafe",
            provider="typesafe",
            effort="none",
            input=self.input,
            cached_input=self.input,
            cache_write=self.input,
            output=self.output,
            max_output_tokens=self.max_output_tokens,
            price_valid_until=self.price_valid_until,
        )


class JevAdapter(ProviderAdapter):
    """One ``/systemone`` call. The request's ``tail`` is the JSON body (state + questions)."""

    provider = "typesafe"

    def __init__(self, spec: JevSpec, post: Post, api_key: str | None = None) -> None:
        super().__init__(spec.model_spec())
        self.spec = spec
        self.post = post
        self.api_key = api_key

    def build_payload(self, request: HarnessRequest) -> dict[str, Any]:
        return {"model": self.spec.model, **json.loads(request.tail)}

    def send(self, request: HarnessRequest) -> ProviderResponse:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = self.spec.base_url.rstrip("/") + "/systemone"
        text = self.post(url, headers, self.build_payload(request))
        # A 200 reply may have been billed whatever its shape, so it always becomes a
        # priced response: usage first, answers validated later by the judge.
        try:
            body = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            body = None
        if not isinstance(body, dict):
            body = {}
        usage, estimated = _usage(body.get("usage"), request.text_size())
        return ProviderResponse(
            tool_calls=(),
            text=json.dumps(body.get("answers")),
            stop_reason="end_turn",
            usage=usage,
            raw={"model": body.get("model"), "usage_estimated": estimated},
        )


def _usage(raw: Any, request_bytes: int) -> tuple[Usage, bool]:
    """Reported usage if it is valid, else the worst-case input estimate (never free)."""

    def count(key: str) -> int | None:
        v = raw.get(key) if isinstance(raw, dict) else None
        return v if type(v) is int and v >= 0 else None

    inp, out = count("input_tokens"), count("output_tokens")
    if inp is None or out is None:
        return Usage(input_tokens=-(-request_bytes // _BYTES_PER_TOKEN)), True
    return Usage(input_tokens=inp, output_tokens=out), False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: the 3xx surfaces as an ``HTTPError`` instead."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def http_post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    opener: urllib.request.OpenerDirector | None = None,
) -> str:
    """POST JSON with the standard library, never following redirects; returns the body.

    An HTTP error status is a rejection (``status`` set). Any other failure,
    including one while reading the reply body (truncated, connection reset,
    timeout), has no status: the request may have been billed, so the judge
    holds its worst case and the batch takes the normal retry/error path.
    """
    opener = opener or urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ProviderError(
                f"HTTP {exc.code}: refusing to follow a redirect", fatal=True, status=exc.code
            ) from exc
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except (http.client.HTTPException, OSError):
            detail = "(error body unreadable)"
        raise ProviderError(
            f"HTTP {exc.code}: {detail}", fatal=exc.code in _FATAL_STATUSES, status=exc.code
        ) from exc
    except (http.client.HTTPException, OSError) as exc:
        # OSError covers URLError, timeouts and connection resets; HTTPException
        # covers a truncated body (IncompleteRead) and other protocol errors.
        raise ProviderError(f"{type(exc).__name__}: {exc}", fatal=False) from exc


class JevJudge(Judge):
    def __init__(self, spec: JevSpec, ledger: Ledger, adapter: ProviderAdapter) -> None:
        self.spec = spec
        self.ledger = ledger
        self.adapter = adapter
        # Which model versions the endpoint said answered.
        self.served: list[str | None] = []
        # Batches whose reply had no valid usage (charged at the worst-case estimate).
        self.usage_estimated: list[str] = []
        # Calls whose outcome is unknown (no reply, no HTTP status): worst case held.
        self.held: list[dict[str, Any]] = []

    @classmethod
    def from_env(cls, ledger: Ledger, *, live: bool, spec: JevSpec | None = None) -> JevJudge:
        """The real (paid) judge. Refuses without both opt-ins and a key."""
        from zantine.harness.live import require_live

        spec = spec or JevSpec()
        require_live(live)
        spec.model_spec().check_price_current()
        key = os.environ.get(spec.api_key_env)
        if not key:
            raise ValueError(f"{spec.api_key_env} is not set")
        return cls(spec, ledger, JevAdapter(spec, http_post, key))

    def info(self) -> dict[str, Any]:
        return {
            "name": "jev",
            "model": self.spec.model,
            "version": self.spec.model,
            "base_url": self.spec.base_url,
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": prompt_hash(),
            "served": list(self.served),
            "usage_estimated": list(self.usage_estimated),
            "held": list(self.held),
            "scoring": True,
        }

    def request(self, batch: Batch) -> HarnessRequest:
        body = json.dumps(batch.to_request(), ensure_ascii=False, sort_keys=True)
        return HarnessRequest(system="", tools=(), memory=(), tail=body)

    def judge(self, batch: Batch) -> Any:
        reserved: list[float] = []
        try:
            response = self.ledger.send(
                self.adapter,
                self.request(batch),
                purpose="jev",
                phase=batch.id,
                round=batch.round,
                on_start=reserved.append,
            )
        except ProviderError as exc:
            # An HTTP error status is a rejection, never billed. Without one the request
            # may have been processed and billed (e.g. the reply was lost to a read
            # timeout): hold its worst case against the cap, as the extractor does.
            if exc.status is None and reserved:
                self.ledger.hold(reserved[0])
                self.held.append({"batch": batch.id, "usd": round(reserved[0], 10)})
            raise
        served = response.raw.get("model")
        if served not in self.served:
            self.served.append(served)
        if response.raw.get("usage_estimated"):
            self.usage_estimated.append(batch.id)
        try:
            return json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise JudgmentError(f"not JSON: {exc}") from exc
