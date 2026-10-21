"""The **Style Mask**: rewrite a public message, confirm its meaning, never broadcast a change.

``StyleMask.apply(item)`` decides the text every player sees:

1. Read the original's meaning (``check.MeaningCheck``): its Claims and whom it
   pushes to eject.
2. Ask the masker for a rewrite. Accept it if it is non-empty, fits the Talk
   Budget and has the same meaning.
3. Otherwise ask once more, telling the masker what was wrong (``retries``).
4. Otherwise fall back to ``neutralize(original)``, the minimal deterministic
   rewrite (formatting stripped, punctuation normalized), if it passes the same
   check.
5. Otherwise broadcast the original unchanged. This also happens when the
   original's meaning can't be read, since no rewrite could then be confirmed.

A message with a meaning change is never broadcast. Every candidate and its
check result is kept in ``MaskResult.attempts`` for the ground-truth log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from zantine.harness.provider import ProviderError
from zantine.stylemask.base import Masker, MaskError, MaskInput, NeutralMasker, neutralize
from zantine.stylemask.check import Meaning, MeaningCheck, MeaningUnavailable

RULE_VERSION = "style-mask-1"

# Outcomes, recorded as ``mask`` on the message's event.
REWRITTEN = "rewritten"  # the masker's rewrite passed the check
NEUTRALIZED = "neutralized"  # the masker failed; the deterministic fallback passed
ORIGINAL = "original"  # nothing could be confirmed; the original was sent unchanged
EMPTY = "empty"  # nothing was said


@dataclass(frozen=True)
class Attempt:
    source: str  # "masker" or "fallback"
    attempt: int
    text: str | None  # None when the masker returned nothing usable
    ok: bool
    problems: tuple[str, ...]
    meaning: Meaning | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "attempt": self.attempt,
            # Not "text": the replay viewer shows a speaker plus text as speech.
            "candidate": self.text,
            "ok": self.ok,
            "problems": list(self.problems),
            "meaning": self.meaning.to_dict() if self.meaning else None,
        }


@dataclass(frozen=True)
class MaskResult:
    original: str
    text: str  # what players see
    outcome: str
    expected: Meaning | None  # the original's meaning, if it could be read
    attempts: tuple[Attempt, ...] = field(default=())


class StyleMask:
    def __init__(self, masker: Masker, check: MeaningCheck, retries: int = 1) -> None:
        self.masker = masker
        self.check = check
        self.retries = retries

    @property
    def masker_id(self) -> str:
        return str(self.masker.info()["id"])

    def ledgers(self) -> list[Any]:
        """The Ledgers of the paid parts (masker, extractor, judge), if any."""
        parts = (self.masker, self.check.extractor, self.check.judge)
        return [p.ledger for p in parts if getattr(p, "ledger", None) is not None]

    def info(self) -> dict[str, Any]:
        return {
            "rule": RULE_VERSION,
            "masker": self.masker.info(),
            "retries": self.retries,
            "fallback": {"name": "neutralize", "version": NeutralMasker().info()["version"]},
            "check": self.check.info(),
        }

    def apply(self, item: MaskInput) -> MaskResult:
        original = item.text
        if not original.strip():
            return MaskResult(original, "", EMPTY, None)
        try:
            expected = self.check.meaning(item, original)
        except MeaningUnavailable as exc:
            problem = Attempt("original", 0, original, False, (f"original: {exc}",), None)
            return MaskResult(original, original, ORIGINAL, None, (problem,))

        attempts: list[Attempt] = []
        checked: dict[str, Attempt] = {}
        problem: str | None = None
        for n in range(1, self.retries + 2):
            try:
                candidate = self.masker.rewrite(item, problem)
            except (MaskError, ProviderError) as exc:
                if isinstance(exc, ProviderError) and exc.fatal:
                    raise
                attempts.append(Attempt("masker", n, None, False, (f"masker: {exc}",), None))
                problem = "your previous reply was unusable"
                continue
            result = self._check(item, expected, "masker", n, candidate, checked)
            attempts.append(result)
            if result.ok:
                return MaskResult(original, candidate, REWRITTEN, expected, tuple(attempts))
            problem = "; ".join(result.problems)

        fallback = neutralize(original, item.keep_words())
        result = self._check(item, expected, "fallback", 1, fallback, checked)
        attempts.append(result)
        if result.ok:
            return MaskResult(original, fallback, NEUTRALIZED, expected, tuple(attempts))
        return MaskResult(original, original, ORIGINAL, expected, tuple(attempts))

    def _check(
        self,
        item: MaskInput,
        expected: Meaning,
        source: str,
        n: int,
        text: Any,
        checked: dict[str, Attempt],
    ) -> Attempt:
        if not isinstance(text, str) or not text.strip():
            return Attempt(source, n, None, False, ("empty rewrite",), None)
        if len(text) > item.max_chars:
            problem = f"rewrite is {len(text)} characters; the limit is {item.max_chars}"
            return Attempt(source, n, text, False, (problem,), None)
        if text in checked:  # same text as an earlier candidate: same verdict, no new calls
            prev = checked[text]
            return Attempt(source, n, text, prev.ok, prev.problems, prev.meaning)
        try:
            found = self.check.meaning(item, text)
        except MeaningUnavailable as exc:
            out = Attempt(source, n, text, False, (str(exc),), None)
        else:
            problems = MeaningCheck.differences(expected, found)
            out = Attempt(source, n, text, not problems, tuple(problems), found)
        checked[text] = out
        return out


def dry_style_mask() -> StyleMask:
    """The free, deterministic Style Mask for dry runs and tests.

    ``neutralize`` as the masker, checked with the regex extractor and keyword
    judge. Neither is used for scoring; live runs pass a model masker and the
    scoring extractor and Jev instead (``stylemask.build_style_mask``).
    """
    from zantine.audit.extractors import PatternExtractor
    from zantine.stylemask.check import MaskKeywordJudge

    return StyleMask(NeutralMasker(), MeaningCheck(PatternExtractor(), MaskKeywordJudge()))
