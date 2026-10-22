"""What a single player is allowed to know.

Seat controllers never see ``GameState``. They get a context object built from
the start-of-Round state of their own room plus their private observation log.
Every observation handed to a player is also written to the transcript, so the
transcript holds each player's exact view.

Observation ``type`` values used by the core engine (``data`` keys in brackets):

- ``role_assigned`` [role, fellow_impostors, impostor_count]: ``impostor_count``
  is "0 to {max_impostors} Impostors" (Hidden Impostor Count), never the real number
- ``leader_assigned`` [leader, known]: v1.4 only. The Leader is told it leads
  (``known`` says whether the others were told too); with a Known Leader every
  other player is told who leads. With a Hidden Leader nobody else gets one.
- ``room_snapshot`` [room, players, bodies]: end-of-Round view of own room
- ``kill_witnessed`` [room, killers, victim]: you were in the room when it happened
- ``killed`` [room, killers]: you were killed
- ``invalid_action`` [what, attempt, reason, forfeited]: ``what`` is action or vote
- ``meeting_called`` [meeting, reason, caller, body_victim, body_room, dead]
- ``statement`` [meeting, pass, order, speaker, text]: under the Style Mask, ``text``
  is the checked rewrite; the original is ground truth only
- ``vote_result`` [meeting, counts, skips, ejected]: anonymous tally
- ``ejected`` [meeting, seat]: never includes the role
- ``returned_to_meeting_room`` [room]
- ``game_over`` [winner, reason]

Plug-ins add their own ``type`` values; prefix them with the module name
(e.g. ``tasks.graded``) to avoid collisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from zantine.roles import Role


@dataclass(frozen=True)
class Observation:
    round: int
    seat: int
    type: str
    data: dict[str, Any]


@dataclass(frozen=True)
class LeaderView:
    """What a player was told about the Leader (v1.4): who leads, and whether
    every player was told. Built from its ``leader_assigned`` observation."""

    seat: int
    known: bool


@dataclass(frozen=True)
class ActionContext:
    """Everything a player knows when choosing an action for a Round."""

    seat: int
    name: str
    role: Role
    round: int
    room: str
    adjacent_rooms: tuple[str, ...]
    # Other living players and bodies in this room at the start of the Round.
    players_here: tuple[int, ...]
    bodies_here: tuple[int, ...]
    # None for Crewmates.
    kill_cooldown: int | None
    emergency_meetings_left: int
    roster: dict[int, str]
    observations: tuple[Observation, ...]
    # Hidden Impostor Count: what every player is told, never the real number.
    impostor_count: str = "0 to 2 Impostors"
    # Set on the single retry after an Invalid Action.
    retry_reason: str | None = None
    # Fields added by plug-in modules, keyed by module name.
    extra: dict[str, Any] = field(default_factory=dict)
    # v1.4: set only if this player was told who leads (always the Leader itself).
    leader: LeaderView | None = None


@dataclass(frozen=True)
class Statement:
    meeting: int
    pass_index: int
    order: int
    speaker: int
    text: str


@dataclass(frozen=True)
class MeetingContext:
    """Everything a player knows when speaking or voting in a Meeting."""

    seat: int
    name: str
    role: Role
    round: int
    meeting: int
    living: tuple[int, ...]
    roster: dict[int, str]
    # Statements made so far in this Meeting, in speaking order.
    statements: tuple[Statement, ...]
    observations: tuple[Observation, ...]
    # Current discussion pass (1-based); None while voting.
    pass_index: int | None
    talk_budget_chars: int
    impostor_count: str = "0 to 2 Impostors"
    retry_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    leader: LeaderView | None = None
