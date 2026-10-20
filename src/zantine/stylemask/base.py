"""Maskers: rewrite one public message in a neutral style.

Every masker implements ``Masker``: ``info()`` (pinned identity) and
``rewrite(MaskInput, problem) -> str``. The engine never trusts the output:
``mask.StyleMask`` checks every rewrite for meaning before anyone sees it.

- ``LLMMasker`` (``stylemask/llm.py``): a pinned open-weight model behind an
  OpenAI-compatible endpoint. Paid; live-gated.
- ``NeutralMasker``: ``neutralize``, a deterministic rule-based normalizer. Free;
  the masker for dry runs and tests, and the fallback when a rewrite fails.
- ``ScriptedMasker``: rewrites computed by a function; for tests.
"""

from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


class MaskError(Exception):
    """A masker's reply is unusable (not JSON, no text, cut off)."""


@dataclass(frozen=True)
class MaskInput:
    """One public message to mask, with the public facts a masker or checker may use.

    ``earlier`` holds the messages before it on the same channel, as players saw
    them (``(speaker name, text)``), so the meaning check can resolve references
    like "I agree with Blue". Nothing here is ground truth.
    """

    channel: str  # "meeting" now; Room Chat later
    text: str
    speaker: int
    roster: dict[int, str]
    living: tuple[int, ...]
    rooms: tuple[str, ...]
    round: int
    meeting: int
    max_chars: int
    earlier: tuple[tuple[str, str], ...] = ()

    @property
    def speaker_name(self) -> str:
        return self.roster[self.speaker]

    def keep_words(self) -> frozenset[str]:
        """Names a rewrite must leave exactly as written."""
        return frozenset([*self.roster.values(), *self.rooms])


class Masker(ABC):
    @abstractmethod
    def info(self) -> dict[str, Any]:
        """Pinned identity: at least ``id``, ``model``, ``version`` and ``scoring``."""

    @abstractmethod
    def rewrite(self, item: MaskInput, problem: str | None = None) -> str:
        """The rewritten message. ``problem`` says why the previous rewrite was rejected.

        Raise ``MaskError`` for unusable output or ``harness.provider.ProviderError``
        when a call fails.
        """


NEUTRALIZE_VERSION = "neutralize-1"

_QUOTES = str.maketrans(
    {
        **dict.fromkeys("\u2018\u2019\u201a\u201b\u2032", "'"),
        **dict.fromkeys("\u201c\u201d\u201e\u201f\u2033\u00ab\u00bb", '"'),
        **dict.fromkeys("\u2010\u2011\u2212", "-"),
    }
)
# En and em dashes, spaced hyphens and double hyphens: all become " - ".
_DASHES = re.compile(r"\s*[\u2012-\u2015]+\s*|\s+-{1,3}\s+|(?<=\w)--+(?=\w)")
_FENCE = re.compile(r"```[^\n]*\n?|`")
_EMPHASIS = re.compile(r"(?<![\w*_~])(\*{1,3}|_{1,3}|~~)(?=\S)(.+?)(?<=\S)\1(?![\w*_~])")
_LINE_MARKUP = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+\u2022]\s+|\d{1,2}[.)]\s+)", re.M)
_ALL_CAPS = re.compile(r"\b[A-Z][A-Z']+\b")
_EMOTICON = re.compile(r"(?<!\S)[:;=]['-]?[()\[\]DPpOo3/\\|*]+(?!\S)|(?<!\S)<3+(?!\S)")
_LONE_I = re.compile(r"(?<![\w.'])i(?=(?:'(?:m|d|ll|ve|s))?\b)(?![.\w])")


def neutralize(text: str, keep: Iterable[str] = ()) -> str:
    """Strip formatting and normalize punctuation, keeping every word that carries meaning.

    Deterministic and meaning-preserving by construction: it only removes
    markup, emoji, emoticons and decoration, maps exclamations, semicolons,
    dashes and ellipses to plain punctuation, lowercases shouted words (``keep``
    names and rooms get their listed spelling; Task ids are untouched) and fixes
    sentence capitals. It never adds, drops or reorders words.
    """
    canonical = {k.casefold(): k for k in keep}
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    text = _EMOTICON.sub(" ", text)
    text = _FENCE.sub(" ", text)
    for _ in range(3):  # nested emphasis, e.g. ***x***
        text = _EMPHASIS.sub(lambda m: m.group(2), text)
    text = _LINE_MARKUP.sub("", text)
    # Each line is a sentence: end unterminated lines before joining them.
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln + ("." if ln[-1].isalnum() else "") for ln in lines if ln]
    text = " ".join(lines)
    # Emoji, symbols, private-use and invisible formatting characters.
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("So", "Sk", "Co", "Cf"))
    text = _DASHES.sub(" - ", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"[!?]*\?[!?]*", "?", text)
    text = re.sub(r"!+", ".", text)
    text = text.replace(";", ".")
    text = re.sub(r",{2,}", ",", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,?:)])", r"\1", text)
    text = re.sub(r"([(])\s+", r"\1", text)
    text = re.sub(r"([.?,:])(?:\s*[.,])+", r"\1", text)
    text = re.sub(r"^[\s.,:-]+", "", text)
    text = _ALL_CAPS.sub(lambda m: canonical.get(m.group(0).casefold(), m.group(0).lower()), text)
    text = _LONE_I.sub("I", text)
    text = re.sub(r"(^|[.?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    text = text.strip(" -")
    if text and (text[-1].isalnum() or text[-1] in "\"')"):
        text += "."
    return text


class NeutralMasker(Masker):
    """``neutralize`` as a masker: free and deterministic, for dry runs and tests."""

    def info(self) -> dict[str, Any]:
        return {
            "id": "neutral",
            "name": "neutralize",
            "model": None,
            "version": NEUTRALIZE_VERSION,
            "scoring": False,
        }

    def rewrite(self, item: MaskInput, problem: str | None = None) -> str:
        return neutralize(item.text, item.keep_words())


class ScriptedMasker(Masker):
    """Rewrites computed by ``fn(item, problem)``; for tests."""

    def __init__(self, fn: Callable[[MaskInput, str | None], str], id: str = "scripted") -> None:
        self.fn = fn
        self.id = id
        self.calls: list[tuple[MaskInput, str | None]] = []

    def info(self) -> dict[str, Any]:
        return {"id": self.id, "name": "scripted", "model": None, "version": "test"}

    def rewrite(self, item: MaskInput, problem: str | None = None) -> str:
        self.calls.append((item, problem))
        return self.fn(item, problem)
