"""A player sees only its own room."""

from typing import Any

from helpers import make_game, obs_of
from zantine.config import GameConfig
from zantine.engine import Game
from zantine.observations import ActionContext, MeetingContext
from zantine.rng import GameRng
from zantine.rules.actions import Action, Kill, Move
from zantine.runner import new_game
from zantine.seats.base import SeatController
from zantine.seats.bots import PlaybookBot, ScriptedBot


class SpyBot(SeatController):
    """Wraps a ScriptedBot and checks every context against the ground truth."""

    kind = "spy"

    def __init__(self, inner: SeatController) -> None:
        self.inner = inner
        self.game: Game | None = None
        self.checked = 0
        self.heard = 0

    def choose_action(self, ctx: ActionContext) -> Action:
        assert self.game is not None
        state = self.game.state
        me = state.player(ctx.seat)
        assert ctx.room == me.room
        assert set(ctx.players_here) == {p.seat for p in state.living_in(me.room)} - {ctx.seat}
        assert set(ctx.bodies_here) == {b.victim for b in state.bodies_in(me.room)}
        snapshot = [o for o in ctx.observations if o.type == "room_snapshot"][-1].data
        assert snapshot == {
            "room": ctx.room,
            "players": list(ctx.players_here),
            "bodies": list(ctx.bodies_here),
        }
        # Room Chat was heard only in the room the player ended that Round in.
        # (A Round's first snapshot is its end; a Meeting adds a later one.)
        rooms: dict[int, str] = {}
        for o in ctx.observations:
            if o.type == "room_snapshot":
                rooms.setdefault(o.round, o.data["room"])
        for o in ctx.observations:
            if o.type == "chat.message":
                assert o.data["room"] == rooms[o.round]
                self.heard += 1
        self.checked += 1
        return self.inner.choose_action(ctx)

    def say(self, ctx: ActionContext) -> str:
        return self.inner.say(ctx)

    def speak(self, ctx: MeetingContext) -> str:
        return self.inner.speak(ctx)

    def vote(self, ctx: MeetingContext) -> int | None:
        return self.inner.vote(ctx)


def test_every_context_matches_only_the_players_own_room() -> None:
    # The games without solo steps get split-key holders talking in Room Chat early.
    configs = [GameConfig(seed=s) for s in range(5)]
    configs += [GameConfig(seed=s, tasks_per_crewmate=0) for s in range(2)]
    heard = 0
    for config in configs:
        game = new_game(config)
        spies = [SpyBot(ScriptedBot(GameRng(config.seed).fork(f"s{i}"))) for i in range(10)]
        game.controllers = list(spies)
        for spy in spies:
            spy.game = game
        game.run()
        assert sum(s.checked for s in spies) > 50
        heard += sum(s.heard for s in spies)
    assert heard > 0


def test_events_in_other_rooms_are_invisible() -> None:
    # Seat 5 waits in Cafeteria; a kill and moves happen elsewhere.
    game = make_game(
        bots={
            0: PlaybookBot({1: Kill(2)}),
            3: PlaybookBot({1: Move("Navigation"), 2: Move("Storage")}),
        },
        rooms={0: "Electrical", 2: "Electrical", 3: "Weapons"},
        kill_cooldown=0,
    )
    for seat in (1, 4, 6, 7, 8, 9):
        game.state.player(seat).room = "Medbay"
    game.play_round()
    game.play_round()
    observed = _mentioned_seats(game, 5)
    assert observed.isdisjoint({0, 2, 3})
    assert obs_of(game, 5, "kill_witnessed") == []
    assert all(o["room"] == "Cafeteria" for o in obs_of(game, 5, "room_snapshot"))


def test_only_impostors_learn_who_the_impostors_are() -> None:
    game = make_game()
    game.start()
    count = "0 to 2 Impostors"
    assert obs_of(game, 0, "role_assigned") == [
        {"role": "impostor", "fellow_impostors": [1], "impostor_count": count}
    ]
    assert obs_of(game, 5, "role_assigned") == [
        {"role": "crewmate", "fellow_impostors": [], "impostor_count": count}
    ]
    ctx = game.action_context(game.state.player(5))
    assert ctx.kill_cooldown is None


def _mentioned_seats(game: Game, seat: int) -> set[int]:
    out: set[int] = set()

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, int) and key in {"players", "killers", "victim", "bodies"}:
            out.add(value)

    for obs in game.observations_of(seat):
        walk(obs.data)
    return out
