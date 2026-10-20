"""Simultaneous action resolution (see docs/rules.md)."""

from helpers import events, make_game, obs_of
from zantine.rules.actions import CallMeeting, Kill, Move, Report, Wait
from zantine.seats.bots import PlaybookBot
from zantine.state import Body


def test_kill_beats_move_victim_dies_in_start_room() -> None:
    game = make_game(
        bots={0: PlaybookBot({1: Kill(2)}), 2: PlaybookBot({1: Move("Electrical")})},
        rooms={0: "Storage", 2: "Storage"},
        kill_cooldown=0,
    )
    game.play_round()
    victim = game.state.player(2)
    assert not victim.alive
    assert victim.room == "Storage"
    assert [(b.victim, b.room) for b in game.state.bodies] == [(2, "Storage")]
    assert game.state.player(0).room == "Storage"  # killing is the killer's action
    assert obs_of(game, 2, "killed") == [{"room": "Storage", "killers": [0]}]
    assert not events(game, "move")


def test_witnesses_are_players_present_before_movement() -> None:
    game = make_game(
        bots={
            0: PlaybookBot({1: Kill(2)}),
            3: PlaybookBot({1: Move("Navigation")}),  # leaves the kill room
            4: PlaybookBot({1: Move("Storage")}),  # walks in from Cafeteria
        },
        rooms={0: "Storage", 2: "Storage", 3: "Storage"},
        kill_cooldown=0,
    )
    game.play_round()
    assert obs_of(game, 3, "kill_witnessed") == [{"room": "Storage", "killers": [0], "victim": 2}]
    assert obs_of(game, 4, "kill_witnessed") == []
    # The arriving player only sees the aftermath.
    assert obs_of(game, 4, "room_snapshot")[-1] == {
        "room": "Storage",
        "players": [0],
        "bodies": [2],
    }
    assert obs_of(game, 3, "room_snapshot")[-1] == {
        "room": "Navigation",
        "players": [],
        "bodies": [],
    }


def test_two_impostors_killing_the_same_target() -> None:
    game = make_game(
        bots={0: PlaybookBot({1: Kill(5)}), 1: PlaybookBot({1: Kill(5)})},
        rooms={0: "Medbay", 1: "Medbay", 5: "Medbay"},
        kill_cooldown=2,
        impostors=(0, 1),
    )
    game.state.player(0).kill_cooldown = game.state.player(1).kill_cooldown = 0
    game.play_round()
    assert events(game, "kill") == [{"victim": 5, "killers": [0, 1], "room": "Medbay"}]
    assert len(game.state.bodies) == 1
    assert game.state.player(0).kill_cooldown == game.state.player(1).kill_cooldown == 2


def test_two_kills_in_one_room_victims_do_not_witness_each_other() -> None:
    game = make_game(
        bots={0: PlaybookBot({1: Kill(5)}), 1: PlaybookBot({1: Kill(6)})},
        rooms={0: "Medbay", 1: "Medbay", 5: "Medbay", 6: "Medbay", 7: "Medbay"},
        kill_cooldown=0,
    )
    game.play_round()
    assert obs_of(game, 5, "kill_witnessed") == []
    assert len(obs_of(game, 7, "kill_witnessed")) == 2


def test_report_by_a_player_killed_that_round_is_cancelled() -> None:
    game = make_game(
        bots={0: PlaybookBot({1: Kill(2)}), 2: PlaybookBot({1: Report()})},
        rooms={0: "Electrical", 2: "Electrical"},
        kill_cooldown=0,
    )
    game.state.bodies.append(Body(victim=9, room="Electrical", round=0))
    game.state.player(9).alive = False
    game.play_round()
    assert not events(game, "meeting_started")
    assert game.state.meetings_held == 0


def test_kill_and_report_same_round_body_from_this_round_is_also_cleared() -> None:
    game = make_game(
        bots={0: PlaybookBot({1: Kill(2)}), 3: PlaybookBot({1: Report()})},
        rooms={0: "Weapons", 2: "Weapons", 3: "Electrical"},
        kill_cooldown=0,
    )
    game.state.bodies.append(Body(victim=9, room="Electrical", round=0))
    game.state.player(9).alive = False
    game.play_round()
    [started] = events(game, "meeting_started")
    assert (started["caller"], started["body_victim"]) == (3, 9)
    assert events(game, "bodies_cleared") == [{"victims": [2, 9]}]
    assert game.state.bodies == []


