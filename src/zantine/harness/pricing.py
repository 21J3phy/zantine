"""Model IDs, effort settings and prices, loaded from ``models.toml``.

``cost(usage)`` turns a normalized ``Usage`` into dollars; ``worst_case_cost``
bounds a call before it is made, so the spend cap can refuse it.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from zantine.harness.provider import Usage

PER_TOKEN = 1 / 1_000_000
# Conservative bytes-per-token for estimating input before a call (real text is ~4).
_BYTES_PER_TOKEN = 3


@dataclass(frozen=True)
class ModelSpec:
    id: str
    lab: str
    provider: str
    effort: str
    # USD per 1M tokens.
    input: float
    cached_input: float
    cache_write: float
    output: float
    max_output_tokens: int
    price_valid_until: dt.date | None = None

    def cost(self, usage: Usage) -> float:
        return PER_TOKEN * (
            usage.input_tokens * self.input
            + usage.cached_input_tokens * self.cached_input
            + usage.cache_write_tokens * self.cache_write
            + usage.output_tokens * self.output
        )

    def worst_case_cost(self, request_bytes: int) -> float:
        """Upper estimate for one call: all input at the dearest input rate, full output cap."""
        input_tokens = -(-request_bytes // _BYTES_PER_TOKEN)
        top_input = max(self.input, self.cached_input, self.cache_write)
        return PER_TOKEN * (input_tokens * top_input + self.max_output_tokens * self.output)

    def check_price_current(self, today: dt.date | None = None) -> None:
        today = today or dt.date.today()
        if self.price_valid_until is not None and today > self.price_valid_until:
            raise ValueError(
                f"price for {self.id} expired on {self.price_valid_until}; update models.toml"
            )

    def describe(self) -> dict[str, Any]:
        return {"model": self.id, "provider": self.provider, "effort": self.effort}


def load_models(path: str | Path | None = None) -> dict[str, ModelSpec]:
    """Every configured Model, by API ID. Defaults to the packaged ``models.toml``."""
    if path is None:
        text = resources.files("zantine.harness").joinpath("models.toml").read_text("utf-8")
    else:
        text = Path(path).read_text("utf-8")
    data = tomllib.loads(text)
    return {mid: ModelSpec(id=mid, **fields) for mid, fields in data["models"].items()}


def model_spec(model_id: str, models: dict[str, ModelSpec] | None = None) -> ModelSpec:
    models = load_models() if models is None else models
    if model_id not in models:
        raise ValueError(f"unknown model {model_id!r}; configured: {', '.join(sorted(models))}")
    return models[model_id]
