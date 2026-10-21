"""Finding, ranking and writing Moments.

``find_moments(records, audit)`` runs every registered detector over one game.
``rank`` orders candidates from any number of games by a transparent score,
never by a model's judgment:

    score = weight + edge + lateness

- ``weight``: a fixed number per Moment type (the detector's ``weight``), an
  editorial prior on how striking that type is. It is not measured from games.
- ``edge``: in [0, 1], how sharp this Moment is; each detector documents its
  rule (e.g. 1.0 for an Ejection decided by one vote, 1 / margin in general).
- ``lateness``: the Moment's Round divided by the game's last Round, so a
  Moment near the end of its game ranks above the same Moment early on.

Ties break on transcript, seed, Round, Meeting, type and caption, so the order
is total.
Every Moment links into the replay viewer at its Meeting (``#meeting=M&round=N``,
so viewers older than Meeting links still open the right Round) or, for
action-phase Moments, its Round (``#round=N``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from zantine.moments.detectors import DETECTORS, Detector, Moment, capabilities, missing
from zantine.moments.facts import GameFacts, build_facts

MOMENTS_SCHEMA_VERSION = 1
NOTE = "Each Moment is a story about one game, never a statistic."


def find_moments(
    records: Iterable[dict[str, Any]],
    audit: dict[str, Any] | None = None,
    *,
    transcript: str | None = None,
    replay: str | None = None,
) -> dict[str, Any]:
    """Every candidate Moment in one game, plus the Moment types that were skipped."""
    facts = build_facts(records, audit)
    caps = capabilities(facts)
    moments: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for det in DETECTORS.values():
        reason = missing(det, caps, facts)
        if reason is not None:
            skipped.append({"type": det.name, "reason": reason})
            continue
        for m in det.detect(facts):
            moments.append(_to_dict(m, det, facts, transcript, replay))
    return {
        "transcript": transcript,
        "replay": replay,
        "seed": facts.seed,
        "audit": _audit_info(facts.audit),
        "audit_rejected": facts.audit_problem,
        "skipped": skipped,
        "moments": rank(moments),
    }


def link(replay: str | None, round: int, meeting: int | None) -> str:
    """``#round=N``, or ``#meeting=M&round=N`` for a Meeting: viewers that predate
    Meeting links still open the right Round."""
    target = f"#meeting={meeting}&round={round}" if meeting is not None else f"#round={round}"
    return f"{replay or ''}{target}"


def score_parts(m: Moment, det: Detector, facts: GameFacts) -> dict[str, float]:
    lateness = m.round / facts.last_round if facts.last_round else 0.0
    return {
        "weight": det.weight,
        "edge": round(m.edge, 6),
        "lateness": round(min(max(lateness, 0.0), 1.0), 6),
    }


def rank(moments: Iterable[dict[str, Any]], top: int | None = None) -> list[dict[str, Any]]:
    ordered = sorted(
        moments,
        key=lambda m: (
            -m["score"],
            m["transcript"] or "",
            m["seed"] if m["seed"] is not None else -1,
            m["round"],
            m["meeting"] if m["meeting"] is not None else 0,
            m["type"],
            m["caption"],
        ),
    )
    return ordered if top is None else ordered[:top]


def _to_dict(
    m: Moment, det: Detector, facts: GameFacts, transcript: str | None, replay: str | None
) -> dict[str, Any]:
    parts = score_parts(m, det, facts)
    return {
        "type": m.type,
        "title": det.title,
        "caption": m.caption,
        "seed": facts.seed,
        "transcript": transcript,
        "round": m.round,
        "meeting": m.meeting,
        "link": link(replay, m.round, m.meeting),
        "seats": [{"seat": s, "name": facts.name(s), "role": facts.role(s)} for s in m.seats],
        "score": round(sum(parts.values()), 6),
        "score_parts": parts,
        "evidence": m.evidence,
        "provisional": m.provisional,
    }


def _audit_info(audit: dict[str, Any] | None) -> dict[str, Any] | None:
    if audit is None:
        return None
    ex = audit.get("extractor", {})
    return {
        "extractor": ex.get("name"),
        "model": ex.get("model"),
        "scoring": bool(ex.get("scoring")),
        "complete": bool(audit.get("complete")),
    }


# Files -----------------------------------------------------------------------


def transcripts_in(path: Path) -> list[Path]:
    """A transcript file, or every transcript (JSONL starting with a header) under a run dir."""
    if path.is_file():
        return [path]
    return [p for p in sorted(path.rglob("*.jsonl")) if _is_transcript(p)]


def _is_transcript(path: Path) -> bool:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    first = json.loads(line)
                except json.JSONDecodeError:
                    return False
                return isinstance(first, dict) and first.get("kind") == "header"
    return False


def audit_path(transcript: Path) -> Path:
    """Where ``zantine audit`` writes this transcript's report."""
    return transcript.with_suffix(".audit.json")


