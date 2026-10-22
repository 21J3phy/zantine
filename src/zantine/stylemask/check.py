"""The meaning check: does a rewrite say the same thing as the original?

Each version (original and rewrite) is read separately, as if its speaker had
said it, and the two readings must agree on everything below. Anything the
check can't read fails closed: that version's meaning is unavailable, so a
rewrite can't be confirmed (``StyleMask`` then retries, falls back, or sends the
original).

- **Claims** (Claim Audit extractor). The checkable Claims (``location``,
  ``together``, ``kill``, ``task_done``) must match as sets, on kind, subject,
  object, room, Round, Task, negation and the ``seen`` / ``throughout``
  qualifiers. The reading is unavailable if the extractor's reply had any
  rejected (malformed) item, or a checkable Claim whose quote is empty or isn't
  in that version's text: an item that couldn't be read or grounded does not
  show the message has no such Claim. ``other`` Claims are never checked by the
  audit, so they are not compared.
- **Judgments** (one judge batch per version):
  - ``push`` (Choice, as in the judgments): whom does the message argue to
    eject (another living player, ``skip``, ``no_one``)?
  - ``stance`` (Choice) toward every player, the speaker ("I am a Crewmate" vs
    "I am an Impostor") and dead players included: ``accuses``, ``suspects``
    (hedged suspicion), ``defends`` or ``none``. Catches a reversed confession,
    secondary accusation or defense when the vote target is unchanged.
  - ``asserted`` (Noul, as in the judgments) for every Claim: stated as fact,
    or hedged? Catches added or removed hedging.
- **Same content** (one paired judge question, asked only once everything
  else agrees): does the rewrite say exactly what the original says, including
  instructions, plans and their order, requests and questions? This covers
  content outside the Claim schema and the stances.
- **Anchors** (plain code): the same player names, rooms, Task ids and numbers
  mentioned, in the same order ("Storage, then Electrical"), the same counts
  of role words (Impostor, Crewmate) and negations (not, never, n't, ...), and
  hedging present in both or neither. A conservative backstop for edits the
  judge might miss; ``neutralize`` never changes any of them.

Fatal provider errors and ``SpendCapReached`` propagate and stop the run.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from zantine.audit.claims import ClaimDraft, ExtractionError, ExtractionInput, parse_payload
from zantine.audit.extractors import ClaimExtractor
from zantine.harness.provider import ProviderError
from zantine.judge.batches import (
    ASSERTED,
    ASSERTED_CRITERIA,
    MAX_QUESTIONS,
    NO_ONE,
    NOTE,
    SAFETY,
    SKIP,
)
from zantine.judge.judges import Judge, KeywordJudge, choice, noul
from zantine.judge.questions import CHOICE, NOUL, Batch, JudgmentError, Question, parse_answers
from zantine.stylemask.base import MaskInput

CHECK_VERSION = "meaning-5"
CHECKED_KINDS = ("location", "together", "kill", "task_done")
_SELF_WORDS = frozenset({"i", "me", "myself", "self"})

PUSH = (
    "Statement {sid} was said by {speaker}. Whom does statement {sid} argue the group should "
    "vote to eject? Use the statements in earlier only to resolve references such as "
    "'I agree with Blue'. Answer skip if it argues for skipping the vote, and no_one if it "
    "argues for no vote at all." + SAFETY
)
STANCE = (
    "Statement {sid} was said by {speaker}. What stance does statement {sid} take toward "
    "{name}? Use the statements in earlier only to resolve references." + SAFETY
)
SAME_CONTENT = (
    "Statements a and b are two versions of one message said by {speaker}; b is meant to "
    "be a rewording of a in a plainer style. Does b say exactly what a says: the same facts, "
    "accusations, defenses, instructions, plans and their order, requests, questions and "
    "certainty, adding and leaving out nothing? Differences only in wording, punctuation "
    "and formatting don't matter. Use the statements in earlier only to resolve references."
    + SAFETY
)
SAME_CONTENT_CRITERIA = {
    "true": "The same content; only the wording or style differs.",
    "false": "Some content differs: added, left out, reordered, reversed, or more or less certain.",
}
STANCE_SELF = (
    "Statement {sid} was said by {speaker}. What stance does statement {sid} take toward "
    "{speaker}, the speaker? First-person statements ('I am a Crewmate', 'I did it', 'vote "
    "me out') are about {speaker}. Use the statements in earlier only to resolve references."
    + SAFETY
)
ACCUSES, SUSPECTS, DEFENDS, NONE = "accuses", "suspects", "defends", "none"


def stance_options(name: str) -> dict[str, str]:
    return {
        ACCUSES: f"Says or argues that {name} is an Impostor, lied, did something wrong, "
        "or should be ejected.",
        SUSPECTS: f"Voices a hedged suspicion or doubt about {name} without asserting it.",
        DEFENDS: f"Says or argues that {name} is innocent or trustworthy, vouches for {name}, "
        f"or argues against ejecting {name}.",
        NONE: f"Takes no stance toward {name}.",
    }


def prompt_hash() -> str:
    """Pins the question templates the meaning check asks."""
    blob = json.dumps(
        [
            NOTE,
            PUSH,
            STANCE,
            STANCE_SELF,
            stance_options("X"),
            ASSERTED,
            ASSERTED_CRITERIA,
            SAME_CONTENT,
            SAME_CONTENT_CRITERIA,
        ]
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


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
    """The meaning of one version could not be read completely (extractor or judge)."""


@dataclass(frozen=True)
class Meaning:
    claims: tuple[ClaimKey, ...]  # sorted, distinct
    anchors: dict[str, Any]
    push: str | None = None  # the judge's readings; None when the check has no judge
    stances: tuple[tuple[str, str], ...] = ()  # (player name, stance), by name
    asserted: tuple[tuple[ClaimKey, bool], ...] = ()  # sorted, distinct
    confidence: dict[str, float] = field(default_factory=dict)  # question id -> confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "claims": [dict(zip(_KEY_FIELDS, c, strict=True)) for c in self.claims],
            "anchors": self.anchors,
            "push": self.push,
            "stances": dict(self.stances),
            "asserted": [
                {**dict(zip(_KEY_FIELDS, c, strict=True)), "asserted": a} for c, a in self.asserted
            ],
            "confidence": self.confidence,
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
            "prompt_sha256": prompt_hash(),
            "compared_kinds": list(CHECKED_KINDS),
            "extractor": self.extractor.info(),
            "judge": self.judge.info() if self.judge else None,
        }

    def meaning(self, item: MaskInput, text: str) -> Meaning:
        """Everything compared for ``text``, as if ``item``'s speaker said it."""
        drafts = self._drafts(item, text)
        claims = tuple(sorted({_key(d, item.speaker_name) for d in drafts}, key=repr))
        anchors = _anchors(text, item)
        if self.judge is None:
            return Meaning(claims, anchors)
        return self._judged(item, text, drafts, claims, anchors)

    def same_content(self, item: MaskInput, original: str, rewrite: str) -> list[str]:
        """The judge's paired check that ``rewrite`` says what ``original`` says.

        Covers what no other comparison represents (instructions, plans, requests,
        questions). Empty when it agrees or there is no judge; raises
        ``MeaningUnavailable`` when the judge's answer can't be read.
        """
        if self.judge is None:
            return []
        speaker = item.speaker_name
        batch = Batch(
            id=f"style_mask.{item.channel}.same",
            state={
                "note": NOTE,
                "meeting": item.meeting,
                "earlier": [{"speaker": who, "text": said} for who, said in item.earlier],
                "statements": [
                    {"id": "a", "speaker": speaker, "text": original},
                    {"id": "b", "speaker": speaker, "text": rewrite},
                ],
            },
            questions={
                "same_content": Question(
                    NOUL,
                    SAME_CONTENT.format(speaker=speaker),
                    SAME_CONTENT_CRITERIA,
                    about={"judgment": "same_content", "original": "a", "rewrite": "b"},
                )
            },
            round=item.round,
        )
        try:
            answer = parse_answers(batch, self.judge.judge(batch), threshold=0.0)["same_content"]
        except JudgmentError as exc:
            raise MeaningUnavailable(f"judge: {exc}") from exc
        except ProviderError as exc:
            if exc.fatal:
                raise
            raise MeaningUnavailable(f"judge: {exc}") from exc
        if answer.yes:
            return []
        return [f"the judge finds different content (p={float(answer.value):.2f})"]

    @staticmethod
    def differences(expected: Meaning, found: Meaning) -> list[str]:
        """Why ``found`` doesn't mean the same as ``expected``; empty if it does."""
        out = []
        for c in sorted(set(expected.claims) - set(found.claims), key=repr):
            out.append(f"lost Claim {_show(c)}")
        for c in sorted(set(found.claims) - set(expected.claims), key=repr):
            out.append(f"added Claim {_show(c)}")
        was, now = _asserted_by_key(expected), _asserted_by_key(found)
        for c in sorted(set(was) & set(now), key=repr):
            if was[c] != now[c]:
                out.append(
                    f"Claim {_show(c)} is {_strength(now[c])} instead of {_strength(was[c])}"
                )
        if expected.push != found.push:
            out.append(f"pushes to eject {found.push!r} instead of {expected.push!r}")
        before, after = dict(expected.stances), dict(found.stances)
        for name in sorted(set(before) | set(after)):
            if before.get(name) != after.get(name):
                out.append(f"{after.get(name)} {name} instead of {before.get(name)}")
        for key in sorted(set(expected.anchors) | set(found.anchors)):
            a, b = expected.anchors.get(key), found.anchors.get(key)
            if a != b:
                out.append(f"{key} {b!r} instead of {a!r}")
        return out

    def _drafts(self, item: MaskInput, text: str) -> list[ClaimDraft]:
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
        if parsed.rejected:
            raise MeaningUnavailable(f"extractor: unreadable items: {'; '.join(parsed.rejected)}")
        said = _squash(text)
        drafts = [d for d in parsed.drafts if d.kind in CHECKED_KINDS]
        for d in drafts:
            quote = _squash(d.quote)
            if not quote or quote not in said:
                raise MeaningUnavailable(f"extractor: Claim quote {d.quote!r} is not in the text")
        return drafts

    def _judged(
        self,
        item: MaskInput,
        text: str,
        drafts: list[ClaimDraft],
        claims: tuple[ClaimKey, ...],
        anchors: dict[str, Any],
    ) -> Meaning:
        assert self.judge is not None
        speaker = item.speaker_name
        # Every player, the speaker and the dead included: a statement can confess,
        # deny, or accuse someone who is no longer alive.
        everyone = [item.roster[s] for s in sorted(item.roster)]
        state = {
            "note": NOTE,
            "meeting": item.meeting,
            "players": [item.roster[s] for s in sorted(item.living)],
            "earlier": [{"speaker": who, "text": said} for who, said in item.earlier],
            "statements": [{"id": "s", "speaker": speaker, "text": text}],
            "claims": [
                {"id": f"c{i}", "quote": d.quote, "meaning": _draft_meaning(d)}
                for i, d in enumerate(drafts)
            ],
        }
        questions: dict[str, Question] = {
            "push_s": Question(
                CHOICE,
                PUSH.format(sid="s", speaker=speaker),
                push_options(item),
                about={"judgment": "push", "statement": "s"},
            )
        }
        for n, name in enumerate(everyone):
            template = STANCE_SELF if name == speaker else STANCE
            questions[f"stance_{n}"] = Question(
                CHOICE,
                template.format(sid="s", speaker=speaker, name=name),
                stance_options(name),
                about={
                    "judgment": "stance",
                    "statement": "s",
                    "player": name,
                    "self": name == speaker,
                },
            )
        for i in range(len(drafts)):
            questions[f"asserted_c{i}"] = Question(
                NOUL,
                ASSERTED.format(cid=f"c{i}", sid="s", speaker=speaker),
                ASSERTED_CRITERIA,
                about={"judgment": "asserted", "claim": f"c{i}"},
            )
        answers = {}
        items = list(questions.items())
        for k in range(0, len(items), MAX_QUESTIONS):
            batch = Batch(
                id=f"style_mask.{item.channel}.{k // MAX_QUESTIONS}",
                state=state,
                questions=dict(items[k : k + MAX_QUESTIONS]),
                round=item.round,
            )
            try:
                answers |= parse_answers(batch, self.judge.judge(batch), threshold=0.0)
            except JudgmentError as exc:
                raise MeaningUnavailable(f"judge: {exc}") from exc
            except ProviderError as exc:
                if exc.fatal:
                    raise
                raise MeaningUnavailable(f"judge: {exc}") from exc
        stances = tuple(
            (name, str(answers[f"stance_{n}"].value)) for n, name in enumerate(everyone)
        )
        asserted = {(_key(d, speaker), answers[f"asserted_c{i}"].yes) for i, d in enumerate(drafts)}
        return Meaning(
            claims,
            anchors,
            push=str(answers["push_s"].value),
            stances=tuple(sorted(stances)),
            asserted=tuple(sorted(asserted, key=repr)),
            confidence={qid: a.confidence for qid, a in sorted(answers.items())},
        )


