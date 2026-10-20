"""Graphs of a Minority Disadvantage run (#23), drawn only from ``minority.json`` numbers.

Same rules as ``graphs.py``: Models in fixed alphabetical order, one neutral
colour, titles that say what is measured, the interval caption on every chart.
The two Style Mask conditions are told apart by marker fill (filled = without,
open = with), not by colour.

- ``minority_disadvantage``: minority minus majority wrongful-ejection rate, pooled
  and per majority Model (left) and per minority Model (right), in both conditions.
- ``minority_rates``: the two pooled rates in each condition, and the change the
  Style Mask makes, pooled and per majority Model.
- ``scapegoating``: Impostor votes (and judged accusations, when present) that
  target a minority Crewmate, minus the base rate, pooled and per Impostor Model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from matplotlib import rc_context
from matplotlib.figure import Figure

from zantine.analysis.graphs import BAR, DOT, GRID, INK, STYLE, caption, interval_bars, save

POOLED = "All pairs (pooled)"
CONDITIONS = (("off", "without Style Mask", DOT), ("mask", "with Style Mask", "white"))


def render_minority(report: dict[str, Any], out: Path) -> list[Path]:
    files: list[Path] = []
    with rc_context(STYLE):
        for name, draw in (
            ("minority_disadvantage", disadvantage),
            ("minority_rates", rates),
            ("scapegoating", scapegoating),
        ):
            files += save(draw(report), out / name)
    return files


def _value(e: dict[str, Any] | None, key: str) -> float:
    v = None if e is None else e.get(key)
    return np.nan if v is None else float(v)


def paired_panel(
    ax: Any,
    rows: list[tuple[str, dict[str, Any], dict[str, Any]]],
    title: str,
    xlabel: str,
    *,
    zero: bool = True,
) -> None:
    """One row per label; each row shows the mask-off and mask-on estimate with its interval."""
    labels = [r[0] for r in rows]
    y = np.arange(len(rows))[::-1].astype(float)  # first row at the top
    ax.set_yticks(y, labels, fontsize=7.5)
    ax.set_ylim(-0.7, len(rows) - 0.3)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    if zero:
        ax.axvline(0, color=BAR, linewidth=0.8)
    for k, (_key, label, face) in enumerate(CONDITIONS):
        at = y + (0.15 if k == 0 else -0.15)
        est = np.array([_value(r[1 + k], "estimate") for r in rows])
        lo = np.array([_value(r[1 + k], "ci_low") for r in rows])
        hi = np.array([_value(r[1 + k], "ci_high") for r in rows])
        ok = np.isfinite(est)
        interval_bars(ax, np.where(ok, at, np.nan), lo, hi, horizontal=True)
        ax.scatter(
            est[ok], at[ok], s=24, color=face, edgecolor=DOT, linewidth=1.0, zorder=3, label=label
        )
        not_estimable(ax, at[~ok])
    ax.set_title(title, fontsize=9, color=INK, loc="left")
    ax.set_xlabel(xlabel, fontsize=7.5)


def not_estimable(ax: Any, at: np.ndarray) -> None:
    for y in at:
        ax.text(
            0.98,
            y,
            "n/a",
            transform=ax.get_yaxis_transform(),
            ha="right",
            va="center",
            fontsize=6.5,
            color=BAR,
        )


def legend(fig: Figure, ax: Any) -> None:
    """The two Style Mask markers, once per figure, clear of every panel."""
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", ncol=2, frameon=False, fontsize=7.5)


def _rows(groups: dict[str, Any], pooled: dict[str, Any], models: list[str], key: str):
    rows = [(POOLED, pooled["off"][key], pooled["mask"][key])]
    rows += [(m, groups[m]["off"][key], groups[m]["mask"][key]) for m in models]
    return rows


def disadvantage(report: dict[str, Any]) -> Figure:
    models = report["models"]
    d = report["disadvantage"]
    height = 1.6 + 0.42 * (len(models) + 1)
    fig = Figure(figsize=(12, height))
    fig.suptitle(
        "Minority Disadvantage: wrongful-ejection rate of minority minus majority Crewmates "
        "(Lopsided Lobbies, 8 vs 2)",
        color=INK,
        fontsize=10.5,
    )
    xlabel = "minority - majority rate (> 0: minority ejected more)"
    for n, (key, title) in enumerate(
        (("by_majority", "By majority Model"), ("by_minority", "By minority Model"))
    ):
        ax = fig.add_subplot(1, 2, n + 1)
        paired_panel(ax, _rows(d[key], d["pooled"], models, "disadvantage"), title, xlabel)
        if n == 0:
            legend(fig, ax)
    fig.subplots_adjust(
        left=0.15, right=0.98, top=1 - 0.7 / height, bottom=1.05 / height, wspace=0.55
    )
    caption(fig, report, f"Reading: {report['verdict']['text']}")
    return fig


def rates(report: dict[str, Any]) -> Figure:
    models = report["models"]
    d = report["disadvantage"]
    pooled = d["pooled"]
    height = 1.6 + 0.42 * (len(models) + 1)
    fig = Figure(figsize=(12, height))
    fig.suptitle("Wrongful-ejection rates and the Style Mask's effect", color=INK, fontsize=10.5)
    ax = fig.add_subplot(1, 2, 1)
    rows = [
        ("Minority Crewmates", pooled["off"]["minority_rate"], pooled["mask"]["minority_rate"]),
        ("Majority Crewmates", pooled["off"]["majority_rate"], pooled["mask"]["majority_rate"]),
    ]
    paired_panel(
        ax, rows, "Pooled wrongful-ejection rate", "share of Crewmate Seats ejected", zero=False
    )
    legend(fig, ax)
    ax = fig.add_subplot(1, 2, 2)
    labels = [POOLED, *models]
    change = [pooled["change"], *(d["by_majority"][m]["change"] for m in models)]
    y = np.arange(len(labels))[::-1].astype(float)
    est = np.array([_value(c, "estimate") for c in change])
    lo = np.array([_value(c, "ci_low") for c in change])
    hi = np.array([_value(c, "ci_high") for c in change])
    ok = np.isfinite(est)
    ax.set_yticks(y, labels, fontsize=7.5)
    ax.set_ylim(-0.7, len(labels) - 0.3)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.axvline(0, color=BAR, linewidth=0.8)
    interval_bars(ax, np.where(ok, y, np.nan), lo, hi, horizontal=True)
    ax.scatter(est[ok], y[ok], s=24, color=DOT, zorder=3)
    not_estimable(ax, y[~ok])
    ax.set_title(
        "Change the Style Mask makes, by majority Model", fontsize=9, color=INK, loc="left"
    )
    ax.set_xlabel("disadvantage without - with mask (> 0: mask shrinks it)", fontsize=7.5)
    fig.subplots_adjust(
        left=0.15, right=0.98, top=1 - 0.7 / height, bottom=1.05 / height, wspace=0.55
    )
    caption(fig, report)
    return fig


def scapegoating(report: dict[str, Any]) -> Figure:
    models = report["models"]
    s = report["scapegoating"]
    kinds = [("votes", "Impostor votes (ground truth)")]
    if "accusations" in s:
        kinds.append(("accusations", "Impostor accusations (#15 judgments)"))
    height = 1.6 + 0.42 * (len(models) + 1)
    fig = Figure(figsize=(6 * len(kinds) + 1, height))
    fig.suptitle(
        "Scapegoating: Impostor targets that are minority Crewmates, minus the base rate",
        color=INK,
        fontsize=10.5,
    )
    for n, (key, title) in enumerate(kinds):
        ax = fig.add_subplot(1, len(kinds), n + 1)
        block = s[key]
        rows = [(POOLED, block["pooled"]["off"]["excess"], block["pooled"]["mask"]["excess"])]
        rows += [
            (
                m,
                block["by_impostor_model"][m]["off"]["excess"],
                block["by_impostor_model"][m]["mask"]["excess"],
            )
            for m in models
        ]
        paired_panel(
            ax,
            rows,
            f"{title}, by Impostor Model",
            "share at minority - base rate (> 0: more than chance)",
        )
        if n == 0:
            legend(fig, ax)
    fig.subplots_adjust(
        left=0.2 if len(kinds) == 1 else 0.15,
        right=0.98,
        top=1 - 0.7 / height,
        bottom=1.05 / height,
        wspace=0.55,
    )
    caption(
        fig,
        report,
        "Base rate: living minority Crewmates / living Crewmates when the Meeting started.",
    )
    return fig
