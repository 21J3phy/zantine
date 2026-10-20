"""The meaning check: does a rewrite say the same thing as the original?

Two parts, both reusing the post-game scoring tools on a single message:

- **Claims**: a ``ClaimExtractor`` (the Claim Audit's) reads the original and
  the rewrite. Their checkable Claims (``location``, ``together``, ``kill``,
  ``task_done``) must match as sets, compared on kind, subject, object, room,
  Round, Task, negation and the ``seen`` / ``throughout`` qualifiers. As in
  ``claims.resolve``, a Claim only counts if its quote is non-empty and appears
  in that version's text. ``other`` Claims are never checked by the audit, so
  they are not compared.
- **Accusation**: a ``Judge`` answers the judgments' ``push`` Choice ("whom does
  this message argue to eject?", over the other living players, ``skip`` and
  ``no_one``) for each version separately; the answers must be equal.

A message whose meaning can't be read (extractor or judge error) can't be
confirmed, so ``StyleMask`` treats it as a failed check. Fatal provider errors
and ``SpendCapReached`` propagate and stop the run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from zantine.audit.claims import ExtractionError, ExtractionInput, parse_payload
from zantine.audit.extractors import ClaimExtractor
from zantine.harness.provider import ProviderError
from zantine.judge.batches import NO_ONE, NOTE, SAFETY, SKIP
from zantine.judge.judges import Judge
from zantine.judge.questions import CHOICE, Batch, JudgmentError, Question, parse_answers
from zantine.stylemask.base import MaskInput

CHECK_VERSION = "meaning-2"
CHECKED_KINDS = ("location", "together", "kill", "task_done")
_SELF_WORDS = frozenset({"i", "me", "myself", "self"})

PUSH = (
    "Statement {sid} was said by {speaker}. Whom does statement {sid} argue the group should "
    "vote to eject? Use the statements in earlier only to resolve references such as "
    "'I agree with Blue'. Answer skip if it argues for skipping the vote, and no_one if it "
    "argues for no vote at all." + SAFETY
)

# kind, subject, object, room, round, task, negated, seen, throughout
ClaimKey = tuple[str, str | None, str | None, str | None, int | None, str | None, bool, bool, bool]
_KEY_FIELDS = (
    "kind",
    "subject",
    "object",
    "room",
    "round",
    "task",
    "negated",
    "seen",
    "throughout",
)


class MeaningUnavailable(Exception):
    """The meaning of one version could not be read (extractor or judge failure)."""


@dataclass(frozen=True)
class Meaning:
    claims: tuple[ClaimKey, ...]  # sorted, distinct
    push: str | None  # None when the check has no judge
    push_confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claims": [dict(zip(_KEY_FIELDS, c, strict=True)) for c in self.claims],
            "push": self.push,
            "push_confidence": self.push_confidence,
        }


def push_options(item: MaskInput) -> dict[str, str]:
    """The ``push`` Choice options, as in the judgments: other living players, skip, no_one."""
    names = [item.roster[s] for s in sorted(item.living) if s != item.speaker]
    if {SKIP, NO_ONE} & {n.casefold() for n in names}:
        raise ValueError("a player name collides with a push option")
    return {
        **{n: f"Eject {n}." for n in names},
        SKIP: "Vote to skip: eject no one at this Meeting.",
        NO_ONE: "Does not argue for any vote.",
    }


class MeaningCheck:
    def __init__(self, extractor: ClaimExtractor, judge: Judge | None) -> None:
        self.extractor = extractor
        self.judge = judge

    def info(self) -> dict[str, Any]:
        return {
            "version": CHECK_VERSION,
            "compared_kinds": list(CHECKED_KINDS),
            "extractor": self.extractor.info(),
            "judge": self.judge.info() if self.judge else None,
        }

    def meaning(self, item: MaskInput, text: str) -> Meaning:
        """What ``text`` claims and whom it pushes to eject, as if ``item``'s speaker said it."""
        claims = self._claims(item, text)
        if self.judge is None:
            return Meaning(claims, None)
        push, confidence = self._push(item, text)
        return Meaning(claims, push, confidence)

    @staticmethod
    def differences(expected: Meaning, found: Meaning) -> list[str]:
        """Why ``found`` doesn't mean the same as ``expected``; empty if it does."""
        out = []
        for c in sorted(set(expected.claims) - set(found.claims), key=repr):
            out.append(f"lost Claim {_show(c)}")
        for c in sorted(set(found.claims) - set(expected.claims), key=repr):
            out.append(f"added Claim {_show(c)}")
        if expected.push != found.push:
            out.append(f"pushes to eject {found.push!r} instead of {expected.push!r}")
        return out

    def _claims(self, item: MaskInput, text: str) -> tuple[ClaimKey, ...]:
        inp = ExtractionInput(
            text=text,
            speaker=item.speaker_name,
            players=tuple(item.roster[s] for s in sorted(item.roster)),
            rooms=item.rooms,
            meeting=item.meeting,
            round=item.round,
        )
        try:
            parsed = parse_payload(self.extractor.extract(inp))
        except ExtractionError as exc:
            raise MeaningUnavailable(f"extractor: {exc}") from exc
        except ProviderError as exc:
            if exc.fatal:
                raise
            raise MeaningUnavailable(f"extractor: {exc}") from exc
        said = _squash(text)
        keys = {
            _key(d, item.speaker_name)
            for d in parsed.drafts
            if d.kind in CHECKED_KINDS and _squash(d.quote) and _squash(d.quote) in said
        }
        return tuple(sorted(keys, key=repr))

    def _push(self, item: MaskInput, text: str) -> tuple[str, float]:
        assert self.judge is not None
        speaker = item.speaker_name
        batch = Batch(
            id=f"style_mask.{item.channel}",
            state={
                "note": NOTE,
                "meeting": item.meeting,
                "players": [item.roster[s] for s in sorted(item.living)],
                "earlier": [{"speaker": who, "text": said} for who, said in item.earlier],
                "statements": [{"id": "s", "speaker": speaker, "text": text}],
            },
            questions={
                "push_s": Question(
                    CHOICE,
                    PUSH.format(sid="s", speaker=speaker),
                    push_options(item),
                    about={"judgment": "push", "statement": "s"},
                )
            },
            round=item.round,
        )
        try:
            answer = parse_answers(batch, self.judge.judge(batch), threshold=0.0)["push_s"]
        except JudgmentError as exc:
            raise MeaningUnavailable(f"judge: {exc}") from exc
        except ProviderError as exc:
            if exc.fatal:
                raise
            raise MeaningUnavailable(f"judge: {exc}") from exc
        return str(answer.value), answer.confidence


def _key(d: Any, speaker: str) -> ClaimKey:
    def name(value: str | None) -> str | None:
        if value is None:
            return None
        key = value.casefold()
        return speaker.casefold() if key in _SELF_WORDS else key

    task = re.sub(r"[^0-9a-z]", "", d.task.casefold()) if d.task else None
    room = d.room.casefold() if d.room else None
    return (
        d.kind,
        name(d.subject),
        name(d.object),
        room,
        d.round,
        task,
        d.negated,
        d.seen,
        d.throughout,
    )


def _squash(text: str) -> str:
    return " ".join(text.casefold().split())


def _show(c: ClaimKey) -> str:
    kind, subject, obj, room, rnd, task, negated, seen, throughout = c
    parts = [kind, f"subject={subject}"]
    for label, value in (("object", obj), ("room", room), ("round", rnd), ("task", task)):
        if value is not None:
            parts.append(f"{label}={value}")
    for label, flag in (("negated", negated), ("seen", seen), ("throughout", throughout)):
        if flag:
            parts.append(label)
    return "(" + " ".join(parts) + ")"
