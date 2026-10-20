from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    CREWMATE = "crewmate"
    IMPOSTOR = "impostor"


class Team(StrEnum):
    """The side that wins a game."""

    CREW = "crew"
    IMPOSTORS = "impostors"
    # Round cap reached with no Impostor left in play (after Containment, or in
    # a Benign Match): nobody wins.
    NONE = "none"
