"""Claim extractors: turn one **Meeting** statement into raw ``{"claims": [...]}`` output.

Every extractor implements ``ClaimExtractor``. Its output goes through
``claims.parse_payload`` like any model's would, so a fake can stand in for the
real extractor anywhere (tests, dry runs, the Planted Claims report).

- ``LLMExtractor`` (``audit/llm.py``): the scoring extractor, a pinned
  open-weight model behind an OpenAI-compatible endpoint. Paid; live-gated.
- ``ScriptedExtractor``: returns canned payloads; for tests.
- ``PatternExtractor``: a few regular expressions that read scripted-bot
  speech. Free and deterministic, for dry runs; never used for scoring.

``info()`` is written into every audit so results always say which extractor
(model, version, prompt) produced them.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import Any

from zantine.audit.claims import CLAIM_SCHEMA_VERSION, ExtractionInput


class ClaimExtractor(ABC):
    @abstractmethod
    def info(self) -> dict[str, Any]:
        """Pinned identity: at least ``name``, ``model`` and ``version``."""

    @abstractmethod
    def extract(self, item: ExtractionInput) -> Any:
        """Raw output for one statement (validated by the caller).

        Raise ``claims.ExtractionError`` for unusable output or
        ``harness.provider.ProviderError`` when a call fails.
        """


class ScriptedExtractor(ClaimExtractor):
    """Canned output keyed by statement text, or computed by a function."""

    def __init__(
        self,
        responses: Mapping[str, Any] | Callable[[ExtractionInput], Any],
        name: str = "scripted",
    ) -> None:
        self.responses = responses
        self.name = name
        self.inputs: list[ExtractionInput] = []

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "model": None, "version": "test", "scoring": False}

    def extract(self, item: ExtractionInput) -> Any:
        self.inputs.append(item)
        if callable(self.responses):
            return self.responses(item)
        return self.responses.get(item.text, {"claims": []})


def claim(kind: str, subject: str | None, quote: str = "", **fields: Any) -> dict[str, Any]:
    """One raw claim item with every schema field (for scripted payloads)."""
    return {
        "kind": kind,
        "subject": subject,
        "object": fields.get("object"),
        "room": fields.get("room"),
        "round": fields.get("round"),
        "task": fields.get("task"),
        "negated": fields.get("negated", False),
        "seen": fields.get("seen", False),
        "throughout": fields.get("throughout", False),
        "quote": quote,
    }


PATTERN_VERSION = "pattern-2"


class PatternExtractor(ClaimExtractor):
    """Regex extraction of simple first-person sentences; for dry runs, not scoring.

    Understands "I saw X kill Y in R" (``seen``), "X killed Y", "I didn't kill X",
    "I/X was (not) in R", "I/X was with Y (in R)" and "I did/finished/completed (Task) T12",
    each optionally followed by "in round N"; "the whole time" after a location or
    together Claim sets ``throughout``.
    """

    def info(self) -> dict[str, Any]:
        return {
            "name": "pattern",
            "model": None,
            "version": PATTERN_VERSION,
            "claim_schema": CLAIM_SCHEMA_VERSION,
            "scoring": False,
        }

    def extract(self, item: ExtractionInput) -> dict[str, Any]:
        name = _alternation(item.players)
        room = _alternation(item.rooms)
        rnd = r"(?:\s+(?:in|during)\s+round\s+(?P<round>\d+))?"
        whole = r"(?P<throughout>\s+the\s+(?:whole|entire)\s+time)?"

        def who(m: re.Match[str]) -> str:
            return item.speaker if m["s"].casefold() == "i" else m["s"]

        patterns: list[tuple[str, Callable[[re.Match[str]], dict[str, Any]]]] = [
            (
                rf"\bI saw (?P<s>{name}) kill (?P<o>{name})(?: in (?:the )?(?P<room>{room}))?{rnd}",
                lambda m: self._make(m, "kill", m["s"], object=m["o"], seen=True),
            ),
            (
                rf"\b(?P<s>{name}) killed (?P<o>{name})(?: in (?:the )?(?P<room>{room}))?{rnd}",
                lambda m: self._make(m, "kill", m["s"], object=m["o"]),
            ),
            (
                rf"\bI (?:didn't|did not|never) kill (?P<o>{name}){rnd}",
                lambda m: self._make(m, "kill", item.speaker, object=m["o"], negated=True),
            ),
            (
                rf"\b(?P<s>I|{name}) was with (?P<o>{name})(?: in (?:the )?(?P<room>{room}))?"
                rf"{rnd}{whole}",
                lambda m: self._make(m, "together", who(m), object=m["o"]),
            ),
            (
                rf"\b(?P<s>I|{name}) (?P<neg>was not|wasn't|was never|was) in (?:the )?"
                rf"(?P<room>{room}){rnd}{whole}",
                lambda m: self._make(m, "location", who(m), negated=m["neg"].casefold() != "was"),
            ),
            (
                rf"\bI (?:did|finished|completed) (?:task |my task )?(?P<task>T\d+){rnd}",
                lambda m: self._make(m, "task_done", item.speaker, task=m["task"]),
            ),
        ]
        found: list[tuple[int, dict[str, Any]]] = []
        taken: list[tuple[int, int]] = []
        for pattern, build in patterns:
            for m in re.finditer(pattern, item.text, flags=re.IGNORECASE):
                if any(a < m.end() and m.start() < b for a, b in taken):
                    continue
                taken.append((m.start(), m.end()))
                found.append((m.start(), build(m)))
        return {"claims": [c for _, c in sorted(found, key=lambda x: x[0])]}

    @staticmethod
    def _make(m: re.Match[str], kind: str, subject: str, **fields: Any) -> dict[str, Any]:
        groups = m.groupdict()
        if groups.get("room"):
            fields["room"] = groups["room"]
        if groups.get("round"):
            fields["round"] = int(groups["round"])
        if groups.get("throughout"):
            fields["throughout"] = True
        return claim(kind, subject, m.group(0), **fields)


def _alternation(words: tuple[str, ...]) -> str:
    ordered = sorted(words, key=len, reverse=True)
    return "|".join(re.escape(w) for w in ordered) or r"(?!x)x"
