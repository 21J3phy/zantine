"""Game parameters. Anything an experiment might vary belongs here."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class GameConfig:
    seed: int = 0
    num_seats: int = 10
    num_impostors: int = 2
    round_cap: int = 30
    # Rounds an Impostor must wait between kills; also the cooldown at game
    # start and after every Meeting.
    kill_cooldown: int = 3
    emergency_meetings_per_seat: int = 1
    meeting_passes: int = 3
    # Talk Budget per Meeting pass, in characters; longer speech is truncated.
    talk_budget_chars: int = 500
    # Crisis: Rounds the Impostors must wait before the first Crisis and after
    # each resolved one; the crew must resolve it within ``crisis_deadline``
    # Rounds after the Round it was triggered in.
    crisis_cooldown: int = 6
    crisis_deadline: int = 3
    # Fixed Impostor seats (tests). None = drawn from the seed.
    impostor_seats: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if not 0 < self.num_impostors < self.num_seats:
            raise ValueError("need at least one Impostor and one Crewmate")
        if self.impostor_seats is not None:
            seats = self.impostor_seats
            if len(set(seats)) != self.num_impostors or not all(
                0 <= s < self.num_seats for s in seats
            ):
                raise ValueError(f"bad impostor_seats {seats!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
