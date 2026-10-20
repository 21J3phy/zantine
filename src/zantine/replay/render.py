"""Render a replay view model as one self-contained static HTML file.

The page has inline CSS and JS and loads nothing from the network. The view
model is embedded as JSON with ``<``, ``>`` and ``&`` escaped, so player speech
can never close the script tag; the page script inserts all text with
``textContent``, never as HTML.
"""

from __future__ import annotations

import html
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from zantine.replay.model import build_view, load_records

DATA_PLACEHOLDER = "__REPLAY_DATA__"
TITLE_PLACEHOLDER = "__REPLAY_TITLE__"


def render_html(view: dict[str, Any]) -> str:
    template = files("zantine.replay").joinpath("viewer.html").read_text(encoding="utf-8")
    return template.replace(TITLE_PLACEHOLDER, html.escape(str(view.get("title", "")))).replace(
        DATA_PLACEHOLDER, embed_json(view)
    )


def embed_json(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    for char, escape in (
        ("&", "\\u0026"),
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        text = text.replace(char, escape)
    return text


def write_replay(transcript: str | Path, out: str | Path) -> dict[str, Any]:
    """Read a JSONL transcript and write the replay page; returns the view model."""
    view = build_view(load_records(transcript))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(view), encoding="utf-8")
    return view
