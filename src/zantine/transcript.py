"""Machine-readable game record (JSON Lines).

One JSON object per line, written with sorted keys so equal games give
byte-identical files. Record kinds:

- ``header``: schema version, seed, config, map, seats (name, role, controller)
- ``event``: ground truth (``type``, ``round``, ``data``), including every
  submitted action, kill, vote and the role of every ejected player
- ``observation``: one player's private view (``seat``, ``type``, ``round``, ``data``)
- ``result``: winner, reason, rounds played
- ``aborted`` / ``cost``: written by live runs (``harness.live``)

Every record carries a ``seq`` number giving its position in the game.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from zantine.observations import Observation

SCHEMA_VERSION = 1


class Transcript:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def _append(self, record: dict[str, Any]) -> None:
        record["seq"] = len(self.records)
        self.records.append(record)

    def header(self, **data: Any) -> None:
        self._append({"kind": "header", "schema": SCHEMA_VERSION, **data})

    def event(self, round: int, type: str, /, **data: Any) -> None:
        self._append({"kind": "event", "round": round, "type": type, "data": data})

    def observation(self, obs: Observation) -> None:
        self._append(
            {
                "kind": "observation",
                "round": obs.round,
                "seat": obs.seat,
                "type": obs.type,
                "data": obs.data,
            }
        )

    def result(self, **data: Any) -> None:
        self._append({"kind": "result", **data})

    def record(self, kind: str, /, **data: Any) -> None:
        """Any other top-level record (e.g. the live runner's ``cost`` and ``aborted``)."""
        self._append({"kind": kind, **data})

    # Queries -----------------------------------------------------------------

    def events(self, type: str | None = None) -> Iterator[dict[str, Any]]:
        return (
            r for r in self.records if r["kind"] == "event" and (type is None or r["type"] == type)
        )

    def observations(self, seat: int | None = None) -> Iterator[dict[str, Any]]:
        return (
            r
            for r in self.records
            if r["kind"] == "observation" and (seat is None or r["seat"] == seat)
        )

    # Serialization ------------------------------------------------------------

    def to_jsonl(self) -> str:
        return "".join(json.dumps(r, sort_keys=True) + "\n" for r in self.records)

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_jsonl(), encoding="utf-8")

    @staticmethod
    def read(path: str | Path) -> list[dict[str, Any]]:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line]
