"""Core rules: wait, move, kill, report, emergency Meeting.

The simultaneous-resolution rules implemented here are documented in
``docs/rules.md``; keep the two in sync.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from zantine.rules.actions import CallMeeting, Kill, Move, Report, Wait
from zantine.rules.registry import GameModule, MeetingRequest, RoundContext, Rules
from zantine.rules.win import impostors_ejected, parity
from zantine.state import Body
from zantine.vision import emit_room_snapshots

if TYPE_CHECKING:
    from zantine.engine import Game


class CoreModule(GameModule):
    name = "core"

    def install(self, rules: Rules) -> None:
        for cls in (Wait, Move, Kill, Report, CallMeeting):
            rules.register_action(cls)
        rules.add_resolver(100, "core.kill", resolve_kills)
        rules.add_resolver(200, "core.move", resolve_moves)
        rules.add_resolver(300, "core.meeting_request", resolve_meeting_requests)
        rules.add_resolver(900, "core.cooldowns", tick_cooldowns)
        rules.add_resolver(1000, "core.snapshots", lambda game, rc: emit_room_snapshots(game))
        rules.add_win_condition(100, "core.impostors_ejected", impostors_ejected)
        rules.add_win_condition(200, "core.parity", parity)


def resolve_kills(game: Game, rc: RoundContext) -> None:
    state = game.state
    killers_of: dict[int, list[int]] = {}
    for seat, action in rc.acting(Kill):
        killers_of.setdefault(action.target, []).append(seat)
    # All kills happen at once, in the room where killer and victim started.
    for victim in sorted(killers_of):
        player = state.player(victim)
        player.alive = False
        rc.killed.add(victim)
        state.bodies.append(Body(victim=victim, room=rc.start_rooms[victim], round=rc.round))
        for killer in killers_of[victim]:
            state.player(killer).kill_cooldown = state.config.kill_cooldown
    for victim in sorted(killers_of):
        room, killers = rc.start_rooms[victim], killers_of[victim]
        game.event("kill", victim=victim, killers=killers, room=room)
        game.observe(victim, "killed", room=room, killers=killers)
        for p in state.living():
            if rc.start_rooms.get(p.seat) == room and p.seat not in killers:
                game.observe(p.seat, "kill_witnessed", room=room, killers=killers, victim=victim)


def resolve_moves(game: Game, rc: RoundContext) -> None:
    for seat, action in rc.acting(Move):
        player = game.state.player(seat)
        game.event("move", seat=seat, from_room=player.room, to_room=action.to)
        player.room = action.to


def resolve_meeting_requests(game: Game, rc: RoundContext) -> None:
    state = game.state
    reporters = [seat for seat, _ in rc.acting(Report)]
    callers = [seat for seat, _ in rc.acting(CallMeeting)]
    if reporters:
        # A report beats an emergency call; the lowest seat is credited.
        caller = reporters[0]
        room = rc.start_rooms[caller]
        # Only bodies that were there when the player chose to report.
        body = min(
            (b for b in state.bodies if b.room == room and b.round < rc.round),
            key=lambda b: b.victim,
        )
        rc.meeting = MeetingRequest("report", caller, body.victim, room, tuple(reporters))
    elif callers:
        caller = callers[0]
        state.player(caller).emergency_meetings_left -= 1
        rc.meeting = MeetingRequest("emergency", caller, requested_by=tuple(callers))
    if rc.meeting is not None:
        m = rc.meeting
        game.event(
            "meeting_requested",
            reason=m.reason,
            caller=m.caller,
            body_victim=m.body_victim,
            body_room=m.body_room,
            requested_by=list(m.requested_by),
        )


def tick_cooldowns(game: Game, rc: RoundContext) -> None:
    killed_this_round = {seat for seat, _ in rc.acting(Kill)}
    for p in game.state.living():
        if p.is_impostor and p.seat not in killed_this_round:
            p.kill_cooldown = max(0, p.kill_cooldown - 1)
