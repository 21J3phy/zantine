"""Style classifier report: can a source Model be told from its messages, before and after masking?

``zantine stylemask eval <transcripts or dirs>`` trains a character n-gram
classifier (multinomial Naive Bayes, 2- to 4-grams, Laplace smoothing, equal
class priors; pure Python) to predict which Model wrote each Meeting message,
(Meeting statements and Room Chat), once on the ``original`` text and once
on the masked ``text`` players saw.

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
- Significance: a **permutation test** of the whole pipeline. The null is "no
  transferable Model style": which Model sits in which Seat of a game doesn't
  change the text. Each permutation shuffles the Model labels among the Seats
  of every game (a Seat's messages keep one label, and each game keeps its
  lineup), re-runs the full cross-validation and recomputes both scores; the
  one-sided p-value is (1 + permutations scoring at least as well) /
  (1 + permutations). So a few games that agree by coincidence are not
  evidence: with two two-Seat games, perfect accuracy has p of about 0.5.
- Uncertainty: 95% CIs from a game cluster bootstrap (resample whole games and
  pool their out-of-fold predictions). They describe how much the score depends
  on which games were played, not significance, and are withheld below
  ``MIN_CI_GAMES`` games. Repeating messages within a game changes neither.

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
PERMUTATIONS = 200
MIN_CI_GAMES = 10


@dataclass(frozen=True)
class Sample:
    game: str
    seat: int
    label: str
    original: str
    masked: str | None  # None when the game ran without a Style Mask


def label_of(controller: dict[str, Any]) -> str:
    return str(controller.get("model") or controller.get("kind") or "unknown")


def samples(records: Sequence[dict[str, Any]], game: str) -> list[Sample]:
    """One ``Sample`` per spoken Meeting statement or delivered Room Chat message."""
    header = next(r for r in records if r["kind"] == "header")
    labels = {s["seat"]: label_of(s.get("controller") or {}) for s in header["seats"]}
    names = [s["name"] for s in header["seats"]]
    rooms = list(header.get("map", {}).get("rooms", []))
    out = []
    for r in records:
        if r["kind"] != "event" or r["type"] not in ("statement", "chat.message"):
            continue
        d = r["data"]
        original = d.get("original", d["text"])
        if not original.strip():
            continue
        masked = d["text"] if "original" in d else None
        out.append(
            Sample(
                game,
                d["speaker"],
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
        return self.fit_features([features(t) for t in texts], labels)

    def fit_features(self, feats: Sequence[Counter[str]], labels: Sequence[str]) -> NaiveBayes:
        counts: dict[str, Counter[str]] = defaultdict(Counter)
        for f, label in zip(feats, labels, strict=True):
            counts[label].update(f)
        self.counts = dict(counts)
        self.totals = {k: sum(c.values()) for k, c in self.counts.items()}
        self.vocab = set().union(*self.counts.values()) if self.counts else set()
        return self

    def predict(self, text: str) -> str:
        return self.predict_features(features(text))

    def predict_features(self, feats: Counter[str]) -> str:
        v = len(self.vocab) + 1
        best, best_score = "", -math.inf
        for label in sorted(self.counts):
            c, denom = self.counts[label], math.log(self.totals[label] + self.alpha * v)
            score = sum(n * (math.log(c.get(g, 0) + self.alpha) - denom) for g, n in feats.items())
            if score > best_score:
                best, best_score = label, score
        return best


def cross_validate(
    texts: Sequence[str],
    labels: Sequence[str],
    groups: Sequence[str],
    folds: int = 5,
    *,
    units: Sequence[Any] | None = None,
    permutations: int = PERMUTATIONS,
) -> dict[str, Any]:
    """Out-of-fold scores, permutation p-values and game-cluster CIs.

    ``groups`` are games; ``units`` (default: each message) are what carries one
    label within a game, normally its Seat. ``None`` scores with fewer than two
    Models or games.
    """
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
    feats = [features(t) for t in texts]
    predicted = _out_of_fold(feats, labels, fold, folds)
    correct = [p == y for p, y in zip(predicted, labels, strict=True)]
    acc, bal = sum(correct) / n, balanced_accuracy(labels, correct)

    # Permutation null: shuffle labels among the units of each game, re-run everything.
    unit_ids = list(range(n)) if units is None else list(units)
    members: dict[tuple[str, Any], list[int]] = defaultdict(list)
    for i, (g, u) in enumerate(zip(groups, unit_ids, strict=True)):
        members[(g, u)].append(i)
    by_game: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    for key in sorted(members, key=repr):
        by_game[key[0]].append(key)
    rng = random.Random(0)
    at_least_acc = at_least_bal = 0
    for _ in range(permutations):
        shuffled = list(labels)
        for game in games:
            keys = by_game[game]
            unit_labels = [labels[members[k][0]] for k in keys]
            rng.shuffle(unit_labels)
            for k, y in zip(keys, unit_labels, strict=True):
                for i in members[k]:
                    shuffled[i] = y
        pred = _out_of_fold(feats, shuffled, fold, folds)
        ok = [p == y for p, y in zip(pred, shuffled, strict=True)]
        at_least_acc += sum(ok) / n >= acc - 1e-12
        at_least_bal += balanced_accuracy(shuffled, ok) >= bal - 1e-12

    out = base | {
        "folds": folds,
        "split": "game",
        "permutations": permutations,
        "accuracy": acc,
        "chance_accuracy": max(base["models"].values()) / n,
        "p_value_vs_chance": (1 + at_least_acc) / (1 + permutations),
        "balanced_accuracy": bal,
        "chance_balanced_accuracy": 1 / len(classes),
        "p_value_balanced_vs_chance": (1 + at_least_bal) / (1 + permutations),
        "confusion": confusion(labels, predicted),
    }
    if len(games) >= MIN_CI_GAMES:
        out |= cluster_bootstrap(labels, correct, groups)
    else:
        out |= {
            "accuracy_ci95": None,
            "balanced_accuracy_ci95": None,
            "ci_note": f"CIs need {MIN_CI_GAMES}+ games",
        }
    return out


def _out_of_fold(
    feats: Sequence[Counter[str]], labels: Sequence[str], fold: Sequence[int], folds: int
) -> list[str]:
    n = len(feats)
    predicted = [""] * n
    for f in range(folds):
        train = [i for i in range(n) if fold[i] != f]
        test = [i for i in range(n) if fold[i] == f]
        if not test or not train:
            continue
        model = NaiveBayes().fit_features([feats[i] for i in train], [labels[i] for i in train])
        for i in test:
            predicted[i] = model.predict_features(feats[i])
    return predicted


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


def evaluate(
    paths: Iterable[str | Path], folds: int = 5, permutations: int = PERMUTATIONS
) -> dict[str, Any]:
    from zantine.audit.report import load_transcript
    from zantine.seats.bots import scripted_bot_versions

    files = transcript_paths(paths)
    data: list[Sample] = []
    bots: dict[tuple[int | None, ...], str] = {}
    for path in files:
        records = load_transcript(path)
        # Games played by different scripted bots talk differently; pooling them would
        # blend two policies into one score (a header from before versions is None).
        versions = tuple(scripted_bot_versions(records[0].get("seats") or []))
        if versions:
            bots.setdefault(versions, str(path))
        data += samples(records, str(path))
    if len(bots) > 1:
        seen = "; ".join(f"{list(v)} in {p}" for v, p in bots.items())
        raise ValueError(
            f"transcripts were played by different scripted-bot versions ({seen}); "
            "evaluate each version separately"
        )
    masked = [s for s in data if s.masked is not None]

    def score(items: list[Sample], masked_text: bool) -> dict[str, Any]:
        texts = [(s.masked if masked_text else s.original) or "" for s in items]
        return cross_validate(
            texts,
            [s.label for s in items],
            [s.game for s in items],
            folds,
            units=[s.seat for s in items],
            permutations=permutations,
        )

    return {
        "classifier": {
            "name": CLASSIFIER_VERSION,
            "ngrams": list(NGRAMS),
            "folds": folds,
            "bootstrap": BOOTSTRAP,
            "permutations": permutations,
            "min_ci_games": MIN_CI_GAMES,
        },
        "transcripts": len(files),
        "masked_games": len({s.game for s in masked}),
        # Every message, and only the masked games' messages before and after masking.
        "original_all": score(data, False),
        "original": score(masked, False),
        "masked": score(masked, True),
    }
