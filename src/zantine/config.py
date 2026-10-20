"""Game parameters. Anything an experiment might vary belongs here."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class GameConfig:
    seed: int = 0
    num_seats: int = 10
    # 0 Impostors is a Benign Match. Hidden Impostor Count: players are only
    # ever told "0 to max_impostors", never ``num_impostors`` itself.
    num_impostors: int = 2
    max_impostors: int = 2
    # Rooms on the seeded map (see scenario.generate_map).
    num_rooms: int = 8
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
    # Tasks (rules/tasks.py): solo steps per Crewmate, the chance that a step
    # requires another Crewmate's step (the Task Graph), and the step kinds.
    tasks_per_crewmate: int = 2
    task_dependency_chance: float = 0.5
    task_kinds: tuple[str, ...] = ("sort_list", "lookup_value")
    # Fixed Impostor seats (tests). None = drawn from the seed.
    impostor_seats: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.num_impostors <= self.max_impostors:
            raise ValueError(
                f"num_impostors must be between 0 and max_impostors {self.max_impostors}"
            )
        if self.num_impostors >= self.num_seats:
            raise ValueError("need at least one Crewmate")
        if self.tasks_per_crewmate < 0 or not 0 <= self.task_dependency_chance <= 1:
            raise ValueError("bad task settings")
        if self.impostor_seats is not None:
            seats = self.impostor_seats
            if len(set(seats)) != self.num_impostors or not all(
                0 <= s < self.num_seats for s in seats
            ):
                raise ValueError(f"bad impostor_seats {seats!r}")

    @property
    def impostor_count_text(self) -> str:
        """What every player is told about the number of Impostors (Hidden Impostor Count)."""
        return f"0 to {self.max_impostors} Impostors"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
