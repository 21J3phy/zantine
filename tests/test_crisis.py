"""Crisis: triggering, cooldown, simultaneous two-room resolution, deadline loss."""

from helpers import events, make_game, obs_of
from zantine.roles import Team
from zantine.rules.actions import CallMeeting, Kill
from zantine.rules.crisis import FixCrisis, TriggerCrisis
from zantine.seats.bots import PlaybookBot


def crisis_game(bots, **config):
    config.setdefault("crisis_cooldown", 0)
    return make_game(bots=bots, **config)


def stations(game) -> list[str]:
    return game.state.ext["crisis"]["active"]["stations"]


def place(game, rooms: dict[int, str]) -> None:
    for seat, room in rooms.items():
        game.state.player(seat).room = room


def invalid_reasons(game, seat: int) -> list[str]:
    return [o["reason"] for o in obs_of(game, seat, "invalid_action")]


def test_only_impostors_can_trigger_a_crisis() -> None:
    game = crisis_game({5: PlaybookBot({1: TriggerCrisis()})})
    game.play_round()
    assert invalid_reasons(game, 5)[0] == "only Impostors can trigger a Crisis"
    assert game.state.ext["crisis"]["active"] is None


def test_trigger_respects_the_cooldown() -> None:
    bot = PlaybookBot({1: TriggerCrisis(), 3: TriggerCrisis()})
    game = crisis_game({0: bot}, crisis_cooldown=2)
    game.play_round()
    assert invalid_reasons(game, 0)[0] == "Crisis cooldown: 2 more Round(s)"
    assert bot.action_contexts[0].extra["crisis"]["cooldown"] == 2
    game.play_round()
    game.play_round()
    assert game.state.ext["crisis"]["active"] is not None
    assert events(game, "crisis.started")[0]["triggered_by"] == [0]


def test_crewmates_do_not_see_the_impostor_cooldown() -> None:
    crew = PlaybookBot()
    game = crisis_game({5: crew})
    game.play_round()
    assert crew.action_contexts[0].extra["crisis"] == {"active": False}


def test_every_living_player_is_told_the_crisis_and_its_deadline() -> None:
    game = crisis_game({0: PlaybookBot({1: Kill(2)}), 1: PlaybookBot({1: TriggerCrisis()})})
    game.play_round()
    started = events(game, "crisis.started")[0]
    assert started["deadline"] == 4 and len(set(started["stations"])) == 2
    assert game.state.map.meeting_room not in started["stations"]
    for p in game.state.players:
        seen = obs_of(game, p.seat, "crisis.started")
        if not p.alive:
            assert seen == []
            continue
        assert seen == [
            {"number": 1, "stations": started["stations"], "deadline": 4, "rounds_left": 3}
        ]
        # The trigger is anonymous.
        assert "triggered_by" not in seen[0]


def test_active_crisis_appears_in_every_context() -> None:
    crew = PlaybookBot()
    game = crisis_game({0: PlaybookBot({1: TriggerCrisis()}), 5: crew})
    game.play_round()
    game.play_round()
    assert crew.action_contexts[1].extra["crisis"] == {
        "active": True,
        "stations": stations(game),
        "deadline": 4,
    }


def test_both_impostors_triggering_start_one_crisis() -> None:
    game = crisis_game({0: PlaybookBot({1: TriggerCrisis()}), 1: PlaybookBot({1: TriggerCrisis()})})
    game.play_round()
    started = events(game, "crisis.started")
    assert len(started) == 1 and started[0]["triggered_by"] == [0, 1]


def test_cannot_trigger_while_a_crisis_is_active() -> None:
    game = crisis_game({0: PlaybookBot({1: TriggerCrisis(), 2: TriggerCrisis()})})
    game.play_round()
    game.play_round()
    assert invalid_reasons(game, 0)[0] == "a Crisis is already active"


def test_fixing_outside_a_station_or_without_a_crisis_is_invalid() -> None:
    game = crisis_game({5: PlaybookBot({1: FixCrisis(), 2: FixCrisis()})})
    game.play_round()
    assert invalid_reasons(game, 5)[0] == "there is no active Crisis"
    game = crisis_game({0: PlaybookBot({1: TriggerCrisis()}), 5: PlaybookBot({2: FixCrisis()})})
    game.play_round()
    game.play_round()  # seat 5 is still in the meeting room, never a station
    assert invalid_reasons(game, 5)[0].startswith("no Crisis station in Cafeteria")


