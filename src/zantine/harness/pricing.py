"""Model IDs, effort settings and prices, loaded from ``models.toml``.

``cost(usage)`` turns a normalized ``Usage`` into dollars; ``worst_case_cost``
bounds a call before it is made, so the spend cap can refuse it.

Effort is a neutral level (``low`` / ``medium`` / ``high``) chosen per run;
``efforts`` maps each level to the lab's own setting, which adapters send as
``effort_setting``. ``at_effort(level)`` gives the same Model at another level
(the effort sweep, #26); a level the Model's ``efforts`` lacks is refused, never
mapped to the nearest one, so a graph never labels one setting as another. A
Seat spec can name a level per Model (``live:<model>?effort=high``,
``split_effort``). A ``long_context`` tier re-prices a whole call when its
prompt is longer than the tier's threshold, as xAI and Google bill.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass, field, replace
from importlib import resources
from pathlib import Path
from typing import Any

from zantine.harness.provider import Usage

PER_TOKEN = 1 / 1_000_000
# Conservative bytes-per-token for estimating input before a call (real text is ~4).
_BYTES_PER_TOKEN = 3
EFFORT_LEVELS = ("low", "medium", "high")


@dataclass(frozen=True)
class PriceTier:
    """Prices (USD per 1M tokens) for calls whose prompt is over ``above_input_tokens``."""

    above_input_tokens: int
    input: float
    cached_input: float
    cache_write: float
    output: float


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
    # Neutral effort level -> the lab's own setting. Empty means the level is sent as-is.
    efforts: dict[str, str] = field(default_factory=dict, hash=False)
    long_context: PriceTier | None = None
    # Analysis only (graphs join a Sibling to its Flagship); never sent to a provider.
    tier: str | None = None
    flagship: str | None = None

    def __post_init__(self) -> None:
        if self.efforts and self.effort not in self.efforts:
            known = ", ".join(self.efforts)
            raise ValueError(
                f"{self.id}: effort {self.effort!r} is not one of {known} "
                "(a level the lab lacks is refused, not mapped to the nearest one)"
            )

    @property
    def effort_setting(self) -> str:
        """The lab's own value for this run's effort level, as sent on the wire."""
        return self.efforts.get(self.effort, self.effort)

    def at_effort(self, level: str) -> ModelSpec:
        """The same Model at another effort level (checked against ``efforts``)."""
        return replace(self, effort=level)

    def _tier(self, prompt_tokens: int) -> ModelSpec | PriceTier:
        lc = self.long_context
        return lc if lc is not None and prompt_tokens > lc.above_input_tokens else self

    def cost(self, usage: Usage) -> float:
        prompt = usage.input_tokens + usage.cached_input_tokens + usage.cache_write_tokens
        p = self._tier(prompt)
        return PER_TOKEN * (
            usage.input_tokens * p.input
            + usage.cached_input_tokens * p.cached_input
            + usage.cache_write_tokens * p.cache_write
            + usage.output_tokens * p.output
        )

    def worst_case_cost(self, request_bytes: int) -> float:
        """Upper estimate for one call: all input at the dearest input rate, full output cap."""
        input_tokens = -(-request_bytes // _BYTES_PER_TOKEN)
        p = self._tier(input_tokens)
        top_input = max(p.input, p.cached_input, p.cache_write)
        return PER_TOKEN * (input_tokens * top_input + self.max_output_tokens * p.output)

    def check_price_current(self, today: dt.date | None = None) -> None:
        today = today or dt.date.today()
        if self.price_valid_until is not None and today > self.price_valid_until:
            raise ValueError(
                f"price for {self.id} expired on {self.price_valid_until}; update models.toml"
            )

    def describe(self) -> dict[str, Any]:
        return {
            "model": self.id,
            "provider": self.provider,
            "effort": self.effort,
            "effort_setting": self.effort_setting,
        }


def split_effort(arg: str) -> tuple[str, str | None]:
    """``"<model>"`` or ``"<model>?effort=<level>"`` -> (model, level or None).

    The level must be one of ``EFFORT_LEVELS``; whether the Model's lab offers it is
    checked by ``ModelSpec.at_effort``.
    """
    model, sep, query = arg.partition("?")
    if not sep:
        return arg, None
    key, eq, level = query.partition("=")
    if key != "effort" or not eq:
        raise ValueError(f"{arg!r}: the only Seat option is ?effort=<level>")
    if level not in EFFORT_LEVELS:
        raise ValueError(f"{arg!r}: effort must be one of {', '.join(EFFORT_LEVELS)}")
    if not model:
        raise ValueError(f"{arg!r}: no model before ?effort=")
    return model, level


def _spec(model_id: str, fields: dict[str, Any]) -> ModelSpec:
    fields = dict(fields)
    if "long_context" in fields:
        fields["long_context"] = PriceTier(**fields["long_context"])
    return ModelSpec(id=model_id, **fields)


def load_models(path: str | Path | None = None) -> dict[str, ModelSpec]:
    """Every configured Model, by API ID. Defaults to the packaged ``models.toml``."""
    if path is None:
        text = resources.files("zantine.harness").joinpath("models.toml").read_text("utf-8")
    else:
        text = Path(path).read_text("utf-8")
    data = tomllib.loads(text)
    return {mid: _spec(mid, fields) for mid, fields in data["models"].items()}


def model_spec(model_id: str, models: dict[str, ModelSpec] | None = None) -> ModelSpec:
    models = load_models() if models is None else models
    if model_id not in models:
        raise ValueError(f"unknown model {model_id!r}; configured: {', '.join(sorted(models))}")
    return models[model_id]
