"""Meetings: discussion passes, the secret vote, and Ejection."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from zantine.observations import Statement
from zantine.vision import emit_room_snapshots

if TYPE_CHECKING:
    from zantine.engine import Game
    from zantine.rules.registry import MeetingRequest


@dataclass(frozen=True)
class Tally:
    counts: dict[int, int]  # seat -> votes received
    skips: int
    ejected: int | None


def tally(votes: Mapping[int, int | None]) -> Tally:
    """Plurality ejects. A tie for most votes, or skip matching or beating the
    leader, ejects nobody."""
    counts = Counter(v for v in votes.values() if v is not None)
    skips = sum(v is None for v in votes.values())
    top = max(counts.values(), default=0)
    leaders = [seat for seat, n in counts.items() if n == top]
    ejected = leaders[0] if top > 0 and len(leaders) == 1 and top > skips else None
    return Tally(dict(sorted(counts.items())), skips, ejected)


def run_meeting(game: Game, request: MeetingRequest) -> None:
    state, config = game.state, game.config
    state.meetings_held += 1
    meeting = state.meetings_held
    living = [p.seat for p in state.living()]
    dead = sorted(p.seat for p in state.players if not p.alive)
    announcement = {
        "meeting": meeting,
        "reason": request.reason,
        "caller": request.caller,
        "body_victim": request.body_victim,
        "body_room": request.body_room,
    }
    game.event("meeting_started", requested_by=list(request.requested_by), **announcement)
    for seat in living:
        game.observe(seat, "meeting_called", dead=dead, **announcement)
    if state.bodies:
        game.event("bodies_cleared", victims=sorted(b.victim for b in state.bodies))
        state.bodies.clear()

    # Discussion: each pass re-shuffles the speaking order.
    statements: list[Statement] = []
    for pass_index in range(1, config.meeting_passes + 1):
        order = game.meeting_rng.shuffled(living)
        game.event("speaking_order", meeting=meeting, pass_index=pass_index, order=order)
        for position, seat in enumerate(order):
            ctx = game.meeting_context(seat, meeting, statements, pass_index)
            raw = game.controllers[seat].speak(ctx)
            text = raw if isinstance(raw, str) else ""
            truncated = len(text) > config.talk_budget_chars
            text = text[: config.talk_budget_chars]
            stmt = Statement(meeting, pass_index, position, seat, text)
            statements.append(stmt)
            game.event(
                "statement",
                meeting=meeting,
                pass_index=pass_index,
                order=position,
                speaker=seat,
                text=text,
                truncated=truncated,
            )
            for listener in living:
                game.observe(
                    listener,
                    "statement",
                    meeting=meeting,
                    pass_index=pass_index,
                    order=position,
                    speaker=seat,
                    text=text,
                )

    # Secret simultaneous vote: every voter sees the same statements and no votes.
    living_set = set(living)
    votes: dict[int, int | None] = {}
    for seat in living:
        controller = game.controllers[seat]
        votes[seat] = game.ask(
            seat,
            "vote",
            prompt=lambda retry, seat=seat, controller=controller: controller.vote(
                game.meeting_context(seat, meeting, statements, None, retry)
            ),
            validate=lambda v: (
                None
                if v is None or (type(v) is int and v in living_set)
                else f"can only vote for a living player or skip, not {v!r}"
            ),
            encode=lambda v: v if v is None or type(v) is int else repr(v),
            fallback=None,
            meeting=meeting,
        )

    result = tally(votes)
    counts = [{"seat": s, "votes": n} for s, n in result.counts.items()]
    game.event(
        "vote_tally", meeting=meeting, counts=counts, skips=result.skips, ejected=result.ejected
    )
    for seat in living:
        game.observe(
            seat,
            "vote_result",
            meeting=meeting,
            counts=counts,
            skips=result.skips,
            ejected=result.ejected,
        )

    if result.ejected is not None:
        ejected = state.player(result.ejected)
        ejected.alive = False
        ejected.ejected = True
        # Ground truth records the role; players are never told it.
        game.event("ejection", meeting=meeting, seat=ejected.seat, role=ejected.role.value)
        for seat in living:
            game.observe(seat, "ejected", meeting=meeting, seat=ejected.seat)

    # Everyone returns to the meeting room; kill cooldowns restart.
    for p in state.living():
        p.room = state.map.meeting_room
        if p.is_impostor:
            p.kill_cooldown = config.kill_cooldown
    game.event("meeting_ended", meeting=meeting)
    for module in game.rules.modules:
        module.on_meeting_end(game, meeting)
    for p in state.living():
        game.observe(p.seat, "returned_to_meeting_room", room=state.map.meeting_room)
    emit_room_snapshots(game)
