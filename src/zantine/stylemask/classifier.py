"""Style classifier report: can a source Model be told from its messages, before and after masking?

``zantine stylemask eval <transcripts or dirs>`` trains a character n-gram
classifier (multinomial Naive Bayes, 2- to 4-grams, Laplace smoothing, equal
class priors; pure Python) to predict which Model wrote each Meeting message,
once on the ``original`` text and once on the masked ``text`` players saw.

- Labels: the Seat's ``controller`` in the transcript header (the live
  ``model`` id, else the controller ``kind``). A transcript without a Style
  Mask contributes to the original-text score only.
- Player and room names are replaced by placeholders first, so the classifier
  scores writing style, not who talks about whom.
- Cross-validation: folds by game only, so a game's messages are never in both
  training and test. With fewer games than ``folds`` there is one fold per game;
  with fewer than two games there is no score.
- Scores: **balanced accuracy** (mean per-Model recall; chance is 1/K for K
  Models, so an 8-vs-2 Lopsided Lobby can't inflate it) and plain accuracy
  (chance: the majority-Model share).
- Inference treats the **game** as the independent unit, since a game's
  messages share Seats, content and a classifier: 95% CIs come from a game
  cluster bootstrap (resample whole games, recompute both scores from their
  pooled out-of-fold predictions), and each one-sided p-value is the share of
  bootstrap replicates at or below chance. Repeating messages within a game
  changes neither the scores nor their uncertainty.

Dry runs with scripted bots are trivial (every bot writes the same templates,
so there is no style to find and both scores sit at chance); the real number
comes from pilot transcripts with live Models.
"""

from __future__ import annotations

import json
import math
import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CLASSIFIER_VERSION = "char-nb-1"
NGRAMS = (2, 4)
BOOTSTRAP = 1000


@dataclass(frozen=True)
class Sample:
    game: str
    label: str
    original: str
    masked: str | None  # None when the game ran without a Style Mask


def label_of(controller: dict[str, Any]) -> str:
    return str(controller.get("model") or controller.get("kind") or "unknown")


def samples(records: Sequence[dict[str, Any]], game: str) -> list[Sample]:
    """One ``Sample`` per spoken Meeting statement of one transcript."""
    header = next(r for r in records if r["kind"] == "header")
    labels = {s["seat"]: label_of(s.get("controller") or {}) for s in header["seats"]}
    names = [s["name"] for s in header["seats"]]
    rooms = list(header.get("map", {}).get("rooms", []))
    out = []
    for r in records:
        if r["kind"] != "event" or r["type"] != "statement":
            continue
        d = r["data"]
        original = d.get("original", d["text"])
        if not original.strip():
            continue
        masked = d["text"] if "original" in d else None
        out.append(
            Sample(
                game,
                labels[d["speaker"]],
                placeholder(original, names, rooms),
                None if masked is None else placeholder(masked, names, rooms),
            )
        )
    return out


def placeholder(text: str, names: Iterable[str], rooms: Iterable[str]) -> str:
    """Replace player names with ``@`` and room names with ``#`` (longest first, any case)."""
    for words, mark in ((rooms, "#"), (names, "@")):
        for w in sorted(words, key=len, reverse=True):
            text = re.sub(rf"(?<!\w){re.escape(w)}(?!\w)", mark, text, flags=re.IGNORECASE)
    return text


def features(text: str) -> Counter[str]:
    padded = f" {' '.join(text.split())} "
    lo, hi = NGRAMS
    return Counter(padded[i : i + n] for n in range(lo, hi + 1) for i in range(len(padded) - n + 1))


class NaiveBayes:
    """Multinomial Naive Bayes over character n-grams with equal class priors."""

    def __init__(self, alpha: float = 1.0) -> None:
        self.alpha = alpha
        self.counts: dict[str, Counter[str]] = {}
        self.totals: dict[str, int] = {}
        self.vocab: set[str] = set()

    def fit(self, texts: Sequence[str], labels: Sequence[str]) -> NaiveBayes:
        counts: dict[str, Counter[str]] = defaultdict(Counter)
        for text, label in zip(texts, labels, strict=True):
            counts[label].update(features(text))
        self.counts = dict(counts)
        self.totals = {k: sum(c.values()) for k, c in self.counts.items()}
        self.vocab = set().union(*self.counts.values()) if self.counts else set()
        return self

    def predict(self, text: str) -> str:
        feats = features(text)
        v = len(self.vocab) + 1
        best, best_score = "", -math.inf
        for label in sorted(self.counts):
            c, denom = self.counts[label], math.log(self.totals[label] + self.alpha * v)
            score = sum(n * (math.log(c.get(g, 0) + self.alpha) - denom) for g, n in feats.items())
            if score > best_score:
                best, best_score = label, score
        return best


