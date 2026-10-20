"""Label Panel judges: label one anonymized view with the four labels.

- ``LLMPanelJudge``: a pinned open-weight model behind an OpenAI-compatible
  endpoint (the adapter the scoring extractor uses), temperature 0, structured
  output constrained to ``prompt.label_schema``, metered through the run's
  ``Ledger`` with ``purpose="label_panel"``. Paid; live-gated.
- ``ScriptedPanelJudge``: labels computed by a function; for tests.

``info()`` is written into every panel report so results always say which
judge (family, model, revision, prompt) produced them.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from zantine.audit.llm import SEED, OpenAICompatAdapter
from zantine.harness.ledger import Ledger
from zantine.harness.provider import HarnessRequest, ProviderAdapter, ProviderError, ToolSpec
from zantine.judge.jev import http_post as _post_no_redirect
from zantine.panel.anonymize import View
from zantine.panel.config import JudgeSpec
from zantine.panel.prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    LabelError,
    label_schema,
    prompt_hash,
    render_input,
)

PURPOSE = "label_panel"


class PanelJudge(ABC):
    id: str

    @abstractmethod
    def info(self) -> dict[str, Any]:
        """Pinned identity: at least ``id``, ``family``, ``model``, ``revision``, ``scoring``."""

    @abstractmethod
    def label(self, view: View, item_id: str) -> Any:
        """Raw labels for one view (validated by the caller with ``parse_labels``).

        Raise ``prompt.LabelError`` for unusable output or ``ProviderError`` when a call fails.
        """


class ScriptedPanelJudge(PanelJudge):
    def __init__(
        self,
        answer: Callable[[View, str], Any],
        *,
        id: str = "scripted",
        family: str = "Test",
    ) -> None:
        self.answer = answer
        self.id = id
        self.family = family
        self.views: list[View] = []
        self.requests: list[str] = []

    def info(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "family": self.family,
            "model": None,
            "revision": "test",
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": prompt_hash(),
            "scoring": False,
        }

    def label(self, view: View, item_id: str) -> Any:
        self.views.append(view)
        self.requests.append(render_input(view.data))
        return self.answer(view, item_id)


def post_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    """POST without following redirects (the key never goes to another host)."""
    text = _post_no_redirect(url, headers, payload)
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"reply is not JSON: {exc}", fatal=False) from exc
    if not isinstance(body, dict):
        raise ProviderError("reply is not a JSON object", fatal=False)
    return body


class LLMPanelJudge(PanelJudge):
    def __init__(self, spec: JudgeSpec, ledger: Ledger, adapter: ProviderAdapter) -> None:
        self.spec = spec
        self.id = spec.id
        self.ledger = ledger
        self.adapter = adapter
        # What the endpoint said it served: (model, system_fingerprint) per distinct reply.
        self.served: list[dict[str, Any]] = []

    @classmethod
    def from_env(cls, spec: JudgeSpec, ledger: Ledger, *, live: bool) -> LLMPanelJudge:
        """The real (paid) judge. Refuses without both opt-ins, a pinned judge and a key."""
        from zantine.harness.live import require_live

        require_live(live)
        spec.check_pinned()
        spec.model_spec().check_price_current()
        key = os.environ.get(spec.api_key_env) if spec.api_key_env else None
        if spec.api_key_env and not key:
            raise ValueError(f"{spec.api_key_env} is not set")
        return cls(spec, ledger, OpenAICompatAdapter(spec.endpoint(), post_json, key))

    def info(self) -> dict[str, Any]:
        return {
            **self.spec.describe(),
            "temperature": 0,
            "seed": SEED,
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": prompt_hash(),
            "served": list(self.served),
            "scoring": True,
        }

    def request(self, view: View) -> HarnessRequest:
        tool = ToolSpec("labels", "Labels for one Claim.", label_schema(view.push_options))
        return HarnessRequest(
            system=SYSTEM_PROMPT, tools=(tool,), memory=(), tail=render_input(view.data)
        )

    def label(self, view: View, item_id: str) -> Any:
        response = self.ledger.send(
            self.adapter, self.request(view), purpose=PURPOSE, phase=self.id
        )
        served = {k: response.raw.get(k) for k in ("model", "system_fingerprint")}
        if served not in self.served:
            self.served.append(served)
        if response.stop_reason == "max_tokens":
            raise LabelError("output cut off at max_tokens")
        try:
            return json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise LabelError(f"not JSON: {exc}") from exc
