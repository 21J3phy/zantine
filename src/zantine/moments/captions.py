"""Moment captions: fixed templates that always speak about one game.

Every caption starts ``In one game (seed N), ...`` and is filled from a
template. Templates are checked when a detector registers them, and again on
every use, against ``GENERALIZING``: words that would turn a story about one
game into a claim about models or games in general (``models``, ``always``,
``usually``, ``tend to``, percentages, ...). Player names and numbers from the
game are the only things filled in.
"""

from __future__ import annotations

import re
import string
from collections.abc import Iterable
from typing import Any

PREFIX = "In one game"

GENERALIZING = re.compile(
    r"\b(?:models?|llms?|ais?|always|never|usually|often|typically|generally|in general"
    r"|tends?\s+to|tended\s+to|most|many|majority|every\s+game|games|on\s+average|average"
    r"|rates?|likely|frequently|consistently|commonly|regularly|routinely|percent|per\s*cent"
    r"|statistics?|statistically|overall|across)\b"
    r"|%",
    re.IGNORECASE,
)


class GeneralizingCaption(ValueError):
    """A caption template would generalize beyond the single game."""


def generalizing_words(text: str) -> list[str]:
    return [m.group(0) for m in GENERALIZING.finditer(text)]


def check_template(template: str) -> str:
    bad = generalizing_words(template)
    if bad:
        raise GeneralizingCaption(f"caption template generalizes ({', '.join(bad)}): {template!r}")
    if any(
        name and not name.isidentifier() for _, name, _, _ in string.Formatter().parse(template)
    ):
        raise GeneralizingCaption(f"caption template fields must be plain names: {template!r}")
    return template


def caption(seed: int | None, template: str, **fields: Any) -> str:
    """``In one game (seed N), <template filled in>``."""
    check_template(template)
    where = f"{PREFIX} (seed {seed})" if seed is not None else PREFIX
    return f"{where}, {template.format(**fields)}"


def join(items: Iterable[str]) -> str:
    """``A``, ``A and B``, ``A, B and C``."""
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"