def replay_path(transcript: Path) -> Path:
    """Where ``zantine replay`` writes this transcript's viewer."""
    return transcript.with_suffix(".html")


# In every Zantine replay page (the embedded view model).
REPLAY_MARKER = 'id="replay-data"'


def sync_replay(records: list[dict[str, Any]], path: Path) -> bool:
    """Make the Zantine replay at ``path`` show exactly these records, with the
    current viewer: write it if missing, rewrite it if its bytes differ (an older
    viewer, or a transcript overwritten by a rerun). A file at that path that is
    not a Zantine replay is left alone. Returns whether the file was written."""
    from zantine.replay import build_view, render_html

    old = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
    if old is not None and REPLAY_MARKER not in old:
        return False
    page = render_html(build_view(records))
    if page == old:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return True


def relative(path: Path, start: Path) -> str:
    try:
        return Path(os.path.relpath(path.resolve(), start.resolve())).as_posix()
    except ValueError:  # different drives on Windows
        return path.resolve().as_posix()


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Moments",
        "",
        f"{NOTE} Ranked by score = type weight + edge + lateness (see `zantine.moments.finder`).",
        "",
    ]
    if not report["moments"]:
        lines.append("No Moments found.")
    for i, m in enumerate(report["moments"], 1):
        p = m["score_parts"]
        provisional = " *Provisional: rests on a dry-run or incomplete Claim Audit.*"
        flag = provisional if m["provisional"] else ""
        lines.append(
            f"{i}. **{m['title']}**: {m['caption']} [Replay]({m['link']})"
            f" (score {m['score']:.2f} = {p['weight']:g} + {p['edge']:.2f}"
            f" + {p['lateness']:.2f}){flag}"
        )
    skipped = sorted({(s["type"], s["reason"]) for g in report["games"] for s in g["skipped"]})
    if skipped:
        lines += ["", "Skipped Moment types:", ""]
        lines += [f"- `{t}`: {reason}" for t, reason in skipped]
    return "\n".join(lines) + "\n"


def find_in_path(
    path: Path, *, out_dir: Path, top: int | None = None, write_replays: bool = True
) -> dict[str, Any]:
    """Moments from a transcript or a run dir. Uses each transcript's
    ``.audit.json`` when it matches the transcript, and (``write_replays``)
    writes or refreshes each transcript's replay page from that transcript, so
    every link opens the game it describes. Links are relative to ``out_dir``."""
    games: list[dict[str, Any]] = []
    moments: list[dict[str, Any]] = []
    for transcript in transcripts_in(path):
        records = [json.loads(x) for x in transcript.read_text(encoding="utf-8").splitlines() if x]
        audit_file = audit_path(transcript)
        audit = json.loads(audit_file.read_text(encoding="utf-8")) if audit_file.is_file() else None
        replay = replay_path(transcript)
        if write_replays:
            sync_replay(records, replay)
        game = find_moments(
            records,
            audit,
            transcript=relative(transcript, out_dir),
            replay=relative(replay, out_dir),
        )
        moments += game.pop("moments")
        games.append({**game, "audit_file": relative(audit_file, out_dir) if audit else None})
    return {
        "kind": "moments",
        "schema": MOMENTS_SCHEMA_VERSION,
        "note": NOTE,
        "scoring": {
            "score": "weight + edge + lateness",
            "types": {
                d.name: {"title": d.title, "weight": d.weight, "edge": d.edge_rule}
                for d in DETECTORS.values()
            },
        },
        "games": games,
        "candidates": len(moments),
        "moments": rank(moments, top),
    }
