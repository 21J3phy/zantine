from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    CREWMATE = "crewmate"
    IMPOSTOR = "impostor"


class Team(StrEnum):
    """The side that wins a game."""

    CREW = "crew"
    IMPOSTORS = "impostors"
