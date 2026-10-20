from helpers import events, make_game, obs_of
from zantine.rules.actions import CallMeeting, Kill
from zantine.rules.tasks import board
from zantine.seats.bots import PlaybookBot


def _kill_off(game, seats) -> None:
    for seat in seats:
        game.state.player(seat).alive = False


def test_parity_after_a_kill_ends_the_game_before_any_meeting() -> None:
    bots = {0: PlaybookBot({1: Kill(2)}), 3: PlaybookBot({1: CallMeeting()})}
    game = make_game(bots=bots, rooms={0: "Storage", 2: "Storage"}, kill_cooldown=0)
    _kill_off(game, [5, 6, 7, 8, 9])  # 2 Impostors vs 3 Crewmates
    game.play_round()
    assert (game.state.winner, game.state.win_reason) == ("impostors", "parity")
    assert game.state.meetings_held == 0
    assert game.transcript.records[-1]["kind"] == "result"
    assert obs_of(game, 2, "game_over") == [{"winner": "impostors", "reason": "parity"}]


def test_ejecting_a_crewmate_into_parity_is_an_impostor_win() -> None:
    votes = {seat: 2 for seat in range(10)}
    bots = {seat: PlaybookBot(votes={1: v}) for seat, v in votes.items()}
    bots[3].actions[1] = CallMeeting()
    game = make_game(bots=bots)
    _kill_off(game, [5, 6, 7, 8, 9])
    game.run()
    assert (game.state.winner, game.state.win_reason) == ("impostors", "parity")
    assert game.state.round == 1


def test_ejecting_both_impostors_records_containment_but_play_continues() -> None:
    bots = {seat: PlaybookBot(votes={1: 0, 2: 1}) for seat in range(10)}
    bots[3].actions[1] = CallMeeting()
    bots[4].actions[2] = CallMeeting()
    game = make_game(bots=bots, round_cap=6)
    game.play_round()
    assert events(game, "containment") == []  # one Impostor left
    game.play_round()
    assert [e["seat"] for e in events(game, "ejection")] == [0, 1]
    assert events(game, "containment") == [{"meeting": 2, "impostors": [0, 1]}]
    assert [r["round"] for r in game.transcript.events("containment")] == [2]
    assert not game.state.over
    # Players are never told: it would reveal the Impostor count.
    for seat in range(10):
        assert not [o for o in game.observations_of(seat) if "contain" in o.type]
    game.run()
    assert game.state.round == 6
    result = game.transcript.records[-1]
    assert (result["winner"], result["reason"]) == ("none", "round_cap")
    assert result["containment"] == {"contained": True, "round": 2, "meeting": 2}


def test_all_tasks_done_after_containment_is_a_crew_win() -> None:
    bots = {seat: PlaybookBot(votes={1: 0}) for seat in range(10)}
    bots[3].actions[1] = CallMeeting()
    game = make_game(bots=bots, impostors=(0,), num_impostors=1)
    game.play_round()
    assert events(game, "containment") == [{"meeting": 1, "impostors": [0]}]
    for task in board(game.state).graph:
        task.done = True
    game.play_round()
    assert (game.state.winner, game.state.win_reason, game.state.round) == ("crew", "tasks_done", 2)
    assert game.transcript.records[-1]["containment"]["round"] == 1


def test_round_cap_is_an_impostor_win_while_an_impostor_is_in_play() -> None:
    game = make_game(round_cap=5)
    game.run()
    assert (game.state.winner, game.state.win_reason, game.state.round) == (
        "impostors",
        "round_cap",
        5,
    )
    assert game.transcript.records[-1]["containment"] == {
        "contained": False,
        "round": None,
        "meeting": None,
    }


def test_round_cap_with_no_impostors_has_no_winner() -> None:
    game = make_game(impostors=(), num_impostors=0, round_cap=3)
    game.run()
    assert (game.state.winner, game.state.win_reason) == ("none", "round_cap")
    assert obs_of(game, 5, "game_over") == [{"winner": "none", "reason": "round_cap"}]
    assert events(game, "containment") == []


def test_meeting_in_the_final_round_beats_the_round_cap() -> None:
    votes = {seat: 2 for seat in range(10)}
    bots = {seat: PlaybookBot(votes={1: v}) for seat, v in votes.items()}
    bots[3].actions[1] = CallMeeting()
    game = make_game(bots=bots, round_cap=1)
    _kill_off(game, [5, 6, 7, 8, 9])
    game.run()
    assert (game.state.winner, game.state.win_reason) == ("impostors", "parity")


def test_play_round_after_game_over_raises() -> None:
    game = make_game(round_cap=1)
    game.run()
    try:
        game.play_round()
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError")
