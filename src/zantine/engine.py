"""The game loop: Rounds of simultaneous actions, Meetings and win checks."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from zantine.config import GameConfig
from zantine.meeting import run_meeting
from zantine.observations import ActionContext, MeetingContext, Observation, Statement
from zantine.rng import GameRng
from zantine.roles import Team
from zantine.rules import Rules, default_rules
from zantine.rules.actions import Action, Wait
from zantine.rules.registry import RoundContext
from zantine.rules.win import round_cap
from zantine.scenario import SCENARIO_VERSION, Scenario, build_scenario
from zantine.seats.base import SeatController
from zantine.state import GameState, PlayerState
from zantine.transcript import Transcript
from zantine.vision import emit_room_snapshots

if TYPE_CHECKING:
    from zantine.stylemask import StyleMask


class Game:
    def __init__(
        self,
        config: GameConfig,
        controllers: Sequence[SeatController],
        *,
        rng: GameRng | None = None,
        rules: Rules | None = None,
        scenario: Scenario | None = None,
        style_mask: StyleMask | None = None,
    ) -> None:
        if len(controllers) != config.num_seats:
            raise ValueError(f"need {config.num_seats} controllers, got {len(controllers)}")
        if style_mask is not None and not config.style_mask:
            raise ValueError("a Style Mask was given but config.style_mask is off")
        if config.style_mask and style_mask is None:
            from zantine.stylemask import dry_style_mask

            style_mask = dry_style_mask()
        # Rewrites every public message when the Style Mask condition is on.
        self.style_mask = style_mask
        self._mask_outcomes: dict[str, int] = {}
        self.config = config
        self.rng = rng or GameRng(config.seed)
        self.rules = rules or default_rules()
        self.controllers = list(controllers)
        # Controllers are already in seat order; runner.new_game applies the
        # scenario's seat order to a lineup before constructing the Game.
        scenario = scenario or build_scenario(config, self.rng.fork("scenario"))
        self.scenario = scenario
        players = [
            PlayerState(
                seat=seat,
                name=scenario.names[seat],
                role=scenario.roles[seat],
                room=scenario.map.meeting_room,
                emergency_meetings_left=config.emergency_meetings_per_seat,
            )
            for seat in range(config.num_seats)
        ]
        for p in players:
            if p.is_impostor:
                p.kill_cooldown = config.kill_cooldown
        self.state = GameState(config=config, map=scenario.map, players=players)
        self.transcript = Transcript()
        self.meeting_rng = self.rng.fork("meeting-order")
        self._observations: dict[int, list[Observation]] = {p.seat: [] for p in players}
        self._started = False
        self._finished = False

    # Recording ----------------------------------------------------------------

    def event(self, type: str, /, **data: Any) -> None:
        """Record a ground-truth event."""
        self.transcript.event(self.state.round, type, **data)

    def observe(self, seat: int, type: str, /, **data: Any) -> None:
        """Deliver a private observation to one player (and record it)."""
        obs = Observation(round=self.state.round, seat=seat, type=type, data=data)
        self._observations[seat].append(obs)
        self.transcript.observation(obs)

    def observations_of(self, seat: int) -> tuple[Observation, ...]:
        return tuple(self._observations[seat])

    def public_message(
        self,
        channel: str,
        speaker: int,
        text: str,
        earlier: Sequence[tuple[int, str]] = (),
        max_chars: int | None = None,
        **where: Any,
    ) -> tuple[str, dict[str, Any]]:
        """The text players see for a message one player broadcasts to others.

        The single hook for public speech: Meeting statements now, Room Chat
        later. Call it after the Talk Budget cut and before any ``observe``, then
        record the returned fields on the message's ground-truth event. With the
        Style Mask off it returns ``text`` unchanged and no fields. With it on,
        the text is the checked rewrite (``stylemask.StyleMask.apply``), every
        candidate is logged as a ``style_mask.check`` event, and the fields are
        ``original``, ``masker`` (id) and ``mask`` (outcome). ``earlier`` is the
        channel's previous messages as players saw them, ``(speaker, text)``;
        ``where`` (e.g. meeting, pass_index, order) is copied into the events.
        """
        if self.style_mask is None:
            return text, {}
        from zantine.stylemask import MaskInput

        state = self.state
        item = MaskInput(
            channel=channel,
            text=text,
            speaker=speaker,
            roster=state.roster(),
            living=tuple(p.seat for p in state.living()),
            rooms=state.map.rooms,
            round=state.round,
            meeting=state.meetings_held,
            max_chars=self.config.talk_budget(speaker) if max_chars is None else max_chars,
            earlier=tuple((state.player(s).name, t) for s, t in earlier),
        )
        result = self.style_mask.apply(item)
        expected = result.expected.to_dict() if result.expected else None
        for attempt in result.attempts:
            self.event(
                "style_mask.check",
                channel=channel,
                speaker=speaker,
                **where,
                **attempt.to_dict(),
                expected=expected,
            )
        self._mask_outcomes[result.outcome] = self._mask_outcomes.get(result.outcome, 0) + 1
        fields = {"original": text, "masker": self.style_mask.masker_id, "mask": result.outcome}
        return result.text, fields

    # Lifecycle ----------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        state = self.state
        self.transcript.header(
            seed=self.config.seed,
            scenario_version=SCENARIO_VERSION,
            config=self.config.to_dict(),
            map=state.map.to_dict(),
            modules=[m.name for m in self.rules.modules],
            seats=[
                {
                    "seat": p.seat,
                    "name": p.name,
                    "role": p.role.value,
                    "controller": self.controllers[p.seat].describe(),
                }
                for p in state.players
            ],
        )
        if self.style_mask is not None:
            self.event("style_mask.setup", **self.style_mask.info())
        for module in self.rules.modules:
            module.on_setup(self)
        impostors = [p.seat for p in state.players if p.is_impostor]
        for p in state.players:
            fellows = [s for s in impostors if s != p.seat] if p.is_impostor else []
            self.observe(
                p.seat,
                "role_assigned",
                role=p.role.value,
                fellow_impostors=fellows,
                impostor_count=self.config.impostor_count_text,
            )
        emit_room_snapshots(self)

    def run(self) -> GameState:
        self.start()
        while not self.state.over:
            self.play_round()
        return self.state

    def play_round(self) -> None:
        self.start()
        if self.state.over:
            raise RuntimeError("game is over")
        state = self.state
        state.round += 1
        living = state.living()
        # Every player decides on the same start-of-Round state: simultaneous.
        actions: dict[int, Action] = {}
        speech: dict[int, str] = {}
        for p in living:
            actions[p.seat], speech[p.seat] = self._request_action(p)
        rc = RoundContext(
            round=state.round,
            actions=actions,
            start_rooms={p.seat: p.room for p in living},
            speech=speech,
        )
        for step in self.rules.resolvers():
            step.fn(self, rc)
        self._check_win()
        if not state.over and rc.meeting is not None:
            run_meeting(self, rc.meeting)
            self._check_win()
        if not state.over and state.round >= self.config.round_cap:
            self._end(*round_cap(self))
        if state.over:
            self._finish()

    def _check_win(self) -> None:
        for step in self.rules.win_conditions():
            result = step.fn(self)
            if result is not None:
                self._end(*result)
                return

    def _end(self, winner: Team, reason: str) -> None:
        self.state.winner = winner
        self.state.win_reason = reason

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        state = self.state
        assert state.winner is not None and state.win_reason is not None
        self.event("game_over", winner=state.winner.value, reason=state.win_reason)
        for p in state.players:
            self.observe(p.seat, "game_over", winner=state.winner.value, reason=state.win_reason)
        extra: dict[str, Any] = {}
        for module in self.rules.modules:
            data = module.result_extra(self)
            if data is not None:
                extra[module.name] = data
        if self.style_mask is not None:
            extra["style_mask"] = {
                "masker": self.style_mask.masker.info(),
                "outcomes": dict(sorted(self._mask_outcomes.items())),
            }
        self.transcript.result(
            winner=state.winner.value,
            reason=state.win_reason,
            rounds=state.round,
            meetings=state.meetings_held,
            killed=sorted(p.seat for p in state.players if not p.alive and not p.ejected),
            ejected=sorted(p.seat for p in state.players if p.ejected),
            survivors=sorted(p.seat for p in state.living()),
            **extra,
        )

    # Asking seats -------------------------------------------------------------

    def ask[T](
        self,
        seat: int,
        what: str,
        prompt: Callable[[str | None], T],
        validate: Callable[[T], str | None],
        encode: Callable[[T], Any],
        fallback: T,
        **event_data: Any,
    ) -> T:
        """Ask a seat for a decision, with one retry after an Invalid Action.

        A second invalid answer forfeits: ``fallback`` is used instead.
        """
        retry_reason: str | None = None
        for attempt in (1, 2):
            answer = prompt(retry_reason)
            reason = validate(answer)
            self.event(
                what,
                seat=seat,
                attempt=attempt,
                value=encode(answer),
                valid=reason is None,
                reason=reason,
                **event_data,
            )
            if reason is None:
                return answer
            forfeited = attempt == 2
            self.observe(
                seat,
                "invalid_action",
                what=what,
                attempt=attempt,
                reason=reason,
                forfeited=forfeited,
            )
            retry_reason = reason
        return fallback

    def _request_action(self, player: PlayerState) -> tuple[Action, str]:
        """The seat's action for this Round and what it says with it (Room Chat)."""
        controller = self.controllers[player.seat]
        asked: list[ActionContext] = []

        def prompt(retry: str | None) -> Action:
            asked.append(self.action_context(player, retry))
            return controller.choose_action(asked[-1])

        forfeit = Wait()
        action = self.ask(
            player.seat,
            "action",
            prompt=prompt,
            validate=lambda a: self.rules.validate(a, self.state, player),
            encode=_encode_action,
            fallback=forfeit,
        )
        if action is forfeit:
            # A forfeited Round says nothing either.
            return action, ""
        said = controller.say(asked[-1])
        return action, said if isinstance(said, str) else ""

    # Contexts -----------------------------------------------------------------

    def _extra(self, seat: int) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        for module in self.rules.modules:
            data = module.context_extra(self, seat)
            if data is not None:
                extra[module.name] = data
        return extra

    def action_context(self, player: PlayerState, retry_reason: str | None = None) -> ActionContext:
        state = self.state
        room = player.room
        return ActionContext(
            seat=player.seat,
            name=player.name,
            role=player.role,
            round=state.round,
            room=room,
            adjacent_rooms=state.map.neighbors(room),
            players_here=tuple(sorted(p.seat for p in state.living_in(room) if p is not player)),
            bodies_here=tuple(sorted(b.victim for b in state.bodies_in(room))),
            kill_cooldown=player.kill_cooldown if player.is_impostor else None,
            emergency_meetings_left=player.emergency_meetings_left,
            roster=state.roster(),
            observations=self.observations_of(player.seat),
            impostor_count=self.config.impostor_count_text,
            retry_reason=retry_reason,
            extra=self._extra(player.seat),
        )

    def meeting_context(
        self,
        seat: int,
        meeting: int,
        statements: Sequence[Statement],
        pass_index: int | None,
        retry_reason: str | None = None,
    ) -> MeetingContext:
        state = self.state
        player = state.player(seat)
        return MeetingContext(
            seat=seat,
            name=player.name,
            role=player.role,
            round=state.round,
            meeting=meeting,
            living=tuple(p.seat for p in state.living()),
            roster=state.roster(),
            statements=tuple(statements),
            observations=self.observations_of(seat),
            pass_index=pass_index,
            talk_budget_chars=self.config.talk_budget(seat),
            impostor_count=self.config.impostor_count_text,
            retry_reason=retry_reason,
            extra=self._extra(seat),
        )


def _encode_action(action: object) -> Any:
    if isinstance(action, Action):
        return action.to_dict()
    return {"kind": None, "repr": repr(action)}