def test_report_needs_a_body_present_at_the_start_of_the_round() -> None:
    # Seat 3 tries to report the body that only appears this Round: invalid, then forfeit.
    game = make_game(
        bots={0: PlaybookBot({1: Kill(2)}), 3: PlaybookBot({1: Report()})},
        rooms={0: "Weapons", 2: "Weapons", 3: "Weapons"},
        kill_cooldown=0,
    )
    game.play_round()
    invalid = obs_of(game, 3, "invalid_action")
    assert [o["attempt"] for o in invalid] == [1, 2]
    assert invalid[-1]["forfeited"] is True
    assert game.state.meetings_held == 0


def test_kill_cooldown_is_enforced() -> None:
    game = make_game(
        bots={0: PlaybookBot({1: Kill(2), 3: Kill(3), 4: Kill(3)})},
        rooms={0: "Storage", 2: "Storage", 3: "Storage"},
        kill_cooldown=2,
    )
    game.state.player(0).kill_cooldown = 0
    for _ in range(4):
        game.play_round()
    kills = events(game, "kill")
    assert [k["victim"] for k in kills] == [2, 3]
    assert [r["round"] for r in game.transcript.events("kill")] == [1, 4]
    assert obs_of(game, 0, "invalid_action")[0]["reason"].startswith("kill cooldown")


def test_initial_kill_cooldown_applies_at_game_start() -> None:
    game = make_game(bots={0: PlaybookBot({1: Kill(2)})}, kill_cooldown=3)
    game.play_round()
    assert game.state.player(2).alive


def test_crewmates_cannot_kill_and_impostors_cannot_kill_impostors() -> None:
    game = make_game(
        bots={2: PlaybookBot({1: Kill(3)}), 0: PlaybookBot({1: Kill(1)})}, kill_cooldown=0
    )
    game.play_round()
    assert all(p.alive for p in game.state.players)
    assert obs_of(game, 2, "invalid_action")[0]["reason"] == "only Impostors can kill"
    assert obs_of(game, 0, "invalid_action")[0]["reason"] == "cannot kill an Impostor"


def test_moves_must_follow_corridors_and_swaps_do_not_interact() -> None:
    game = make_game(
        bots={
            2: PlaybookBot({1: Move("Weapons")}),
            3: PlaybookBot({1: Move("Cafeteria")}),
            4: PlaybookBot({1: Move("Electrical")}),  # not adjacent to Cafeteria
        },
        rooms={3: "Weapons"},
    )
    game.play_round()
    assert game.state.player(2).room == "Weapons"
    assert game.state.player(3).room == "Cafeteria"
    assert game.state.player(4).room == "Cafeteria"
    assert obs_of(game, 4, "invalid_action")[-1]["forfeited"] is True


def test_report_beats_emergency_and_lowest_seat_is_credited() -> None:
    game = make_game(
        bots={
            4: PlaybookBot({1: CallMeeting()}),
            6: PlaybookBot({1: Report()}),
            5: PlaybookBot({1: Report()}),
        },
        rooms={5: "Medbay", 6: "Medbay"},
    )
    game.state.bodies.append(Body(victim=9, room="Medbay", round=0))
    game.state.player(9).alive = False
    game.play_round()
    [started] = events(game, "meeting_started")
    assert started["reason"] == "report"
    assert started["caller"] == 5
    assert started["requested_by"] == [5, 6]
    assert game.state.player(4).emergency_meetings_left == 1  # not consumed


def test_emergency_meeting_uses_are_limited() -> None:
    game = make_game(bots={4: PlaybookBot({1: CallMeeting(), 2: CallMeeting()})})
    game.play_round()
    game.play_round()
    assert game.state.meetings_held == 1
    assert obs_of(game, 4, "invalid_action")[0]["reason"] == "no emergency meetings left"


def test_wait_is_always_valid() -> None:
    game = make_game(bots={2: PlaybookBot({1: Wait()})})
    game.play_round()
    assert obs_of(game, 2, "invalid_action") == []
