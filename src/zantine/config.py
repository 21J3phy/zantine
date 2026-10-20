"""Game parameters. Anything an experiment might vary belongs here."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class GameConfig:
    seed: int = 0
    num_seats: int = 10
    num_impostors: int = 2
    # Rooms on the seeded map (see scenario.generate_map).
    num_rooms: int = 8
    round_cap: int = 30
    # Rounds an Impostor must wait between kills; also the cooldown at game
    # start and after every Meeting.
    kill_cooldown: int = 3
    emergency_meetings_per_seat: int = 1
    meeting_passes: int = 3
    # Talk Budget, in characters, per Meeting pass and per Round of Room Chat;
    # longer speech is truncated. Equal for every Seat (see ``talk_budget``).
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
    # Shared Tasks: one multi-seat Task per entry (tasks.graph.place_shared_tasks).
    shared_task_kinds: tuple[str, ...] = ("split_key",)
    # Split-key (rules/split_key.py): the code must be entered within this many
    # Rounds after the Round its first part is read.
    split_key_window: int = 4
    # Fixed Impostor seats (tests). None = drawn from the seed.
    impostor_seats: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if not 0 < self.num_impostors < self.num_seats:
            raise ValueError("need at least one Impostor and one Crewmate")
        if self.tasks_per_crewmate < 0 or not 0 <= self.task_dependency_chance <= 1:
            raise ValueError("bad task settings")
        if self.split_key_window < 1 or self.talk_budget_chars < 0:
            raise ValueError("bad split-key window or Talk Budget")
        if self.impostor_seats is not None:
            seats = self.impostor_seats
            if len(set(seats)) != self.num_impostors or not all(
                0 <= s < self.num_seats for s in seats
            ):
                raise ValueError(f"bad impostor_seats {seats!r}")

    def talk_budget(self, seat: int) -> int:
        """The Talk Budget of ``seat``: the same for every Seat in v1."""
        return self.talk_budget_chars

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
