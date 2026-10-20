"""Game-level cluster bootstrap.

Seats in the same game are correlated (``docs/sample-size.md``: DEFF), so every
interval resamples **whole games** with replacement, never Seats. Resampling is
stratified by game kind (Balanced Lobby, Benign Match, Mirror Match): each
replicate keeps as many games of each kind as the data has, so every replicate
contains both the lobbies and the Benign Matches that Sabotage Damage compares.

A replicate is a vector of counts ``w[g]`` (how many times game ``g`` was drawn),
so a statistic that is a ratio of per-game sums is ``(w @ num) / (w @ den)`` and
all replicates are one matrix product. Intervals are 95% percentile intervals.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

DEFAULT_REPLICATES = 2000
DEFAULT_SEED = 0
LEVEL = 0.95


def resample_weights(strata: Sequence[str], replicates: int, seed: int) -> np.ndarray:
    """``replicates x len(strata)`` draw counts; each stratum is resampled to its own size."""
    rng = np.random.default_rng(seed)
    labels = np.asarray(strata, dtype=object)
    weights = np.zeros((replicates, len(labels)))
    for stratum in sorted(set(strata)):
        idx = np.flatnonzero(labels == stratum)
        probs = np.full(len(idx), 1.0 / len(idx))
        weights[:, idx] = rng.multinomial(len(idx), probs, size=replicates)
    return weights


@dataclass(frozen=True)
class Estimate:
    """A point estimate with its 95% cluster-bootstrap interval (NaN when undefined)."""

    estimate: float
    low: float
    high: float
    n_games: int  # games contributing to the estimate
    n_units: float  # sum of the denominator (Seats, votes, games, ...)
    replicates: int  # replicates in which the statistic was defined

    @property
    def width(self) -> float:
        return self.high - self.low

    def to_dict(self) -> dict[str, float | int | None]:
        def clean(x: float) -> float | None:
            return None if not np.isfinite(x) else round(float(x), 6)

        return {
            "estimate": clean(self.estimate),
            "ci_low": clean(self.low),
            "ci_high": clean(self.high),
            "ci_width": clean(self.width),
            "n_games": self.n_games,
            "n_units": round(float(self.n_units), 6),
            "replicates": self.replicates,
        }


def percentile_interval(boot: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-column 95% percentile interval over finite replicates, and their count."""
    alpha = (1 - LEVEL) / 2
    finite = np.isfinite(boot)
    n = finite.sum(axis=0)
    low = np.full(boot.shape[1], np.nan)
    high = np.full(boot.shape[1], np.nan)
    for j in np.flatnonzero(n > 0):
        col = boot[finite[:, j], j]
        low[j], high[j] = np.quantile(col, [alpha, 1 - alpha])
    return low, high, n


def ratio_estimates(num: np.ndarray, den: np.ndarray, weights: np.ndarray) -> list[Estimate]:
    """Per-column ``sum(num) / sum(den)`` with cluster-bootstrap intervals.

    ``num`` and ``den`` are ``games x columns``; a game outside a column's scope
    has ``den = 0`` there. A replicate whose denominator is 0 is undefined and
    left out (its count is reported in ``Estimate.replicates``).
    """
    num = np.asarray(num, dtype=float)
    den = np.asarray(den, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        point = num.sum(axis=0) / den.sum(axis=0)
        boot = (weights @ num) / (weights @ den)
    boot[~np.isfinite(boot)] = np.nan
    low, high, n = percentile_interval(boot)
    games = (den > 0).sum(axis=0)
    out = []
    for j in range(num.shape[1]):
        defined = den[:, j].sum() > 0
        out.append(
            Estimate(
                estimate=float(point[j]) if defined else np.nan,
                low=float(low[j]) if defined else np.nan,
                high=float(high[j]) if defined else np.nan,
                n_games=int(games[j]),
                n_units=float(den[:, j].sum()),
                replicates=int(n[j]) if defined else 0,
            )
        )
    return out


def ratio_difference(
    num: np.ndarray, den: np.ndarray, weights: np.ndarray, a: int, b: int
) -> Estimate:
    """Column ``a``'s pooled ratio minus column ``b``'s, with the same game-level
    replicates for both (so the interval keeps their within-game correlation)."""
    num = np.asarray(num, dtype=float)[:, [a, b]]
    den = np.asarray(den, dtype=float)[:, [a, b]]
    with np.errstate(divide="ignore", invalid="ignore"):
        point = num.sum(axis=0) / den.sum(axis=0)
        boot = (weights @ num) / (weights @ den)
    diff = (boot[:, 0] - boot[:, 1])[:, None]
    diff[~np.isfinite(diff)] = np.nan
    low, high, n = percentile_interval(diff)
    defined = bool((den.sum(axis=0) > 0).all())
    return Estimate(
        estimate=float(point[0] - point[1]) if defined else np.nan,
        low=float(low[0]) if defined else np.nan,
        high=float(high[0]) if defined else np.nan,
        n_games=int(((den > 0).any(axis=1)).sum()),
        n_units=float(den.sum()),
        replicates=int(n[0]) if defined else 0,
    )
