from helpers import events, make_game, obs_of
from zantine.meeting import tally
from zantine.rules.actions import CallMeeting, Kill
from zantine.seats.bots import PlaybookBot


def test_tally_plurality_ties_and_skips() -> None:
    assert tally({0: 3, 1: 3, 2: 4}).ejected == 3
    assert tally({0: 3, 1: 4, 2: None}).ejected is None  # 1-1 tie
    assert tally({0: 3, 1: 3, 2: 4, 3: 4}).ejected is None  # 2-2 tie
    assert tally({0: 3, 1: 3, 2: None, 3: None}).ejected is None  # skip ties the leader
    assert tally({0: 3, 1: None, 2: None}).ejected is None  # skip wins
    assert tally({0: None, 1: None}).ejected is None
    assert tally({0: 3, 1: 3, 2: None}).ejected == 3
    result = tally({0: 3, 1: 3, 2: 4, 3: None})
    assert (result.counts, result.skips) == ({3: 2, 4: 1}, 1)


def _emergency_game(votes: dict[int, int | None], **kw):
    bots = {
        seat: PlaybookBot(votes={1: vote}, speech={1: f"seat {seat}"})
        for seat, vote in votes.items()
    }
    bots.setdefault(4, PlaybookBot())
    bots[4].actions[1] = CallMeeting()
    return make_game(bots=bots, **kw), bots


def test_three_passes_reshuffled_and_everyone_speaks_once_per_pass() -> None:
    game, _ = _emergency_game({})
    game.play_round()
    orders = [e["order"] for e in events(game, "speaking_order")]
    assert len(orders) == 3
    for order in orders:
        assert sorted(order) == list(range(10))
    assert len({tuple(o) for o in orders}) > 1  # re-shuffled
    statements = events(game, "statement")
    assert len(statements) == 30
    for listener in range(10):
        assert len(obs_of(game, listener, "statement")) == 30


def test_speakers_see_earlier_statements_and_voters_see_no_votes() -> None:
    game, bots = _emergency_game({seat: None for seat in range(10)})
    game.play_round()
    order = events(game, "speaking_order")[0]["order"]
    for position, seat in enumerate(order):
        first_speech_ctx = bots[seat].meeting_contexts[0]
        assert [s.speaker for s in first_speech_ctx.statements] == order[:position]
    for bot in bots.values():
        vote_ctx = bot.meeting_contexts[-1]
        assert vote_ctx.pass_index is None
        assert len(vote_ctx.statements) == 30
        assert not any(o.type == "vote" for o in vote_ctx.observations)


def test_plurality_ejects_and_role_is_never_revealed() -> None:
    votes = {seat: 0 for seat in range(2, 8)} | {0: 5, 1: 5}
    game, _ = _emergency_game(votes)
    game.play_round()
    assert events(game, "ejection") == [{"meeting": 1, "seat": 0, "role": "impostor"}]
    ejected_player = game.state.player(0)
    assert ejected_player.ejected and not ejected_player.alive
    for seat in range(10):
        assert obs_of(game, seat, "ejected") == [{"meeting": 1, "seat": 0}]
        for obs in game.observations_of(seat):
            if obs.type != "role_assigned":
                assert "role" not in obs.data
                assert "impostor" not in str(obs.data)
    result = obs_of(game, 5, "vote_result")[0]
    assert result["ejected"] == 0
    # Anonymous tally: counts only, never who voted for whom.
    assert set(result) == {"meeting", "counts", "skips", "ejected"}


def test_tie_ejects_nobody() -> None:
    votes = {seat: 5 if seat < 5 else 6 for seat in range(10)}  # 5-5, no skips
    game, _ = _emergency_game(votes)
    game.play_round()
    [tally_event] = events(game, "vote_tally")
    assert tally_event["ejected"] is None
    assert not events(game, "ejection")
    assert all(p.alive for p in game.state.players)


def test_invalid_vote_gets_one_retry_then_counts_as_skip() -> None:
    game, _ = _emergency_game({2: 42, 3: "Red"})  # type: ignore[dict-item]
    game.play_round()
    for seat in (2, 3):
        invalid = obs_of(game, seat, "invalid_action")
        assert [o["attempt"] for o in invalid] == [1, 2]
        assert invalid[-1]["what"] == "vote"
    assert events(game, "vote_tally")[0]["skips"] == 10


def test_after_meeting_bodies_cleared_players_regroup_and_cooldowns_reset() -> None:
    bots = {0: PlaybookBot({1: Kill(2)}), 4: PlaybookBot({1: CallMeeting()})}
    game = make_game(bots=bots, rooms={0: "Storage", 2: "Storage", 4: "Weapons"}, kill_cooldown=2)
    game.state.player(0).kill_cooldown = 0
    game.play_round()
    assert game.state.bodies == []
    assert all(p.room == "Cafeteria" for p in game.state.living())
    assert game.state.player(0).kill_cooldown == 2
    [called] = obs_of(game, 5, "meeting_called")
    assert called["reason"] == "emergency" and called["dead"] == [2]
    assert obs_of(game, 5, "room_snapshot")[-1]["room"] == "Cafeteria"


def test_speech_is_truncated_to_the_talk_budget() -> None:
    bots = {4: PlaybookBot({1: CallMeeting()}, speech={1: "x" * 50})}
    game = make_game(bots=bots, talk_budget_chars=10)
    game.play_round()
    mine = [s for s in events(game, "statement") if s["speaker"] == 4]
    assert all(s["text"] == "x" * 10 and s["truncated"] for s in mine)
