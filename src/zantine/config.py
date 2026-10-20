"""Game parameters. Anything an experiment might vary belongs here."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class LeaderConfig:
    """A v1.4 Leader condition (issue #24). The Leader has no extra powers: only
    prompt lines differ (see ``docs/rules.md``, "Leader").

    ``known``: every other player is told who leads (Known Leader), or only the
    Leader is told (Hidden Leader). ``compromised``: True forces the Leader to be
    an Impostor (Compromised Leader), False forces a Crewmate, None keeps
    whatever the normal Impostor draw gives.
    """

    seat: int
    known: bool
    compromised: bool | None = None

    @property
    def condition(self) -> str | None:
        """``known_crew``, ``hidden_crew``, ``known_compromised`` or ``hidden_compromised``."""
        return None if self.compromised is None else condition_name(self.known, self.compromised)


def condition_name(known: bool, compromised: bool) -> str:
    """One of the four v1.4 Leader conditions."""
    return f"{'known' if known else 'hidden'}_{'compromised' if compromised else 'crew'}"


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
    # Optional Audit Tasks (rules/audit.py): maximum targets checked by two other
    # Crewmates at setup; these do not gate completion of the submitted work.
    audit_tasks: int = 4
    # Fixed Impostor seats (tests, and scheduled runs: runs.game_setup). None = drawn
    # from the seed.
    impostor_seats: tuple[int, ...] | None = None
    # v1.4 Leader condition; None = no Leader (v1). A dict is accepted (manifests, JSON).
    leader: LeaderConfig | None = None

    def __post_init__(self) -> None:
        if isinstance(self.leader, dict):
            object.__setattr__(self, "leader", LeaderConfig(**self.leader))
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
            or not 0 <= self.task_dependency_chance <= 1
        ):
            raise ValueError("bad task settings")
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
        leader = self.leader
        if leader is not None:
            if not 0 <= leader.seat < self.num_seats:
                raise ValueError(f"bad leader seat {leader.seat!r}")
            if leader.compromised and self.num_impostors == 0:
                raise ValueError("a Compromised Leader needs at least one Impostor")
            if (
                self.impostor_seats is not None
                and leader.compromised is not None
                and (leader.seat in self.impostor_seats) != leader.compromised
            ):
                raise ValueError("impostor_seats contradict the Leader's forced role")

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
