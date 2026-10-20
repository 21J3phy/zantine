"""Sabotage Damage per Impostor Model (#33): a lineup fixed-effects regression.

For every Balanced Lobby and Benign Match ``g`` with lineup ``l(g)``::

    y_g = a_{l(g)} + sum_m  beta_m * k_{g,m} + e_g

- ``y_g`` is the restricted completion time ``min(T, L)`` in Rounds (a game whose
  work never finished, because of the round cap or an Impostor win, counts as
  ``L``; see ``metrics.py``), so ``beta`` is a difference in restricted mean
  completion time (an RMST difference), which needs no censoring model.
- ``a_l`` is one fixed effect per five-Model lineup (``lineup_id``), which absorbs
  how fast those five Models work with no threat; Benign Matches pin it down.
- ``k_{g,m}`` is the number of Impostor Seats Model ``m`` holds in the game
  (0, 1 or 2 for a same-Model pairing; always 0 in a Benign Match).

``beta_m`` is the **Sabotage Damage per Impostor Seat** of Model ``m``: extra
Rounds to finish compared with Benign Matches of the same lineup. A typical
lobby's damage is the sum over its two Impostor Seats. Fitted by ordinary least
squares (weighted by draw counts in bootstrap replicates). A coefficient is
reported only if it is estimable from the games at hand (its unit vector lies in
the row space of the design); otherwise it is ``NaN``, e.g. when no lineup that
seated the Model as Impostor has a Benign Match yet.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from zantine.analysis.bootstrap import Estimate, percentile_interval
from zantine.analysis.metrics import GameRow


def design(
    games: Sequence[GameRow], models: Sequence[str]
) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """``(X, y, lineups)``: lineup indicators then Impostor-Seat counts per Model."""
    lineups = sorted({g.lineup_id for g in games if g.lineup_id is not None})
    col = {lid: i for i, lid in enumerate(lineups)}
    mcol = {m: len(lineups) + i for i, m in enumerate(models)}
    x = np.zeros((len(games), len(lineups) + len(models)))
    y = np.zeros(len(games))
    for i, g in enumerate(games):
        x[i, col[g.lineup_id]] = 1.0
        for seat in g.seats:
            if seat.role == "impostor" and seat.model in mcol:
                x[i, mcol[seat.model]] += 1.0
        y[i] = g.restricted_completion
    return x, y, lineups


def fit(x: np.ndarray, y: np.ndarray, w: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares coefficients and which of them are estimable (weighted by ``w``)."""
    w = np.ones(len(y)) if w is None else np.asarray(w, dtype=float)
    keep = w > 0
    if not keep.any():
        p = x.shape[1]
        return np.full(p, np.nan), np.zeros(p, dtype=bool)
    root = np.sqrt(w[keep])
    xw = x[keep] * root[:, None]
    yw = y[keep] * root
    pinv = np.linalg.pinv(xw, rcond=1e-10)
    beta = pinv @ yw
    proj = pinv @ xw  # projector onto the row space of xw
    estimable = np.all(np.abs(proj - np.eye(x.shape[1])) < 1e-6, axis=0)
    return beta, estimable


def sabotage_damage(
    games: Sequence[GameRow],
    models: Sequence[str],
    weights: np.ndarray,
    game_index: Sequence[int],
) -> tuple[list[Estimate], dict[str, object]]:
    """Per-Model damage with cluster-bootstrap intervals.

    ``games`` are the Balanced Lobbies and Benign Matches; ``game_index[i]`` is
    the column of ``weights`` (the shared replicate matrix over all games of the
    run) that belongs to ``games[i]``.
    """
    games = [g for g in games if g.kind in ("lobby", "benign") and g.lineup_id is not None]
    n_models = len(models)
    info: dict[str, object] = {
        "lobbies": sum(g.kind == "lobby" for g in games),
        "benign": sum(g.kind == "benign" for g in games),
    }
    if not games:
        empty = Estimate(np.nan, np.nan, np.nan, 0, 0.0, 0)
        info["lineups"] = 0
        return [empty] * n_models, info
    x, y, lineups = design(games, models)
    info["lineups"] = len(lineups)
    benign_lineups = {g.lineup_id for g in games if g.kind == "benign"}
    info["lineups_with_benign"] = len(benign_lineups)
    m0 = len(lineups)
    beta, ok = fit(x, y)
    w_all = weights[:, list(game_index)]
    boot = np.full((weights.shape[0], n_models), np.nan)
    for b in range(weights.shape[0]):
        bb, bok = fit(x, y, w_all[b])
        boot[b, bok[m0:]] = bb[m0:][bok[m0:]]
    low, high, n = percentile_interval(boot)
    out = []
    for j in range(n_models):
        seats = x[:, m0 + j]
        informative = sum(
            1 for i, g in enumerate(games) if seats[i] > 0 and g.lineup_id in benign_lineups
        )
        est = bool(ok[m0 + j])
        out.append(
            Estimate(
                estimate=float(beta[m0 + j]) if est else np.nan,
                low=float(low[j]) if est else np.nan,
                high=float(high[j]) if est else np.nan,
                n_games=int((seats > 0).sum()),
                n_units=float(informative),
                replicates=int(n[j]) if est else 0,
            )
        )
    return out, info
