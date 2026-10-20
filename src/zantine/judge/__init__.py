"""Typed judgments over Meeting speech, and the metrics built on them (issue #15).

A post-game step after the **Claim Audit** (``zantine audit --judge ...``):

1. ``batches``: build typed questions from the game record and the audit. Is a
   Claim asserted or hedged (Noul)? Was a **Lie** challenged before the vote
   (Noul)? Whom is a statement pushing to eject (Choice over the other living
   players, ``skip`` and ``no_one``)? Speech is passed only as quoted data.
2. A ``Judge`` answers each batch: ``JevJudge`` (TypeSafe's Jev; paid,
   live-gated) for scoring, ``KeywordJudge`` for free dry runs, ``ScriptedJudge``
   for tests. ``questions.parse_answers`` validates every answer and flags
   low-confidence ones against a configurable threshold.
3. ``report.run_judgments`` computes per-Seat **Floor Share**, **Vote Pull**,
   Suspicion, Lie Survival and Misdirection. Judgments never change a Claim
   verdict, a vote or an Ejection; those come only from the game record.
4. In v1.4 Leader games, ``leader`` adds ``instruction`` questions over the
   Leader's statements and checks **Compliance** and time to eject in code.
"""

from zantine.judge.judges import Judge, KeywordJudge, ScriptedJudge, choice, noul
from zantine.judge.questions import Answer, Batch, JudgmentError, Question, parse_answers
from zantine.judge.report import DEFAULT_THRESHOLD, ballots, run_judgments

__all__ = [
    "DEFAULT_THRESHOLD",
    "Answer",
    "Batch",
    "Judge",
    "JudgmentError",
    "KeywordJudge",
    "Question",
    "ScriptedJudge",
    "ballots",
    "choice",
    "noul",
    "parse_answers",
    "run_judgments",
]