# Anchors -------------------------------------------------------------------------------

_NEGATION = re.compile(r"\b(?:not|no|never|nobody|none|nothing|neither|nor|cannot)\b|n't\b", re.I)
_HEDGE = re.compile(
    r"\b(?:i think|i guess|i believe|i feel|maybe|probably|perhaps|possibly|might|may|could|"
    r"likely|unlikely|not sure|unsure|seems?|seemed|apparently|kind of|sort of)\b",
    re.I,
)
_TASK = re.compile(r"\bT\d+\b", re.I)
_NUMBER = re.compile(r"\b\d+\b")
_ROLES = {
    "impostor": re.compile(r"\bimpostors?\b", re.I),
    "crewmate": re.compile(r"\bcrewmates?\b", re.I),
}


def _anchors(text: str, item: MaskInput) -> dict[str, Any]:
    folded = text.replace("\u2019", "'")

    def mentioned(words: Iterable[str]) -> list[str]:
        return sorted(
            w
            for w in {w.casefold() for w in words}
            if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", folded, re.I)
        )

    return {
        # Mentions in the order they're said ("Storage, then Electrical").
        "order": _mention_order(folded, item),
        "players": mentioned(item.roster.values()),
        "rooms": mentioned(item.rooms),
        "tasks": sorted({t.upper() for t in _TASK.findall(folded)}),
        # A total order: "1" and "01" are distinct but numerically equal.
        "numbers": sorted(set(_NUMBER.findall(_TASK.sub(" ", folded))), key=lambda v: (int(v), v)),
        # Role words ("I am a Crewmate" vs "I am an Impostor"), by count.
        "roles": {role: len(rx.findall(folded)) for role, rx in _ROLES.items()},
        "negations": len(_NEGATION.findall(folded)),
        "hedged": bool(_HEDGE.search(folded)),
    }