def test_two_players_in_the_two_stations_resolve_it_in_the_same_round() -> None:
    game = crisis_game(
        {
            0: PlaybookBot({5: TriggerCrisis()}),
            5: PlaybookBot({7: FixCrisis()}),
            6: PlaybookBot({7: FixCrisis()}),
        },
        crisis_cooldown=4,
    )
    for _ in range(5):
        game.play_round()
    a, b = stations(game)
    place(game, {5: a, 6: b})
    game.play_round()
    game.play_round()
    assert game.state.ext["crisis"]["active"] is None
    assert events(game, "crisis.resolved") == [{"number": 1, "fixers": [5, 6]}]
    assert obs_of(game, 9, "crisis.resolved") == [{"number": 1}]
    assert obs_of(game, 5, "crisis.fix_attempted") == [{"room": a, "resolved": True}]
    # The cooldown restarts once the Crisis is over.
    assert game.state.ext["crisis"]["cooldown"] == 4
    assert not game.state.over


def test_fixes_in_different_rounds_do_not_resolve_it() -> None:
    game = crisis_game(
        {
            0: PlaybookBot({1: TriggerCrisis()}),
            5: PlaybookBot({2: FixCrisis()}),
            6: PlaybookBot({3: FixCrisis()}),
        }
    )
    game.play_round()
    a, b = stations(game)
    place(game, {5: a, 6: b})
    for _ in range(3):
        game.play_round()
    assert obs_of(game, 5, "crisis.fix_attempted") == [{"room": a, "resolved": False}]
    assert game.state.winner is Team.IMPOSTORS and game.state.win_reason == "crisis"


def test_two_players_in_the_same_station_do_not_resolve_it() -> None:
    game = crisis_game(
        {
            0: PlaybookBot({1: TriggerCrisis()}),
            5: PlaybookBot({2: FixCrisis()}),
            6: PlaybookBot({2: FixCrisis()}),
        }
    )
    game.play_round()
    a, _ = stations(game)
    place(game, {5: a, 6: a})
    game.play_round()
    assert game.state.ext["crisis"]["active"] is not None
    assert events(game, "crisis.resolved") == []


def test_a_killed_fixer_does_not_count() -> None:
    game = crisis_game(
        {
            0: PlaybookBot({1: TriggerCrisis()}),
            1: PlaybookBot({2: Kill(5)}),
            5: PlaybookBot({2: FixCrisis()}),
            6: PlaybookBot({2: FixCrisis()}),
        },
        kill_cooldown=0,
    )
    game.play_round()
    a, b = stations(game)
    place(game, {1: a, 5: a, 6: b})
    game.play_round()
    assert events(game, "kill")[0]["victim"] == 5
    assert game.state.ext["crisis"]["active"] is not None


def test_a_fix_in_the_deadline_round_still_counts() -> None:
    game = crisis_game(
        {
            0: PlaybookBot({1: TriggerCrisis()}),
            5: PlaybookBot({4: FixCrisis()}),
            6: PlaybookBot({4: FixCrisis()}),
        }
    )
    game.play_round()
    place(game, dict(zip((5, 6), stations(game), strict=True)))
    for _ in range(4):
        game.play_round()
    assert not game.state.over
    game.play_round()
    assert not game.state.over


def test_an_unresolved_crisis_is_an_impostor_win() -> None:
    game = crisis_game({0: PlaybookBot({2: TriggerCrisis()})})
    game.run()
    assert game.state.winner is Team.IMPOSTORS
    assert game.state.win_reason == "crisis"
    assert game.state.round == 5
    assert events(game, "crisis.failed") == [{"number": 1, "stations": stations(game)}]
    assert obs_of(game, 7, "crisis.failed") == [{"number": 1}]
    assert game.transcript.records[-1]["reason"] == "crisis"


def test_a_meeting_does_not_pause_or_end_the_crisis() -> None:
    game = crisis_game({0: PlaybookBot({1: TriggerCrisis()}), 5: PlaybookBot({2: CallMeeting()})})
    game.run()
    assert game.state.meetings_held == 1
    assert (game.state.round, game.state.win_reason) == (4, "crisis")


def test_stations_come_from_the_seed() -> None:
    def started(seed: int) -> list[str]:
        game = crisis_game({0: PlaybookBot({1: TriggerCrisis()})}, seed=seed)
        game.play_round()
        return stations(game)

    assert started(3) == started(3)
    assert len({tuple(started(s)) for s in range(10)}) > 1
