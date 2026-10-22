"""Moment finder: candidate single-game **Moments** from transcripts and Claim Audits.

A **Moment** is a striking event from one game, shared as a story about that
game and never as a statistic. Pipeline:

1. ``facts.build_facts``: the game's facts from its transcript (``header``,
   ``event`` and ``result`` records only) plus its Claim Audit report, if any.
2. ``detectors``: one registered detector per Moment type; types whose inputs
   a game lacks (no audit, no Crisis module, no Leader conditions) are skipped.
3. ``finder.rank``: a documented score (type weight + edge + lateness), never a
   model judgment. Every Moment deep-links into the replay viewer.
4. ``captions``: fixed ``In one game (seed N), ...`` templates checked for
   generalizing words.

``zantine moments <transcript-or-run-dir>`` runs it from the CLI.
"""

from zantine.moments.captions import GENERALIZING, PREFIX, caption, generalizing_words
from zantine.moments.detectors import DETECTORS, Detector, Moment, detector
from zantine.moments.facts import GameFacts, build_facts
from zantine.moments.finder import find_in_path, find_moments, link, rank, to_markdown

__all__ = [
    "DETECTORS",
    "GENERALIZING",
    "PREFIX",
    "Detector",
    "GameFacts",
    "Moment",
    "build_facts",
    "caption",
    "detector",
    "find_in_path",
    "find_moments",
    "generalizing_words",
    "link",
    "rank",
    "to_markdown",
]
