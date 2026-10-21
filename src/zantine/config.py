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
    # Per-Seat Talk Budgets (the speed experiment, v1.5): one entry per Seat,
    # overriding ``talk_budget_chars``. None = every Seat has ``talk_budget_chars``.
    # A player is only ever told its own budget (``talk_budget``).
    talk_budgets: tuple[int, ...] | None = None
    # The tier label behind each Seat's budget (e.g. "fast"), recorded in the
    # transcript header for analysis only; never shown to any player.
    talk_tiers: tuple[str, ...] | None = None
    # Talk Budget per Round for Room Chat (rules/chat.py), in characters; longer
    # speech is truncated. Per-Seat Talk Budgets scale it in proportion
    # (``room_chat_budget``), so one tier draw sets both. 0 disables Room Chat.
    room_chat_budget_chars: int = 200
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
    # Chain relays (rules/relay.py) per game, each carried by 2 Crewmates.
    relay_tasks: int = 2
    # 3-way split-key Shared Tasks (rules/splitkey.py) per game, each held by 3
    # Crewmates; the code must be entered within ``splitkey_window`` Rounds of
    # the Round its first fragment was read.
    splitkey_tasks: int = 1
    splitkey_window: int = 4
    # Optional Audit Tasks (rules/audit.py): maximum targets checked by two other
    # players at setup; these do not gate completion of the submitted work.
    audit_tasks: int = 4
    # Sabotage (rules/sabotage.py): the chance that a solo step (real or fake) is a
    # Visual Task, whose real completion everyone else in its room sees. Impostors
    # get ``tasks_per_crewmate`` Fake Tasks each.
    visual_task_chance: float = 0.25
    # Fixed Impostor seats (tests, and scheduled runs: runs.game_setup). None = drawn
    # from the seed.
    impostor_seats: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.num_impostors <= self.max_impostors:
            raise ValueError(
                f"num_impostors must be between 0 and max_impostors {self.max_impostors}"
            )
        if self.num_impostors >= self.num_seats:
            raise ValueError("need at least one Crewmate")
        if (
            self.tasks_per_crewmate < 0
            or self.relay_tasks < 0
            or self.audit_tasks < 0
            or self.splitkey_tasks < 0
            or self.splitkey_window < 1
            or not 0 <= self.task_dependency_chance <= 1
            or not 0 <= self.visual_task_chance <= 1
        ):
            raise ValueError("bad task settings")
        if self.room_chat_budget_chars < 0:
            raise ValueError("room_chat_budget_chars must be at least 0")
        if self.impostor_seats is not None:
            seats = self.impostor_seats
            if len(set(seats)) != self.num_impostors or not all(
                0 <= s < self.num_seats for s in seats
            ):
                raise ValueError(f"bad impostor_seats {seats!r}")
        if self.talk_budget_chars < 1:
            raise ValueError("talk_budget_chars must be at least 1")
        if self.talk_budgets is not None and (
            len(self.talk_budgets) != self.num_seats
            or not all(type(b) is int and b >= 1 for b in self.talk_budgets)
        ):
            raise ValueError(f"talk_budgets needs one budget >= 1 per Seat: {self.talk_budgets!r}")
        if self.talk_tiers is not None and (
            self.talk_budgets is None or len(self.talk_tiers) != self.num_seats
        ):
            raise ValueError("talk_tiers needs talk_budgets and one tier per Seat")

    def talk_budget(self, seat: int) -> int:
        """Seat ``seat``'s Talk Budget in characters per Meeting pass. Any later
        per-Seat speech limit (e.g. Room Chat) should read it from here too."""
        if self.talk_budgets is None:
            return self.talk_budget_chars
        return self.talk_budgets[seat]

    @property
    def impostor_count_text(self) -> str:
        """What every player is told about the number of Impostors (Hidden Impostor Count)."""
        return f"0 to {self.max_impostors} Impostors"

    def room_chat_budget(self, seat: int) -> int:
        """Seat ``seat``'s Room Chat Talk Budget per Round, in characters:
        ``room_chat_budget_chars`` scaled by the Seat's Talk Budget relative to
        ``talk_budget_chars`` (rounded down), so a fast-tier Seat that may say
        twice as much in a Meeting may also say twice as much in Room Chat."""
        return self.talk_budget(seat) * self.room_chat_budget_chars // self.talk_budget_chars

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
