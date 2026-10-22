"""v1.4 graphs as standalone SVG files (no plotting dependency).

Three graphs from ``analysis.leader_results``, every point with its 95%
cluster-bootstrap interval:

- ``compliance.svg``: Compliance per condition, Known vs Hidden Leader, grouped
  by the Leader's role.
- ``time_to_eject.svg``: share of Compromised Leaders still in play after each
  Round (Known vs Hidden), with the censored mean time to eject in the legend.
- ``vote_pull.svg``: the Leader's Vote Pull vs ordinary Seats', per condition.

Colors follow the reference categorical order (slot 1 blue, slot 2 orange),
with separate light and dark steps; values and labels use text colors, and
every mark has a hover title. Results are graphs, never rankings. Each figure's
description names the treatment its games share (Verification Protocol, Style Mask). Results from
a dry run (``results["scoring"]`` false) carry their label in every figure's
corner, title and description, so they can't pass for scored results.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape
from pathlib import Path
from typing import Any

from zantine.leader.analysis import condition_label

W, H = 640, 360
LEFT, RIGHT, TOP, BOTTOM = 64, 24, 72, 56
STYLE = """
<style>
  svg { --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e; --grid:#e4e3df;
        --s1:#2a78d6; --s2:#eb6834; font-family: system-ui, sans-serif; }
  @media (prefers-color-scheme: dark) {
    svg { --surface:#1a1a19; --ink:#f0efec; --ink-2:#c3c2b7; --grid:#3a3a38;
          --s1:#3987e5; --s2:#d95926; }
  }
  .bg { fill: var(--surface); } .title { fill: var(--ink); font-size: 16px; font-weight: 600; }
  .sub, .tick, .label { fill: var(--ink-2); font-size: 12px; }
  .value { fill: var(--ink); font-size: 11px; }
  .grid { stroke: var(--grid); stroke-width: 1; } .axis { stroke: var(--ink-2); stroke-width: 1; }
  .s1 { stroke: var(--s1); fill: var(--s1); } .s2 { stroke: var(--s2); fill: var(--s2); }
  .ci { stroke-width: 2; fill: none; } .dot { stroke: var(--surface); stroke-width: 2; }
  .line { stroke-width: 2; fill: none; }
</style>"""


def _svg(
    title: str,
    subtitle: str,
    body: Sequence[str],
    desc: str,
    label: str | None = None,
    note: str | None = None,
) -> str:
    if note:
        desc = f"{note}. {desc}"
    if label:
        title, desc = f"{title} [{label}]", f"{label}. {desc}"
        body = [
            *body,
            f'<text class="title" x="{W - RIGHT}" y="{H - 12}" text-anchor="end" '
            f'style="font-size:12px">{escape(label)}</text>',
        ]
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
            f'height="{H}" role="img" aria-label="{escape(title)}">',
            f"<title>{escape(title)}</title><desc>{escape(desc)}</desc>",
            STYLE,
            f'<rect class="bg" width="{W}" height="{H}"/>',
            f'<text class="title" x="{LEFT}" y="26">{escape(title)}</text>',
            f'<text class="sub" x="{LEFT}" y="44">{escape(subtitle)}</text>',
            *body,
            "</svg>",
            "",
        ]
    )


def _y(v: float, lo: float, hi: float) -> float:
    return TOP + (H - TOP - BOTTOM) * (1 - (v - lo) / (hi - lo))


def _y_axis(lo: float, hi: float, ticks: Sequence[float], fmt: str) -> list[str]:
    out = []
    for t in ticks:
        y = _y(t, lo, hi)
        out.append(f'<line class="grid" x1="{LEFT}" x2="{W - RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(
            f'<text class="tick" x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">'
            f"{format(t, fmt)}</text>"
        )
    return out


def _legend(items: Sequence[tuple[str, str]], y: float = 60) -> list[str]:
    out, x = [], LEFT
    for cls, name in items:
        out.append(f'<circle class="{cls} dot" cx="{x + 5}" cy="{y - 4}" r="5"/>')
        out.append(f'<text class="label" x="{x + 14}" y="{y}">{escape(name)}</text>')
        x += 14 + 7 * len(name) + 24
    return out


def _fmt(e: dict[str, Any], pct: bool) -> str:
    if e["estimate"] is None:
        return "no data"
    f = (lambda v: f"{v:.0%}") if pct else (lambda v: f"{v:.1f}")
    ci = "" if e["ci_low"] is None else f" (95% CI {f(e['ci_low'])} to {f(e['ci_high'])})"
    return f"{f(e['estimate'])}{ci}, {e['n_games']} games"


def _short(e: dict[str, Any]) -> str:
    if e["estimate"] is None:
        return "no data"
    ci = "" if e["ci_low"] is None else f" ({e['ci_low']:.1f} to {e['ci_high']:.1f})"
    return f"{e['estimate']:.1f}{ci} Rounds"


def dot_chart(
    title: str,
    subtitle: str,
    groups: Sequence[str],
    series: Sequence[tuple[str, str, Sequence[dict[str, Any]]]],
    *,
    pct: bool = True,
    label: str | None = None,
    note: str | None = None,
) -> str:
    """Dots with CI whiskers: ``groups`` on x, one dot per series (name, css class, estimates)."""
    lo, hi = 0.0, 1.0
    body = _y_axis(lo, hi, [0, 0.25, 0.5, 0.75, 1.0], ".0%")
    body += _legend([(cls, name) for name, cls, _ in series])
    band = (W - LEFT - RIGHT) / len(groups)
    for g, group in enumerate(groups):
        cx = LEFT + band * (g + 0.5)
        body.append(
            f'<text class="label" x="{cx:.1f}" y="{H - BOTTOM + 20}" '
            f'text-anchor="middle">{escape(group)}</text>'
        )
        for s, (name, cls, estimates) in enumerate(series):
            e = estimates[g]
            x = cx + (s - (len(series) - 1) / 2) * 32
            tip = f"{group}, {name}: {_fmt(e, pct)}"
            if e["estimate"] is None:
                body.append(
                    f'<text class="tick" x="{x:.1f}" y="{_y(0.05, lo, hi):.1f}" '
                    f'text-anchor="middle">n/a<title>{escape(tip)}</title></text>'
                )
                continue
            parts = [f"<g><title>{escape(tip)}</title>"]
            if e["ci_low"] is not None:
                y1, y2 = _y(e["ci_low"], lo, hi), _y(e["ci_high"], lo, hi)
                parts.append(
                    f'<line class="{cls} ci" x1="{x:.1f}" x2="{x:.1f}" '
                    f'y1="{y1:.1f}" y2="{y2:.1f}"/>'
                )
            y = _y(e["estimate"], lo, hi)
            parts.append(f'<circle class="{cls} dot" cx="{x:.1f}" cy="{y:.1f}" r="5"/>')
            parts.append(
                # The left dot is labelled on its left, the right one on its right.
                f'<text class="value" x="{x + (-9 if s == 0 else 9):.1f}" y="{y + 4:.1f}" '
                f'text-anchor="{"end" if s == 0 else "start"}">{e["estimate"]:.0%}</text></g>'
            )
            body.append("".join(parts))
    desc = "; ".join(
        f"{groups[g]}, {name}: {_fmt(est[g], pct)}"
        for name, _, est in series
        for g in range(len(groups))
    )
    return _svg(title, subtitle, body, desc, label, note)


def curve_chart(
    title: str,
    subtitle: str,
    series: Sequence[tuple[str, str, Sequence[dict[str, Any]]]],
    label: str | None = None,
    note: str | None = None,
) -> str:
    """Step curves of the share still in play by Round: (name, css class, curve points)."""
    cap = max((p["round"] for _, _, pts in series for p in pts), default=1) or 1
    lo, hi = 0.0, 1.0
    body = _y_axis(lo, hi, [0, 0.25, 0.5, 0.75, 1.0], ".0%")
    body += _legend([(cls, name) for name, cls, _ in series])

    def x(r: float) -> float:
        return LEFT + (W - LEFT - RIGHT) * r / cap

    for t in range(0, cap + 1, 5):
        body.append(
            f'<text class="tick" x="{x(t):.1f}" y="{H - BOTTOM + 18}" '
            f'text-anchor="middle">{t}</text>'
        )
    body.append(
        f'<text class="label" x="{(LEFT + W - RIGHT) / 2}" y="{H - 12}" '
        f'text-anchor="middle">Round</text>'
    )
    for name, cls, points in series:
        if not points:
            continue
        first = points[0]
        path = [f"M{x(first['round']):.1f},{_y(first['in_play'], lo, hi):.1f}"]
        path += [f"H{x(p['round']):.1f}V{_y(p['in_play'], lo, hi):.1f}" for p in points[1:]]
        tips = ", ".join(f"Round {p['round']}: {p['in_play']:.0%}" for p in points[::5])
        body.append(
            f'<path class="{cls} line" d="{"".join(path)}">'
            f"<title>{escape(name)}: {escape(tips)}</title></path>"
        )
    desc = "; ".join(
        f"{name}: {pts[-1]['in_play']:.0%} still in play at the cap"
        for name, _, pts in series
        if pts
    )
    return _svg(title, subtitle, body, desc, label, note)


def treatment_text(treatment: dict[str, Any] | None) -> str | None:
    if not treatment:
        return None
    mask = "on" if treatment.get("style_mask") else "off"
    return f"Verification Protocol {treatment.get('verification_protocol')}, Style Mask {mask}"


def write_graphs(results: dict[str, Any], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    c = results["conditions"]
    label = results.get("label")
    note = treatment_text(results.get("treatment"))
    roles = ("crew", "compromised")
    by = {known: [c[f"{known}_{role}"] for role in roles] for known in ("known", "hidden")}
    paths = []

    path = out_dir / "compliance.svg"
    path.write_text(
        dot_chart(
            "Compliance by Leader condition",
            "Share of the Leader's checked assignments the target carried out",
            ["Crewmate Leader", "Compromised Leader"],
            [
                ("Known Leader", "s1", [s["compliance"] for s in by["known"]]),
                ("Hidden Leader", "s2", [s["compliance"] for s in by["hidden"]]),
            ],
            label=label,
            note=note,
        ),
        encoding="utf-8",
    )
    paths.append(path)

    eject = {k: c[f"{k}_compromised"]["time_to_eject"] for k in ("known", "hidden")}
    path = out_dir / "time_to_eject.svg"
    path.write_text(
        curve_chart(
            "Compromised Leaders still in play",
            "Mean Round of Ejection in the legend; never-ejected Leaders count at the round cap",
            [
                (
                    f"{k.title()} · mean {_short(eject[k]['mean_rounds_censored_at_cap'])}",
                    cls,
                    eject[k]["in_play_curve"],
                )
                for k, cls in (("known", "s1"), ("hidden", "s2"))
            ],
            label=label,
            note=note,
        ),
        encoding="utf-8",
    )
    paths.append(path)

    conditions = list(c)
    path = out_dir / "vote_pull.svg"
    path.write_text(
        dot_chart(
            "Leader Vote Pull vs ordinary Seats",
            "Share of other players' ballots matching what the Seat argued for",
            [condition_label(k).removesuffix(" Leader") for k in conditions],
            [
                ("Leader", "s1", [c[k]["vote_pull"]["leader"] for k in conditions]),
                ("Ordinary Seats", "s2", [c[k]["vote_pull"]["ordinary"] for k in conditions]),
            ],
            label=label,
            note=note,
        ),
        encoding="utf-8",
    )
    paths.append(path)
    return paths
