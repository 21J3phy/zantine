"""Extension points: rule modules, action parsing, controller specs."""

from dataclasses import dataclass
from typing import ClassVar

import pytest

from helpers import events, make_game, obs_of
from zantine.config import GameConfig
from zantine.engine import Game
from zantine.rng import GameRng
from zantine.roles import Team
from zantine.rules import GameModule, RoundContext, Rules, default_rules
from zantine.rules.actions import Action, Invalid, Kill, Move
from zantine.rules.core import CoreModule
from zantine.seats.bots import PlaybookBot
from zantine.seats.registry import make_controllers, parse_spec


@dataclass(frozen=True)
class Dance(Action):
    kind: ClassVar[str] = "dance.dance"
    style: str = "waltz"

    def validate(self, state, actor) -> str | None:
        return None if self.style in {"waltz", "tango"} else f"unknown style {self.style!r}"


class DanceModule(GameModule):
    """A toy plug-in: dancing three times wins the game for the crew."""

    name = "dance"

    def install(self, rules: Rules) -> None:
        rules.register_action(Dance)
        rules.add_resolver(400, "dance.count", self.resolve)
        rules.add_win_condition(300, "dance.enough", self.win)

    def on_setup(self, game: Game) -> None:
        game.state.ext["dance"] = {"count": 0}

    def resolve(self, game: Game, rc: RoundContext) -> None:
        for seat, action in rc.acting(Dance):
            game.state.ext["dance"]["count"] += 1
            game.event("dance.danced", seat=seat, style=action.style)
            for p in game.state.living_in(game.state.player(seat).room):
                game.observe(p.seat, "dance.seen", dancer=seat)

    def win(self, game: Game):
        return (Team.CREW, "dance.enough") if game.state.ext["dance"]["count"] >= 3 else None

    def context_extra(self, game: Game, seat: int):
        return {"count": game.state.ext["dance"]["count"]}


def test_a_plugin_module_adds_actions_resolvers_wins_and_context() -> None:
    bot = PlaybookBot({1: Dance(), 2: Dance("tango"), 3: Dance("disco"), 4: Dance()})
    cfg = GameConfig(seed=1, impostor_seats=(0, 1))
    controllers = [bot if s == 5 else PlaybookBot() for s in range(10)]
    game = Game(cfg, controllers, rules=default_rules([CoreModule(), DanceModule()]))
    game.run()
    assert game.state.win_reason == "dance.enough"
    assert game.state.round == 4
    assert [e["style"] for e in events(game, "dance.danced")] == ["waltz", "tango", "waltz"]
    assert bot.action_contexts[1].extra == {"dance": {"count": 1}}
    assert len(obs_of(game, 7, "dance.seen")) == 3
    assert game.transcript.records[0]["modules"] == ["core", "dance"]


def test_resolvers_run_in_order() -> None:
    names = [s.name for s in default_rules().resolvers()]
    assert [n for n in names if n.startswith("core.")] == [
        "core.kill",
        "core.move",
        "core.meeting_request",
        "core.cooldowns",
        "core.snapshots",
    ]
    # Plug-ins run between meeting requests and cooldowns.
    assert names.index("core.meeting_request") < names.index("crisis.resolve")
    assert names.index("crisis.resolve") < names.index("core.cooldowns")
    assert names.index("core.meeting_request") < names.index("tasks.work")
    assert names.index("tasks.work") < names.index("core.cooldowns")


def test_installing_a_module_twice_fails() -> None:
    rules = default_rules()
    with pytest.raises(ValueError):
        rules.install(CoreModule())


def test_parse_action_round_trip_and_bad_input() -> None:
    rules = default_rules()
    for action in (Move("Weapons"), Kill(3)):
        assert rules.parse_action(action.to_dict()) == action
    assert isinstance(rules.parse_action({"kind": "fly"}), Invalid)
    assert isinstance(rules.parse_action({"kind": "move", "to": "X", "speed": 2}), Invalid)


def test_invalid_action_from_a_harness_is_retried_then_forfeited() -> None:
    bad = Invalid(reason="could not parse", raw="blah")
    game = make_game(bots={3: PlaybookBot({1: bad})})
    game.play_round()
    invalid = obs_of(game, 3, "invalid_action")
    assert [(o["attempt"], o["forfeited"]) for o in invalid] == [(1, False), (2, True)]
    assert invalid[0]["reason"] == "could not parse"


def test_bot_spec_parsing() -> None:
    assert parse_spec("scripted", 3) == ["scripted"] * 3
    assert parse_spec("scripted*2,idle", 3) == ["scripted", "scripted", "idle"]
    with pytest.raises(ValueError):
        parse_spec("scripted*2", 3)
    with pytest.raises(ValueError):
        make_controllers("nope", 1, GameRng(0))