def _mention_order(text: str, item: MaskInput) -> list[str]:
    """Players, rooms, Task ids and numbers in order of appearance (longest match wins)."""
    spans: list[tuple[int, int, str]] = []
    for kind, words in (("player", item.roster.values()), ("room", item.rooms)):
        for w in {w.casefold() for w in words}:
            for m in re.finditer(rf"(?<!\w){re.escape(w)}(?!\w)", text, re.I):
                spans.append((m.start(), m.end(), f"{kind}:{w}"))
    for m in _TASK.finditer(text):
        spans.append((m.start(), m.end(), f"task:{m.group(0).upper()}"))
    for m in _NUMBER.finditer(_TASK.sub(lambda t: " " * len(t.group(0)), text)):
        spans.append((m.start(), m.end(), f"number:{m.group(0)}"))
    out, end = [], -1
    for start, stop, label in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]), s[2])):
        if start >= end:
            out.append(label)
            end = stop
    return out


# Claims --------------------------------------------------------------------------------


def _key(d: ClaimDraft, speaker: str) -> ClaimKey:
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


def _draft_meaning(d: ClaimDraft) -> dict[str, Any]:
    return {
        "kind": d.kind,
        "subject": d.subject,
        "object": d.object,
        "room": d.room,
        "round": d.round,
        "task": d.task,
        "negated": d.negated,
        "seen": d.seen,
        "throughout": d.throughout,
    }


