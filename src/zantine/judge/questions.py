"""Typed judgment questions, batches and answer validation.

A ``Batch`` is one judge request: a ``state`` (plain JSON data, where every
piece of player speech lives) and named ``Question``s evaluated against it, the
shape of a Jev ``POST /v1/systemone`` body. Question ``instructions`` and
``criteria`` are fixed text written here plus ids and engine-generated player
names, never player speech, so speech can only ever reach a judge as data.

Answers are never trusted: ``parse_answers`` checks every answer against its
question (type, option names, probabilities in [0, 1] summing to 1) and raises
``JudgmentError`` for anything else, so a judge coaxed into inventing an option
(say, ``"verdict": "true"``) is rejected rather than read.

Confidence: a Choice answer carries Jev's own ``confidence`` (how concentrated
its probabilities are). A Noul has only the probability of yes, ``p``; its
confidence here is ``|2p - 1|`` (0 at a coin flip, 1 when certain). An answer
below the run's threshold is flagged ``low_confidence``. Flagged answers still
count; every metric reports how many of its inputs were flagged.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

NOUL, CHOICE = "noul", "choice"
# Jev limits: a Choice takes 1 to 255 options.
MAX_OPTIONS = 255
_PROBABILITY_TOLERANCE = 1e-3


class JudgmentError(Exception):
    """A judge's reply is unusable (missing answers, wrong types, invented options)."""


@dataclass(frozen=True)
class Question:
    kind: str  # "noul" or "choice"
    instructions: str
    # Choice: option name -> description. Noul: optional {"true": ..., "false": ...}.
    criteria: Mapping[str, str] | None = None
    # Local metadata for offline judges and the report (ids only); never sent to Jev.
    about: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in (NOUL, CHOICE):
            raise ValueError(f"unknown question kind {self.kind!r}")
        if self.kind == CHOICE and not (self.criteria and len(self.criteria) <= MAX_OPTIONS):
            raise ValueError(f"a Choice needs 1 to {MAX_OPTIONS} options")

    @property
    def options(self) -> tuple[str, ...]:
        return tuple(self.criteria or ()) if self.kind == CHOICE else ()

    def to_request(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.kind, "instructions": self.instructions}
        if self.criteria is not None:
            out["criteria"] = dict(self.criteria)
        return out


@dataclass(frozen=True)
class Batch:
    id: str
    state: dict[str, Any]
    questions: dict[str, Question]
    round: int | None = None

    def to_request(self) -> dict[str, Any]:
        """The body of one Jev call, minus ``model``."""
        return {
            "state": self.state,
            "questions": {qid: q.to_request() for qid, q in self.questions.items()},
        }


@dataclass(frozen=True)
class Answer:
    kind: str
    # Noul: probability of yes. Choice: the chosen option name.
    value: float | str
    confidence: float
    probabilities: dict[str, float] | None
    low_confidence: bool

    @property
    def yes(self) -> bool:
        """For a Noul: is yes the more likely outcome?"""
        return self.kind == NOUL and float(self.value) >= 0.5

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "value": self.value,
            "confidence": self.confidence,
            "low_confidence": self.low_confidence,
        }
        if self.probabilities is not None:
            out["probabilities"] = self.probabilities
        return out


def parse_answers(batch: Batch, raw: Any, threshold: float) -> dict[str, Answer]:
    """Validate a judge's raw ``answers`` object against the batch's questions."""
    if not isinstance(raw, Mapping):
        raise JudgmentError("expected an object of answers keyed by question id")
    missing = sorted(set(batch.questions) - set(raw))
    if missing:
        raise JudgmentError(f"missing answers: {', '.join(missing)}")
    out = {}
    for qid, question in batch.questions.items():
        try:
            out[qid] = _parse_one(question, raw[qid], threshold)
        except JudgmentError as exc:
            raise JudgmentError(f"answer {qid!r}: {exc}") from exc
    return out


def _parse_one(question: Question, raw: Any, threshold: float) -> Answer:
    if not isinstance(raw, Mapping):
        raise JudgmentError("not an object")
    if raw.get("type", question.kind) != question.kind:
        raise JudgmentError(f"type {raw.get('type')!r}, expected {question.kind!r}")
    if question.kind == NOUL:
        p = _probability(raw.get("noul"), "noul")
        confidence = abs(2 * p - 1)
        return Answer(NOUL, p, round(confidence, 6), None, confidence < threshold)
    choice = raw.get("choice")
    if choice not in question.options:
        raise JudgmentError(f"choice {choice!r} is not an option")
    probs = raw.get("probabilities")
    if not isinstance(probs, Mapping) or set(probs) != set(question.options):
        raise JudgmentError("probabilities must cover exactly the options")
    probs = {k: _probability(probs[k], f"probabilities[{k!r}]") for k in question.options}
    if abs(sum(probs.values()) - 1) > _PROBABILITY_TOLERANCE:
        raise JudgmentError("probabilities do not sum to 1")
    if probs[choice] < max(probs.values()) - _PROBABILITY_TOLERANCE:
        raise JudgmentError(f"choice {choice!r} is not the most probable option")
    confidence = _probability(raw.get("confidence"), "confidence")
    return Answer(CHOICE, choice, confidence, probs, confidence < threshold)


def _probability(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise JudgmentError(f"{what} must be a number")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise JudgmentError(f"{what} must be between 0 and 1")
    return value
