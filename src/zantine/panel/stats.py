"""Agreement statistics for the Label Panel: Krippendorff's alpha (nominal) and Wilson intervals.

``krippendorff_alpha`` follows Krippendorff (2011), "Computing Krippendorff's
Alpha-Reliability": build the coincidence matrix from every unit with at least
two values (missing values allowed), then alpha = 1 - D_o / D_e with the nominal
distance (0 if two values are equal, else 1). It is undefined (``None``) when
fewer than two values are pairable or every pairable value is the same.

``bootstrap_alpha`` gives a 95% percentile interval by resampling units with a
seeded ``GameRng``.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Hashable, Sequence

from zantine.rng import GameRng

Unit = Sequence[Hashable | None]  # one value per coder; None = missing
Z95 = 1.959964


def krippendorff_alpha(units: Sequence[Unit]) -> float | None:
    """Nominal alpha over units (rows), each holding one value per coder."""
    coincidence: dict[tuple[Hashable, Hashable], float] = defaultdict(float)
    for unit in units:
        values = [v for v in unit if v is not None]
        m = len(values)
        if m < 2:
            continue
        counts = Counter(values)
        for c, nc in counts.items():
            for k, nk in counts.items():
                pairs = nc * (nc - 1) if c == k else nc * nk
                coincidence[(c, k)] += pairs / (m - 1)
    totals: dict[Hashable, float] = defaultdict(float)
    for (c, _), o in coincidence.items():
        totals[c] += o
    n = sum(totals.values())
    if n <= 1:
        return None
    observed = sum(o for (c, k), o in coincidence.items() if c != k)
    expected = sum(totals[c] * totals[k] for c in totals for k in totals if c != k)
    if expected == 0:
        return None
    return 1 - (n - 1) * observed / expected


def bootstrap_alpha(
    units: Sequence[Unit], rng: GameRng, reps: int = 1000
) -> tuple[float | None, float | None]:
    """95% percentile interval for alpha, resampling whole units."""
    if not units:
        return None, None
    draws = []
    for _ in range(reps):
        sample = [units[rng.randint(0, len(units) - 1)] for _ in units]
        a = krippendorff_alpha(sample)
        if a is not None:
            draws.append(a)
    if len(draws) < reps / 2:
        return None, None
    draws.sort()
    lo = draws[int(0.025 * (len(draws) - 1))]
    hi = draws[math.ceil(0.975 * (len(draws) - 1))]
    return round(lo, 6), round(hi, 6)


def wilson(k: int, n: int, z: float = Z95) -> tuple[float | None, float | None]:
    """Wilson score 95% interval for ``k`` successes in ``n`` trials."""
    if n == 0:
        return None, None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, centre - half), 6), round(min(1.0, centre + half), 6)


def proportion(k: int, n: int) -> dict[str, object]:
    lo, hi = wilson(k, n)
    return {"correct": k, "n": n, "accuracy": round(k / n, 6) if n else None, "ci95": [lo, hi]}
