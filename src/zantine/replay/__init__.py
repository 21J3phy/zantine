"""Demo replay viewer: steps through a game transcript Round by Round.

For showing games to people; never used for scoring. Reads transcripts only.
"""

from zantine.replay.model import build_view, load_records
from zantine.replay.render import render_html, write_replay

__all__ = ["build_view", "load_records", "render_html", "write_replay"]
