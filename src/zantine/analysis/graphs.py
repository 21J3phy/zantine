"""Graphs of a run's results (#18), drawn only from the numbers in ``metrics.json``.

No ranking and no composite score (``docs/plan.md``):

- every chart lists Models in the same fixed alphabetical order;
- one neutral colour for every Model, so no Model is visually singled out;
- titles describe what is measured, never who is ahead;
- every chart carries the interval caption (marginal 95%, game-level cluster bootstrap).

Charts (each written as SVG with real text, and PNG):

- ``opening_map``: Productivity vs Vigilance, one dot per Model with an interval on
  each axis, dot area by cost per game, Flagship-Sibling pairs joined, and Models
  whose interval rectangles overlap shaded as one (descriptive) cluster.
- ``small_multiples``: one panel per metric (Balanced Lobbies, plus Benign-only
  Productivity, Sabotage Damage and cost), Models in fixed order.
- ``sabotage_damage``: Sabotage Damage per Impostor Seat (#33).
- ``cost_vs_metrics``: cost per game against a few metrics.
- ``mirror``: Mirror Matches, separately labelled as descriptive self-play.
- ``verification_protocols`` (v1.1 experiment runs, ``analysis/verification.py``):
  Sabotage Damage prevented against Productivity cost per Verification Protocol,
  relative to the optional baseline, plus each protocol's own damage and
  Benign-Match completion time.

Interval bars are drawn from their bounds, never as distances from the estimate:
a percentile interval need not contain its point estimate.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import numpy as np
from matplotlib import rc_context
from matplotlib.figure import Figure

INK = "#2d3748"
DOT = "#4a5568"
BAR = "#718096"
PAIR = "#a0aec0"
HULL = "#cbd5e0"
GRID = "#e2e8f0"
STYLE = {
    "svg.fonttype": "none",  # keep text as text (searchable, restylable by the website)
    "svg.hashsalt": "zantine-report",  # stable element ids
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.edgecolor": BAR,
    "axes.labelcolor": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "axes.spines.top": False,
    "axes.spines.right": False,
}

SMALL_MULTIPLES = (
    "productivity",
    "finished_share",
    "correct_vote_share",
    "impostors_ejected",
    "containment_time",
    "wrongly_ejected",
    "task_completion",
    "impostor_survival",
    "sabotage_damage",
    "productivity_benign",
    "cost_per_game",
    "reliability",
    "lie_rate",
)
COST_PANELS = ("productivity", "correct_vote_share", "impostor_survival", "sabotage_damage")


def render_all(report: dict[str, Any], out: Path) -> list[Path]:
    files: list[Path] = []
    with rc_context(STYLE):
        for name, draw in (
            ("opening_map", opening_map),
            ("small_multiples", small_multiples),
            ("sabotage_damage", sabotage_damage),
            ("cost_vs_metrics", cost_vs_metrics),
            ("mirror", mirror),
        ):
            fig = draw(report)
            files += save(fig, out / name)
    return files


def save(fig: Figure, stem: Path) -> list[Path]:
    svg, png = stem.with_suffix(".svg"), stem.with_suffix(".png")
    fig.savefig(svg, metadata={"Date": None, "Creator": None})
    fig.savefig(png, dpi=150, metadata={"Software": None})
    return [svg, png]


# Helpers ------------------------------------------------------------------------------


def models_of(report: dict[str, Any]) -> list[str]:
    return [m["model"] for m in report["models"]]


def values(metric: dict[str, Any], models: list[str]) -> tuple[np.ndarray, ...]:
    """(estimate, low, high) arrays in model order, NaN where not estimable."""

    def arr(key: str) -> np.ndarray:
        return np.array(
            [
                np.nan if metric["per_model"][m][key] is None else metric["per_model"][m][key]
                for m in models
            ],
            dtype=float,
        )

    return arr("estimate"), arr("ci_low"), arr("ci_high")


def caption(fig: Figure, report: dict[str, Any], extra: str = "") -> None:
    method = report["method"]
    text = (
        f"{method['intervals']} {method['replicates']} replicates, seed {method['seed']}. "
        "Models in fixed alphabetical order. "
        f"{report['run']['games_complete']} of {report['run']['games_planned']} games complete."
    )
    if extra:
        text += " " + extra
    fig.text(0.01, 0.005, text, fontsize=7, color=BAR, ha="left", va="bottom", wrap=True)


def note(ax: Any, text: str) -> None:
    ax.text(0.5, 0.5, text, transform=ax.transAxes, ha="center", va="center", color=BAR)


def dry_run(report: dict[str, Any]) -> bool:
    mode = report["run"].get("mode")
    return mode == "dry" if mode else report["run"]["total_cost_usd"] == 0


# Opening map --------------------------------------------------------------------------


def opening_map(report: dict[str, Any]) -> Figure:
    spec = report["opening_map"]
    models = models_of(report)
    xm = report["metrics"][spec["x"]]
    ym = report["metrics"][spec["y"]]
    x, xlo, xhi = values(xm, models)
    y, ylo, yhi = values(ym, models)
    cost, _, _ = values(report["metrics"][spec["size"]], models)

    fig = Figure(figsize=(8, 6.2))
    ax = fig.add_axes((0.1, 0.16, 0.86, 0.74))
    ax.set_title(
        "Productivity vs Vigilance per Model (Balanced Lobbies)", color=INK, fontsize=11, pad=10
    )
    ax.set_xlabel(f"{xm['label']}: {xm['unit']}; fewer Rounds = faster")
    ax.set_ylabel(f"{ym['label']} ({ym['unit']})")
    ax.grid(color=GRID, linewidth=0.6)
    ok = np.isfinite(x) & np.isfinite(y)
    if not ok.any():
        note(ax, "No Balanced Lobby with both measures yet")
        caption(fig, report)
        return fig

    # Clusters: Models whose interval rectangles overlap are drawn as one shaded region.
    pos = {m: i for i, m in enumerate(models)}
    span_x = np.nanmax(np.r_[xhi, x]) - np.nanmin(np.r_[xlo, x]) or 1.0
    span_y = np.nanmax(np.r_[yhi, y]) - np.nanmin(np.r_[ylo, y]) or 1.0
    shaded = False
    for group in spec["overlap_clusters"]:
        if len(group) < 2:
            continue
        pts = []
        for m in group:
            i = pos[m]
            for px in (xlo[i], xhi[i]):
                for py in (ylo[i], yhi[i]):
                    pts.append((px, py))
        hull = convex_hull(pad(pts, 0.02 * span_x, 0.02 * span_y))
        ax.fill(
            *zip(*hull, strict=True),
            color=HULL,
            alpha=0.45,
            linewidth=0,
            zorder=0,
            label=None if shaded else "Overlapping marginal intervals (read as one cluster)",
        )
        shaded = True

    # Flagship-Sibling pairs.
    joined = False
    for flagship, sibling in spec["flagship_sibling_pairs"]:
        a, b = pos[flagship], pos[sibling]
        if ok[a] and ok[b]:
            ax.plot(
                [x[a], x[b]],
                [y[a], y[b]],
                color=PAIR,
                linestyle="--",
                linewidth=1.2,
                zorder=1,
                label=None if joined else "Flagship and Sibling of the same lab",
            )
            joined = True

    interval_bars(ax, np.where(ok, y, np.nan), xlo, xhi, horizontal=True, cap=5)
    interval_bars(ax, np.where(ok, x, np.nan), ylo, yhi, horizontal=False, cap=5)
    sizes = dot_sizes(cost)
    ax.scatter(x[ok], y[ok], s=sizes[ok], color=DOT, edgecolor="white", linewidth=0.8, zorder=3)
    for i in np.flatnonzero(ok):
        ax.annotate(
            models[i],
            (x[i], y[i]),
            xytext=(6, 5),
            textcoords="offset points",
            fontsize=8,
            color=INK,
            zorder=4,
        )
    # Room for the labels to the right of the dots.
    x0, x1 = np.nanmin(np.r_[xlo, x]), np.nanmax(np.r_[xhi, x])
    y0, y1 = np.nanmin(np.r_[ylo, y]), np.nanmax(np.r_[yhi, y])
    ax.set_xlim(x0 - 0.06 * span_x, x1 + 0.25 * span_x)
    ax.set_ylim(y0 - 0.08 * span_y, y1 + 0.18 * span_y)
    if shaded or joined:
        ax.legend(loc="upper left", frameon=False, fontsize=8)
    size_note = (
        "Dot area: cost per game (dry run, $0: equal sizes)."
        if not np.nansum(cost) > 0
        else f"Dot area proportional to cost per game (largest ${np.nanmax(cost):,.2f})."
    )
    caption(fig, report, size_note)
    return fig


def dot_sizes(cost: np.ndarray) -> np.ndarray:
    c = np.where(np.isfinite(cost), cost, 0.0)
    if c.max() <= 0:
        return np.full(len(c), 90.0)
    return 30.0 + 370.0 * c / c.max()


def pad(points: list[tuple[float, float]], dx: float, dy: float) -> list[tuple[float, float]]:
    return [(px + sx * dx, py + sy * dy) for px, py in points for sx in (-1, 1) for sy in (-1, 1)]


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain; returns the hull counter-clockwise."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# Dot-and-interval panels ------------------------------------------------------------


def interval_bars(
    ax: Any,
    at: np.ndarray,
    lo: np.ndarray,
    hi: np.ndarray,
    *,
    horizontal: bool,
    lw: float = 1.0,
    cap: float = 4.0,
) -> None:
    """Draw each interval ``[lo, hi]`` at position ``at`` (a y for horizontal bars, an x
    for vertical ones), with end caps. Drawn from the bounds themselves, so an interval
    lying wholly above or below its estimate is shown as it is."""
    ok = np.isfinite(at) & np.isfinite(lo) & np.isfinite(hi)
    if not ok.any():
        return
    a, low, high = at[ok], lo[ok], hi[ok]
    if horizontal:
        ax.hlines(a, low, high, colors=BAR, linewidth=lw, zorder=2)
        ends = (np.r_[low, high], np.r_[a, a])
    else:
        ax.vlines(a, low, high, colors=BAR, linewidth=lw, zorder=2)
        ends = (np.r_[a, a], np.r_[low, high])
    marker = "|" if horizontal else "_"
    ax.scatter(*ends, marker=marker, s=cap**2, color=BAR, linewidths=lw, zorder=2)


def interval_panel(ax: Any, metric: dict[str, Any], models: list[str], zero: bool = False) -> None:
    est, lo, hi = values(metric, models)
    rows = np.arange(len(models))[::-1]  # first Model at the top
    ax.set_yticks(rows, models, fontsize=7.5)
    ax.set_ylim(-0.7, len(models) - 0.3)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ok = np.isfinite(est)
    if zero:
        ax.axvline(0, color=BAR, linewidth=0.8)
    if ok.any():
        interval_bars(ax, np.where(ok, rows, np.nan), lo, hi, horizontal=True)
        ax.scatter(est[ok], rows[ok], s=22, color=DOT, zorder=3)
    for r in rows[~ok]:
        ax.text(
            0.98,
            r,
            "n/a",
            transform=ax.get_yaxis_transform(),
            ha="right",
            va="center",
            fontsize=7,
            color=BAR,
        )
    ax.set_title(metric["label"], fontsize=9, color=INK, loc="left")
    ax.set_xlabel(metric["unit"], fontsize=7.5)


def small_multiples(report: dict[str, Any]) -> Figure:
    models = models_of(report)
    keys = [k for k in SMALL_MULTIPLES if k in report["metrics"]]
    cols = 3
    rows = math.ceil(len(keys) / cols)
    height = 2.7 * rows + 1.2
    fig = Figure(figsize=(12, height))
    fig.suptitle(
        "Per-Model metrics (Balanced Lobbies unless the panel says otherwise)",
        color=INK,
        fontsize=11,
    )
    grid = fig.add_gridspec(
        rows,
        cols,
        left=0.11,
        right=0.97,
        top=1 - 0.6 / height,
        bottom=1.0 / height,
        hspace=0.8,
        wspace=0.8,
    )
    for n, key in enumerate(keys):
        ax = fig.add_subplot(grid[n // cols, n % cols])
        interval_panel(ax, report["metrics"][key], models, zero=key == "sabotage_damage")
    caption(fig, report, "n/a: not estimable from the games so far.")
    return fig


def sabotage_damage(report: dict[str, Any]) -> Figure:
    models = models_of(report)
    metric = report["metrics"]["sabotage_damage"]
    fig = Figure(figsize=(7, 4.2))
    ax = fig.add_axes((0.24, 0.2, 0.72, 0.68))
    interval_panel(ax, metric, models, zero=True)
    ax.set_title("", loc="left")
    fig.suptitle(
        "Sabotage Damage per Impostor Seat (vs Benign Matches of the same lineup)",
        fontsize=10,
        color=INK,
    )
    fit = metric["fit"]
    caption(
        fig,
        report,
        f"Lineup fixed-effects least squares on {fit['lobbies']} Balanced Lobbies and "
        f"{fit['benign']} Benign Matches; unfinished games count as the round cap.",
    )
    return fig


def cost_vs_metrics(report: dict[str, Any]) -> Figure:
    models = models_of(report)
    cost, clo, chi = values(report["metrics"]["cost_per_game"], models)
    keys = [k for k in COST_PANELS if k in report["metrics"]]
    fig = Figure(figsize=(12, 3.8))
    fig.suptitle("Cost per game against per-Model metrics", color=INK, fontsize=11)
    for n, key in enumerate(keys):
        ax = fig.add_subplot(1, len(keys), n + 1)
        metric = report["metrics"][key]
        est, lo, hi = values(metric, models)
        ax.grid(color=GRID, linewidth=0.6)
        ax.set_title(metric["label"], fontsize=9, color=INK, loc="left")
        ax.set_xlabel("Cost per game (USD)", fontsize=7.5)
        ax.set_ylabel(metric["unit"], fontsize=7.5)
        ok = np.isfinite(est) & np.isfinite(cost)
        if not ok.any():
            note(ax, "n/a")
            continue
        interval_bars(ax, np.where(ok, est, np.nan), clo, chi, horizontal=True, lw=0.9)
        interval_bars(ax, np.where(ok, cost, np.nan), lo, hi, horizontal=False, lw=0.9)
        ax.scatter(cost[ok], est[ok], s=22, color=DOT, zorder=3)
        for i in np.flatnonzero(ok):
            ax.annotate(
                models[i],
                (cost[i], est[i]),
                xytext=(4, 3),
                textcoords="offset points",
                fontsize=6.5,
                color=INK,
            )
    fig.subplots_adjust(left=0.06, right=0.98, top=0.82, bottom=0.24, wspace=0.35)
    caption(fig, report, "Dry run: no API cost recorded." if dry_run(report) else "")
    return fig


def mirror(report: dict[str, Any]) -> Figure:
    models = models_of(report)
    keys = list(report["mirror"])
    fig = Figure(figsize=(12, 3.6))
    fig.suptitle(
        "Mirror Matches: self-play, descriptive only (attackers and defenders are the same "
        "Model; not a measure of defense)",
        color=INK,
        fontsize=10,
    )
    for n, key in enumerate(keys):
        ax = fig.add_subplot(1, len(keys), n + 1)
        interval_panel(ax, report["mirror"][key], models)
    fig.subplots_adjust(left=0.1, right=0.98, top=0.8, bottom=0.25, wspace=0.75)
    caption(fig, report)
    return fig


# Verification Protocol experiment (#37) ----------------------------------------------


def _protocol_values(
    report: dict[str, Any], protocols: list[str], key: str
) -> tuple[np.ndarray, ...]:
    def arr(field: str) -> np.ndarray:
        out = []
        for p in protocols:
            v = (report["protocols"][p].get(key) or {}).get(field)
            out.append(np.nan if v is None else v)
        return np.array(out, dtype=float)

    return arr("estimate"), arr("ci_low"), arr("ci_high")


def verification_protocols(report: dict[str, Any]) -> Figure:
    """Sabotage Damage prevented against Productivity cost, one dot per Verification
    Protocol, relative to the optional baseline; and each protocol's own numbers."""
    protocols = list(report["order"])
    fig = Figure(figsize=(11, 4.8))
    fig.suptitle(
        "Verification Protocols: Sabotage Damage prevented vs Productivity cost",
        color=INK,
        fontsize=11,
    )
    ax = fig.add_axes((0.07, 0.22, 0.42, 0.62))
    ax.set_xlabel("Productivity cost: extra Rounds to finish in Benign Matches vs optional")
    ax.set_ylabel("Sabotage Damage prevented vs optional (Rounds)")
    ax.grid(color=GRID, linewidth=0.6)
    ax.axhline(0, color=BAR, linewidth=0.8)
    ax.axvline(0, color=BAR, linewidth=0.8)
    if not report["baseline_present"]:
        note(ax, "No optional baseline loaded\n(zantine report <run> --baseline <v1 run>)")
    else:
        others = [p for p in protocols if p != report["baseline"]]
        x, xlo, xhi = _protocol_values(report, others, "productivity_cost")
        y, ylo, yhi = _protocol_values(report, others, "damage_prevented")
        ok = np.isfinite(x) & np.isfinite(y)
        interval_bars(ax, np.where(ok, y, np.nan), xlo, xhi, horizontal=True, cap=5)
        interval_bars(ax, np.where(ok, x, np.nan), ylo, yhi, horizontal=False, cap=5)
        ax.scatter(x[ok], y[ok], s=40, color=DOT, edgecolor="white", linewidth=0.8, zorder=3)
        ax.scatter([0], [0], s=40, facecolor="white", edgecolor=DOT, linewidth=1.2, zorder=3)
        ax.annotate(
            f"{report['baseline']} (baseline)",
            (0, 0),
            xytext=(6, -11),
            textcoords="offset points",
            fontsize=8,
            color=INK,
        )
        for i in np.flatnonzero(ok):
            ax.annotate(
                others[i], (x[i], y[i]), xytext=(6, 5), textcoords="offset points", fontsize=8
            )
        for i in np.flatnonzero(~ok):
            ax.text(
                0.02,
                0.95 - 0.06 * i,
                f"{others[i]}: n/a",
                transform=ax.transAxes,
                fontsize=7,
                color=BAR,
            )
        span = np.r_[xlo, xhi, ylo, yhi, 0.0]
        lim = np.nanmax(np.abs(span)) if np.isfinite(span).any() else 1.0
        lim = 1.15 * (lim or 1.0)
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)

    panels = (
        ("sabotage_damage", "Sabotage Damage per Balanced Lobby", "extra Rounds vs own Benign"),
        ("benign_completion", "Benign Matches: time to finish", "Rounds (restricted mean)"),
    )
    for n, (key, title, unit) in enumerate(panels):
        pax = fig.add_axes((0.64, 0.6 - 0.38 * n, 0.33, 0.24))
        est, lo, hi = _protocol_values(report, protocols, key)
        rows = np.arange(len(protocols))[::-1]
        pax.set_yticks(rows, protocols, fontsize=8)
        pax.set_ylim(-0.7, len(protocols) - 0.3)
        pax.grid(axis="x", color=GRID, linewidth=0.6)
        if key == "sabotage_damage":
            pax.axvline(0, color=BAR, linewidth=0.8)
        ok = np.isfinite(est)
        if ok.any():
            interval_bars(pax, np.where(ok, rows, np.nan), lo, hi, horizontal=True)
            pax.scatter(est[ok], rows[ok], s=22, color=DOT, zorder=3)
        for r in rows[~ok]:
            pax.text(
                0.98,
                r,
                "n/a",
                transform=pax.get_yaxis_transform(),
                ha="right",
                va="center",
                fontsize=7,
                color=BAR,
            )
        pax.set_title(title, fontsize=9, color=INK, loc="left")
        pax.set_xlabel(unit, fontsize=7.5)

    method = report["method"]
    counts = ", ".join(
        f"{p}: {v['games']['lobby']} lobbies + {v['games']['benign']} Benign"
        for p, v in report["protocols"].items()
    )
    sources = "; ".join(
        f"{r['role']} run, {r['mode'] or 'unplayed'}"
        + (" (scripted stand-ins, not Models)" if r["mode"] == "dry" else "")
        for r in report["runs"]
    )
    finished = ", ".join(
        f"{p} {v['benign_finished']['estimate']:.0%}"
        for p, v in report["protocols"].items()
        if v["benign_finished"]["estimate"] is not None
    )
    text = (
        f"{method['intervals']} {method['replicates']} replicates, seed {method['seed']}. "
        "Protocols in fixed order. Each protocol's damage is measured against its own "
        "Benign Matches; unfinished games count as the round cap, which compresses damage "
        f"when work rarely finishes (Benign Matches finished: {finished or 'n/a'}). "
        f"Games: {counts}. Sources: {sources}."
    )
    fig.text(0.01, 0.005, text, fontsize=7, color=BAR, ha="left", va="bottom", wrap=True)
    return fig
