from helpers import events, make_game, obs_of
from zantine.rules.actions import CallMeeting, Kill
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


def test_both_impostors_ejected_is_a_crew_win() -> None:
    bots = {seat: PlaybookBot(votes={1: 0, 2: 1}) for seat in range(10)}
    bots[3].actions[1] = CallMeeting()
    bots[4].actions[2] = CallMeeting()
    game = make_game(bots=bots)
    game.play_round()
    assert not game.state.over  # one Impostor left
    game.play_round()
    assert (game.state.winner, game.state.win_reason) == ("crew", "impostors_ejected")
    assert [e["seat"] for e in events(game, "ejection")] == [0, 1]


def test_round_cap_is_an_impostor_win() -> None:
    game = make_game(round_cap=5)
    game.run()
    assert (game.state.winner, game.state.win_reason, game.state.round) == (
        "impostors",
        "round_cap",
        5,
    )


def test_meeting_in_the_final_round_beats_the_round_cap() -> None:
    bots = {seat: PlaybookBot(votes={1: 0}) for seat in range(10)}
    bots[3].actions[1] = CallMeeting()
    game = make_game(bots=bots, impostors=(0,), num_impostors=1, round_cap=1)
    game.run()
    assert (game.state.winner, game.state.win_reason) == ("crew", "impostors_ejected")


def test_play_round_after_game_over_raises() -> None:
    game = make_game(round_cap=1)
    game.run()
    try:
        game.play_round()
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError")
