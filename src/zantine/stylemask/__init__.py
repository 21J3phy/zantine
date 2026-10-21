"""The **Style Mask** condition (``GameConfig.style_mask``).

A fixed model rewrites every public message in a neutral style before any
player sees it, removing writing-style cues to which Model is speaking. The
engine calls ``Game.public_message`` for each message (Meeting statements now,
Room Chat later); with the mask on it runs ``StyleMask.apply``.

- ``base``: ``MaskInput``, the ``Masker`` interface, ``neutralize`` (the
  deterministic rule-based rewrite) and ``NeutralMasker`` / ``ScriptedMasker``.
- ``llm``: ``LLMMasker``, the pinned open-weight model (``maskers.toml``),
  live-gated and metered by the ``Ledger`` with ``purpose="style_mask"``.
- ``check``: ``MeaningCheck``, the same Claims and the same ``push`` target.
- ``mask``: ``StyleMask``, the retry-then-fallback rule that never broadcasts a
  meaning change.
- ``classifier``: the style classifier report (``zantine stylemask eval``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from zantine.stylemask.base import (
    Masker,
    MaskError,
    MaskInput,
    NeutralMasker,
    ScriptedMasker,
    neutralize,
)
from zantine.stylemask.check import Meaning, MeaningCheck, MeaningUnavailable
from zantine.stylemask.mask import MaskResult, StyleMask, dry_style_mask

if TYPE_CHECKING:
    from zantine.harness.ledger import Ledger

__all__ = [
    "MaskError",
    "MaskInput",
    "MaskResult",
    "Masker",
    "Meaning",
    "MeaningCheck",
    "MeaningUnavailable",
    "NeutralMasker",
    "ScriptedMasker",
    "StyleMask",
    "build_style_mask",
    "dry_style_mask",
    "neutralize",
]


def build_style_mask(
    masker: str = "neutral",
    extractor: str = "pattern",
    judge: str = "keyword",
    *,
    ledger: Ledger | None = None,
    live: bool = False,
    maskers_path: str | Path | None = None,
    extractors_path: str | Path | None = None,
) -> StyleMask:
    """A Style Mask from CLI-style names.

    ``masker``: ``neutral`` (free) or an id from ``stylemask/maskers.toml``.
    ``extractor``: ``pattern`` (free) or an id from ``audit/extractors.toml``.
    ``judge``: ``keyword`` (free) or ``jev``. Paid parts need ``live``,
    ``ZANTINE_LIVE=1`` and a ``ledger`` (the run's, so one spend cap covers them).
    """
    from zantine.audit.extractors import ClaimExtractor, PatternExtractor
    from zantine.audit.llm import LLMExtractor, load_extractor_specs
    from zantine.judge.jev import JevJudge
    from zantine.judge.judges import Judge
    from zantine.stylemask.check import MaskKeywordJudge
    from zantine.stylemask.llm import LLMMasker, load_masker_specs

    def need_ledger() -> Ledger:
        if ledger is None:
            raise ValueError("a paid Style Mask part needs the run's Ledger")
        return ledger

    m: Masker
    if masker == "neutral":
        m = NeutralMasker()
    else:
        specs = load_masker_specs(maskers_path)
        if masker not in specs:
            raise ValueError(f"unknown masker {masker!r}; known: neutral, {', '.join(specs)}")
        m = LLMMasker.from_env(specs[masker], need_ledger(), live=live)
    x: ClaimExtractor
    if extractor == "pattern":
        x = PatternExtractor()
    else:
        xspecs = load_extractor_specs(extractors_path)
        if extractor not in xspecs:
            raise ValueError(
                f"unknown extractor {extractor!r}; known: pattern, {', '.join(xspecs)}"
            )
        x = LLMExtractor.from_env(xspecs[extractor], need_ledger(), live=live)
    j: Judge
    if judge == "keyword":
        j = MaskKeywordJudge()
    elif judge == "jev":
        j = JevJudge.from_env(need_ledger(), live=live)
    else:
        raise ValueError(f"unknown judge {judge!r}; known: keyword, jev")
    return StyleMask(m, MeaningCheck(x, j))
