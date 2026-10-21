"""Label Panel configuration: the three pinned judges, loaded from ``panel/judges.toml``.

``load_panel`` refuses a panel that breaks the independence rules
(``validate_panel``): exactly three judges, from three different families and
three different labs, none from a **Model Pool** lab, none sharing a family with
the extractor being validated and none from the judgment model's lab. Which
models sit on the panel is chosen in #19; the shipped entries are placeholders
that refuse live calls until they are pinned.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from zantine.audit.llm import ExtractorSpec, load_extractor_specs
from zantine.harness.pricing import ModelSpec, load_models

PANEL_SIZE = 3


class PanelConfigError(ValueError):
    """The panel breaks an independence rule or is incompletely pinned."""


@dataclass(frozen=True)
class JudgeSpec:
    id: str
    family: str
    lab: str
    base_url: str
    model: str  # model name sent to the endpoint
    revision: str  # weights revision the endpoint serves
    api_key_env: str
    # USD per 1M tokens.
    input: float
    output: float
    max_output_tokens: int
    status: str = "placeholder"
    price_valid_until: Any = None

    def endpoint(self) -> ExtractorSpec:
        """The same OpenAI-compatible endpoint shape the scoring extractor uses."""
        return ExtractorSpec(
            id=self.id,
            family=self.family,
            base_url=self.base_url,
            model=self.model,
            revision=self.revision,
            api_key_env=self.api_key_env,
            input=self.input,
            output=self.output,
            max_output_tokens=self.max_output_tokens,
            status=self.status,
            price_valid_until=self.price_valid_until,
        )

    def model_spec(self) -> ModelSpec:
        return self.endpoint().model_spec()

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "family": self.family,
            "lab": self.lab,
            "model": self.model,
            "revision": self.revision,
            "base_url": self.base_url,
            "status": self.status,
        }

    def check_pinned(self) -> None:
        """Refuse a judge that is still a placeholder or has no pinned revision."""
        if self.status == "placeholder":
            raise PanelConfigError(
                f"judge {self.id!r} is a placeholder; the panel is chosen in #19"
            )
        if not self.revision:
            raise PanelConfigError(f"judge {self.id!r} has no pinned revision in judges.toml")


@dataclass(frozen=True)
class PanelConfig:
    judges: tuple[JudgeSpec, ...]
    extractor: str  # id in audit/extractors.toml
    extractor_family: str
    pipeline_judge_lab: str
    min_planted: float = 0.95
    alpha_threshold: float = 0.67

    def describe(self) -> dict[str, Any]:
        return {
            "judges": [j.describe() for j in self.judges],
            "extractor": self.extractor,
            "extractor_family": self.extractor_family,
            "pipeline_judge_lab": self.pipeline_judge_lab,
            "min_planted": self.min_planted,
            "alpha_threshold": self.alpha_threshold,
        }


def validate_panel(
    judges: Iterable[JudgeSpec],
    *,
    pool_labs: Iterable[str],
    extractor_family: str,
    pipeline_judge_lab: str | None = None,
) -> None:
    """Raise ``PanelConfigError`` unless the judges are independent of the pool and pipeline."""
    judges = list(judges)
    problems: list[str] = []
    if len(judges) != PANEL_SIZE:
        problems.append(f"the panel needs exactly {PANEL_SIZE} judges, got {len(judges)}")
    pool = {lab.casefold() for lab in pool_labs}
    for what in ("family", "lab"):
        seen: dict[str, str] = {}
        for j in judges:
            key = getattr(j, what).strip().casefold()
            if not key:
                problems.append(f"judge {j.id!r} has no {what}")
            elif key in seen:
                problems.append(f"judges {seen[key]!r} and {j.id!r} share the {what} {key!r}")
            else:
                seen[key] = j.id
    for j in judges:
        if j.lab.strip().casefold() in pool:
            problems.append(f"judge {j.id!r} is from {j.lab}, a Model Pool lab")
        if j.family.strip().casefold() == extractor_family.strip().casefold():
            problems.append(f"judge {j.id!r} is from the extractor's family ({extractor_family})")
        if pipeline_judge_lab and j.lab.strip().casefold() == pipeline_judge_lab.casefold():
            problems.append(f"judge {j.id!r} is from the judgment model's lab ({j.lab})")
    if problems:
        raise PanelConfigError("; ".join(problems))


def load_panel(
    path: str | Path | None = None,
    *,
    models: Mapping[str, ModelSpec] | None = None,
    extractors: Mapping[str, ExtractorSpec] | None = None,
) -> PanelConfig:
    if path is None:
        text = resources.files("zantine.panel").joinpath("judges.toml").read_text("utf-8")
    else:
        text = Path(path).read_text("utf-8")
    data = tomllib.loads(text)
    meta = dict(data.get("panel", {}))
    judges = tuple(JudgeSpec(id=jid, **f) for jid, f in data.get("judges", {}).items())
    models = load_models() if models is None else models
    extractors = load_extractor_specs() if extractors is None else extractors
    extractor = meta.pop("extractor", None)
    if extractor not in extractors:
        raise PanelConfigError(
            f"[panel] extractor {extractor!r} is not in extractors.toml "
            f"(known: {', '.join(sorted(extractors))})"
        )
    family = extractors[extractor].family
    validate_panel(
        judges,
        pool_labs={m.lab for m in models.values()},
        extractor_family=family,
        pipeline_judge_lab=meta.get("pipeline_judge_lab"),
    )
    return PanelConfig(judges=judges, extractor=extractor, extractor_family=family, **meta)