def cross_validate(
    texts: Sequence[str], labels: Sequence[str], groups: Sequence[str], folds: int = 5
) -> dict[str, Any]:
    """Out-of-fold predictions and scores; ``None`` scores with fewer than two Models."""
    n = len(texts)
    classes = sorted(set(labels))
    games = sorted(set(groups))
    base: dict[str, Any] = {
        "messages": n,
        "games": len(games),
        "models": {c: labels.count(c) for c in classes},
    }
    if n == 0 or len(classes) < 2:
        return base | {"accuracy": None, "balanced_accuracy": None, "note": "needs 2+ Models"}
    if len(games) < 2:
        return base | {"accuracy": None, "balanced_accuracy": None, "note": "needs 2+ games"}
    folds = min(folds, len(games))
    fold_of_game = {g: i % folds for i, g in enumerate(games)}
    fold = [fold_of_game[g] for g in groups]
    predicted = [""] * n
    for f in range(folds):
        train = [i for i in range(n) if fold[i] != f]
        test = [i for i in range(n) if fold[i] == f]
        if not test or not train:
            continue
        model = NaiveBayes().fit([texts[i] for i in train], [labels[i] for i in train])
        for i in test:
            predicted[i] = model.predict(texts[i])
    correct = [p == y for p, y in zip(predicted, labels, strict=True)]
    majority = max(base["models"].values()) / n
    chance_bal = 1 / len(classes)
    boot = cluster_bootstrap(labels, correct, groups, chance_acc=majority, chance_bal=chance_bal)
    return base | {
        "folds": folds,
        "split": "game",
        "accuracy": sum(correct) / n,
        "accuracy_ci95": boot["accuracy_ci95"],
        "chance_accuracy": majority,
        "p_value_vs_chance": boot["p_accuracy"],
        "balanced_accuracy": balanced_accuracy(labels, correct),
        "balanced_accuracy_ci95": boot["balanced_accuracy_ci95"],
        "chance_balanced_accuracy": chance_bal,
        "p_value_balanced_vs_chance": boot["p_balanced"],
        "confusion": confusion(labels, predicted),
    }


def balanced_accuracy(labels: Sequence[str], correct: Sequence[bool]) -> float:
    hits: dict[str, list[bool]] = defaultdict(list)
    for y, ok in zip(labels, correct, strict=True):
        hits[y].append(ok)
    return sum(sum(v) / len(v) for v in hits.values()) / len(hits)


def cluster_bootstrap(
    labels: Sequence[str],
    correct: Sequence[bool],
    groups: Sequence[str],
    *,
    chance_acc: float,
    chance_bal: float,
    replicates: int = BOOTSTRAP,
) -> dict[str, Any]:
    """Game cluster bootstrap of accuracy and balanced accuracy (seeded, so reproducible).

    Each replicate draws as many games as there are, with replacement, and pools
    their (label -> hits, messages) counts; balanced accuracy averages recall over
    the Models present in that replicate. Only ratios of per-game counts enter,
    so duplicating every message of a game leaves every replicate unchanged.
    """
    per_game: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for y, ok, g in zip(labels, correct, groups, strict=True):
        cell = per_game[g][y]
        cell[0] += ok
        cell[1] += 1
    games = sorted(per_game)
    rng = random.Random(0)
    accs, bals = [], []
    for _ in range(replicates):
        hits: dict[str, int] = defaultdict(int)
        total: dict[str, int] = defaultdict(int)
        for _ in games:
            for y, (h, m) in per_game[games[rng.randrange(len(games))]].items():
                hits[y] += h
                total[y] += m
        accs.append(sum(hits.values()) / sum(total.values()))
        bals.append(sum(hits[y] / total[y] for y in total) / len(total))
    return {
        "accuracy_ci95": _percentiles(accs),
        "balanced_accuracy_ci95": _percentiles(bals),
        "p_accuracy": (sum(a <= chance_acc for a in accs) + 1) / (replicates + 1),
        "p_balanced": (sum(b <= chance_bal for b in bals) + 1) / (replicates + 1),
    }


def _percentiles(values: list[float]) -> list[float]:
    ordered = sorted(values)
    n = len(ordered)
    return [ordered[int(0.025 * n)], ordered[min(n - 1, int(0.975 * n))]]


def confusion(labels: Sequence[str], predicted: Sequence[str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {y: {} for y in sorted(set(labels))}
    for y, p in zip(labels, predicted, strict=True):
        out[y][p] = out[y].get(p, 0) + 1
    return {y: dict(sorted(row.items())) for y, row in out.items()}


def transcript_paths(paths: Iterable[str | Path]) -> list[Path]:
    """Transcript files from files and directories (``*.jsonl`` whose first record is a header)."""
    out: list[Path] = []
    for p in map(Path, paths):
        candidates = sorted(p.rglob("*.jsonl")) if p.is_dir() else [p]
        for c in candidates:
            with c.open(encoding="utf-8") as fh:
                first = fh.readline()
            try:
                if json.loads(first).get("kind") == "header":
                    out.append(c)
            except (json.JSONDecodeError, AttributeError):
                continue
    return out


def evaluate(paths: Iterable[str | Path], folds: int = 5) -> dict[str, Any]:
    from zantine.audit.report import load_transcript

    files = transcript_paths(paths)
    data: list[Sample] = []
    for path in files:
        data += samples(load_transcript(path), str(path))
    masked = [s for s in data if s.masked is not None]

    def score(items: list[Sample], masked_text: bool) -> dict[str, Any]:
        texts = [(s.masked if masked_text else s.original) or "" for s in items]
        return cross_validate(texts, [s.label for s in items], [s.game for s in items], folds)

    return {
        "classifier": {
            "name": CLASSIFIER_VERSION,
            "ngrams": list(NGRAMS),
            "folds": folds,
            "bootstrap": BOOTSTRAP,
        },
        "transcripts": len(files),
        "masked_games": len({s.game for s in masked}),
        # Every message, and only the masked games' messages before and after masking.
        "original_all": score(data, False),
        "original": score(masked, False),
        "masked": score(masked, True),
    }
