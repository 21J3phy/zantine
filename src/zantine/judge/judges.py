"""Judges: answer a ``Batch`` of typed questions with Jev-shaped raw answers.

Every judge implements ``Judge``. Its output goes through
``questions.parse_answers`` like Jev's would, so a fake can stand in for Jev
anywhere (tests, dry runs).

- ``JevJudge`` (``judge/jev.py``): the scoring judge, TypeSafe's Jev. Paid; live-gated.
- ``ScriptedJudge``: answers computed by a function; for tests.
- ``KeywordJudge``: a few keyword rules that read scripted-bot speech. Free and
  deterministic, for dry runs; never used for scoring.

``info()`` is written into every report so results always say which judge
(model, version, prompt) produced them.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import Any

from zantine.judge.questions import CHOICE, NOUL, Batch, Question


class Judge(ABC):
    @abstractmethod
    def info(self) -> dict[str, Any]:
        """Pinned identity: at least ``name``, ``model``, ``version`` and ``scoring``."""

    @abstractmethod
    def judge(self, batch: Batch) -> Any:
        """Raw ``answers`` for one batch, keyed by question id (validated by the caller).

        Raise ``questions.JudgmentError`` for unusable output or
        ``harness.provider.ProviderError`` when a call fails.
        """


def noul(p: float) -> dict[str, Any]:
    """One raw Noul answer."""
    return {"type": NOUL, "noul": p}


def choice(options: tuple[str, ...], pick: str, p: float = 0.9) -> dict[str, Any]:
    """One raw Choice answer: ``p`` on ``pick``, the rest spread evenly."""
    rest = (1 - p) / (len(options) - 1) if len(options) > 1 else 0.0
    probs = {o: (p if o == pick else rest) for o in options}
    if len(options) == 1:
        probs[pick] = 1.0
    return {"type": CHOICE, "choice": pick, "confidence": p, "probabilities": probs}


class ScriptedJudge(Judge):
    """Answers each question with ``answer(batch, question_id, question)``."""

    def __init__(
        self,
        answer: Callable[[Batch, str, Question], Any],
        name: str = "scripted",
    ) -> None:
        self.answer = answer
        self.name = name
        self.batches: list[Batch] = []

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "model": None, "version": "test", "scoring": False}

    def judge(self, batch: Batch) -> dict[str, Any]:
        self.batches.append(batch)
        return {qid: self.answer(batch, qid, q) for qid, q in batch.questions.items()}


KEYWORD_VERSION = "keyword-1"
_HEDGES = re.compile(
    r"\b(i think|i guess|maybe|probably|perhaps|might|not sure|i believe|possibly)\b", re.I
)
_CHALLENGES = re.compile(
    r"\b(lie|lies|lying|liar|not true|that's false|wrong|didn't|did not|wasn't|was not)\b", re.I
)
_PUSH = r"\b(?:accuse|vote(?: for| out)?|eject|suspect)\s+(?P<name>{names})\b"


class KeywordJudge(Judge):
    """Keyword rules for scripted-bot speech; for dry runs, not scoring.

    - push: the last player named after "accuse", "vote (for/out)", "eject" or
      "suspect"; else ``skip`` if the statement says "skip"; else ``no_one``.
    - asserted: no for hedges ("I think", "maybe", ...), else yes.
    - challenged: yes if a later statement by another player calls something a
      lie, says "not true" / "wrong" / a denial, or accuses the speaker.
    """

    def info(self) -> dict[str, Any]:
        return {"name": "keyword", "model": None, "version": KEYWORD_VERSION, "scoring": False}

    def judge(self, batch: Batch) -> dict[str, Any]:
        return {qid: self._answer(batch, q) for qid, q in batch.questions.items()}

    def _answer(self, batch: Batch, q: Question) -> dict[str, Any]:
        kind = q.about["judgment"]
        state = batch.state
        if kind == "push":
            text = _statement(state, q.about["statement"])["text"]
            players = [o for o in q.options if o not in ("skip", "no_one")]
            names = "|".join(re.escape(n) for n in sorted(players, key=len, reverse=True))
            found = list(re.finditer(_PUSH.format(names=names or r"(?!x)x"), text, re.I))
            if found:
                wanted = found[-1]["name"].casefold()
                pick = next(o for o in players if o.casefold() == wanted)
            elif re.search(r"\bskip\b", text, re.I):
                pick = "skip"
            else:
                pick = "no_one"
            return choice(q.options, pick)
        if kind == "asserted":
            quote = next(c for c in state["claims"] if c["id"] == q.about["claim"])["quote"]
            return noul(0.1 if _HEDGES.search(quote) else 0.9)
        if kind == "challenged":
            speaker = state["speaker"]
            accuse = re.compile(_PUSH.format(names=re.escape(speaker)), re.I)
            hit = any(
                _CHALLENGES.search(s["text"]) or accuse.search(s["text"])
                for s in state["later_statements"]
            )
            return noul(0.9 if hit else 0.1)
        raise ValueError(f"unknown judgment {kind!r}")


def _statement(state: Mapping[str, Any], sid: str) -> Mapping[str, Any]:
    return next(s for s in state["statements"] if s["id"] == sid)
