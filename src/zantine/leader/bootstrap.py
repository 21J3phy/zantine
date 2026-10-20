"""A minimal game-level cluster bootstrap, in pure Python.

Seats in the same game are correlated (``docs/sample-size.md``: DEFF), so every
interval resamples **whole games** with replacement, never Seats or
assignments. A statistic is a ratio of per-game sums, ``sum(num) / sum(den)``
(a mean is the ratio with ``den = 1`` per game), so one set of draw counts per
group of games serves every statistic of that group. Intervals are 95%
percentile intervals; a replicate whose denominator is 0 is left out.

#58 (``analysis/bootstrap.py``, numpy) implements the same estimator for the
v1 report and isn't on this branch's base yet; consolidate once both merge.
``Estimate.to_dict`` uses the same keys as #58's.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from zantine.rng import GameRng

DEFAULT_REPLICATES = 2000
DEFAULT_SEED = 0
LEVEL = 0.95


def draw_counts(n_games: int, replicates: int, rng: GameRng) -> list[list[int]]:
    """``replicates`` lists of how many times each of ``n_games`` games was drawn."""
    out = []
    for _ in range(replicates):
        counts = [0] * n_games
        for _ in range(n_games):
            counts[rng.randint(0, n_games - 1)] += 1
        out.append(counts)
    return out


@dataclass(frozen=True)
class Estimate:
    estimate: float | None
    low: float | None
    high: float | None
    n_games: int  # games contributing to the denominator
    n_units: float  # sum of the denominator (assignments, ballots, games, ...)
    replicates: int  # replicates in which the statistic was defined

    def to_dict(self) -> dict[str, Any]:
        def clean(x: float | None) -> float | None:
            return None if x is None or not math.isfinite(x) else round(x, 6)

        width = None if self.low is None or self.high is None else self.high - self.low
        return {
            "estimate": clean(self.estimate),
            "ci_low": clean(self.low),
            "ci_high": clean(self.high),
            "ci_width": clean(width),
            "n_games": self.n_games,
            "n_units": round(self.n_units, 6),
            "replicates": self.replicates,
        }


def ratio(num: Sequence[float], den: Sequence[float], draws: Sequence[Sequence[int]]) -> Estimate:
    """``sum(num) / sum(den)`` over games, with a percentile interval from ``draws``."""
    total = sum(den)
    point = sum(num) / total if total else None
    boot = []
    for counts in draws:
        d = sum(w * x for w, x in zip(counts, den, strict=True))
        if d:
            boot.append(sum(w * x for w, x in zip(counts, num, strict=True)) / d)
    low, high = percentile_interval(boot) if point is not None else (None, None)
    return Estimate(
        point,
        low,
        high,
        sum(x > 0 for x in den),
        float(total),
        len(boot) if point is not None else 0,
    )


def difference(
    num_a: Sequence[float],
    den_a: Sequence[float],
    num_b: Sequence[float],
    den_b: Sequence[float],
    draws: Sequence[Sequence[int]],
) -> Estimate:
    """Ratio A minus ratio B over the same games (paired: the same draws for both)."""
    ta, tb = sum(den_a), sum(den_b)
    point = sum(num_a) / ta - sum(num_b) / tb if ta and tb else None
    boot = []
    for counts in draws:
        da = sum(w * x for w, x in zip(counts, den_a, strict=True))
        db = sum(w * x for w, x in zip(counts, den_b, strict=True))
        if da and db:
            na = sum(w * x for w, x in zip(counts, num_a, strict=True))
            nb = sum(w * x for w, x in zip(counts, num_b, strict=True))
            boot.append(na / da - nb / db)
    low, high = percentile_interval(boot) if point is not None else (None, None)
    games = sum(a > 0 and b > 0 for a, b in zip(den_a, den_b, strict=True))
    return Estimate(
        point, low, high, games, float(min(ta, tb)), len(boot) if point is not None else 0
    )


def percentile_interval(values: Sequence[float]) -> tuple[float | None, float | None]:
    """The 95% percentile interval (linear interpolation, like numpy's default)."""
    if not values:
        return None, None
    xs = sorted(values)
    alpha = (1 - LEVEL) / 2
    return _quantile(xs, alpha), _quantile(xs, 1 - alpha)


def _quantile(xs: Sequence[float], q: float) -> float:
    pos = q * (len(xs) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)