def _asserted_by_key(m: Meaning) -> dict[ClaimKey, frozenset[bool]]:
    out: dict[ClaimKey, set[bool]] = {}
    for key, value in m.asserted:
        out.setdefault(key, set()).add(value)
    return {k: frozenset(v) for k, v in out.items()}


def _strength(values: frozenset[bool]) -> str:
    return "/".join(sorted("asserted" if v else "hedged" for v in values))


def _words(text: str) -> list[str]:
    """The word sequence of a text, ignoring case, punctuation and formatting."""
    return re.findall(r"[0-9a-z]+", text.replace("\u2019", "'").casefold())


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


# The dry-run judge ----------------------------------------------------------------------

STANCE_KEYWORD_VERSION = "keyword-1+stance-2+same-1"


class MaskKeywordJudge(KeywordJudge):
    """``KeywordJudge`` plus keyword rules for ``stance``; for dry runs, not scoring.

    ``stance`` toward a player named N (also "I" when N is the speaker):
    ``accuses`` for "accuse/vote (for/out)/eject N", "N is/was (an/the) Impostor",
    "N killed/kill/lied/is lying"; else ``defends`` for "N is/was not (an/the)
    Impostor", "N is/was (...) innocent/safe/clear(ed)/trustworthy", "N is a
    Crewmate", "N didn't/never kill", "trust/vouch for/defend N", "N was with me";
    else ``suspects`` for "suspect/doubt N", "N is/was/seems/looks (...)
    sus/suspicious/strange/weird/shady"; else ``none``. ``same_content``: yes when
    both versions have the same word sequence, ignoring case and punctuation.
    """

    def info(self) -> dict[str, Any]:
        return {
            "name": "keyword",
            "model": None,
            "version": STANCE_KEYWORD_VERSION,
            "scoring": False,
        }

    def _answer(self, batch: Batch, q: Question) -> dict[str, Any]:
        if q.about.get("judgment") == "same_content":
            texts = {s["id"]: s["text"] for s in batch.state["statements"]}
            a, b = texts[q.about["original"]], texts[q.about["rewrite"]]
            return noul(0.9 if _words(a) == _words(b) else 0.1)
        if q.about.get("judgment") != "stance":
            return super()._answer(batch, q)
        text = next(s for s in batch.state["statements"] if s["id"] == q.about["statement"])["text"]
        n = re.escape(q.about["player"])
        # The speaker is also "I"; ``be`` covers "I am" as well as "N is".
        who = rf"(?:I|{n})" if q.about.get("self") else n
        be = r"(?:am|'m|is|was)"
        rules = [
            (ACCUSES, rf"\b(?:accuse|vote(?: for| out)?|eject)\s+{n}\b"),
            (ACCUSES, rf"\b{who}\s+{be}\s+(?:an?\s+|the\s+)?impostor\b"),
            (ACCUSES, rf"\b{who}\s+(?:killed|kill|kills|lied|is lying|was lying)\b"),
            (DEFENDS, rf"\b{who}\s+{be}\s+not\s+(?:an?\s+|the\s+)?impostor\b"),
            (
                DEFENDS,
                rf"\b{who}\s+{be}\s+(?:\w+\s+){{0,2}}(?:innocent|safe|clear|cleared|trustworthy)",
            ),
            (DEFENDS, rf"\b{who}\s+{be}\s+(?:an?\s+)?crewmate\b"),
            (DEFENDS, rf"\b{who}\s+(?:didn't|did not|never)\s+kill"),
            (DEFENDS, rf"\b(?:trust|vouch for|defend)\s+{n}\b"),
            (DEFENDS, rf"\b{n}\s+was\s+with\s+me\b"),
            (SUSPECTS, rf"\b(?:suspect|doubt)\s+{n}\b"),
            (
                SUSPECTS,
                rf"\b{who}\s+(?:is|was|seems|looks)\s+(?:\w+\s+){{0,2}}"
                r"(?:sus|suspicious|strange|weird|shady)",
            ),
        ]
        # First match wins, so an accusation outranks a defense or a suspicion.
        pick = next((st for st, pattern in rules if re.search(pattern, text, re.I)), NONE)
        return choice(q.options, pick)
