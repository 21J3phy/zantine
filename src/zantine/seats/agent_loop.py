"""A live Seat played through the agent-loop Harness (``harness.agent_loop``).

``AgentLoopSeat`` runs each decision as a loop of up to ``MAX_TURNS`` model
calls, every one through the run's ``Ledger`` like the Neutral Harness's single
call. ``update_notes`` calls and unusable tool calls are steps inside the loop;
the first usable decision-tool call ends it. Anything else (no tool call, a
refusal, a transient API error, running out of turns) ends the decision as an
Invalid Action, after which the engine's one retry and forfeit apply. Room
Chat rides on the deciding action call's ``say`` argument, as in the Neutral
Harness. Speech gets the same one-retry rule as ``HarnessSeat``. Notes are recorded as
``harness.notes`` events; they never reach another player.

Select it with ``--bots 'live:<model>@agent-loop,...'``.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from zantine.harness import agent_loop, neutral
from zantine.harness.ledger import Ledger
from zantine.harness.provider import HarnessRequest, ProviderAdapter, ProviderResponse
from zantine.observations import ActionContext, MeetingContext
from zantine.rules import Rules
from zantine.rules.actions import Action, Invalid
from zantine.seats.live import HarnessSeat, register_harness

# (value, None) for a usable answer, or (None, reason).
Parse = Callable[[ProviderResponse], tuple[Any, str | None]]
# Called with ``notes=`` and ``steps=``.
Build = Callable[..., HarnessRequest]


@register_harness
class AgentLoopSeat(HarnessSeat):
    """A live Seat played through the agent-loop Harness."""

    harness_name = agent_loop.HARNESS_NAME
    harness_version = agent_loop.HARNESS_VERSION

    def __init__(
        self, adapter: ProviderAdapter, ledger: Ledger, rules: Rules | None = None
    ) -> None:
        super().__init__(adapter, ledger, rules)
        self.notes = ""

    def _run(
        self,
        build: Build,
        parse: Parse,
        phase: str,
        ctx: ActionContext | MeetingContext,
        attempt: int,
    ) -> tuple[Any, str | None]:
        """One decision: (answer, None) or (None, why there is none)."""
        steps: list[agent_loop.Step] = []
        for _ in range(agent_loop.MAX_TURNS):
            request = build(notes=self.notes, steps=tuple(steps))
            response = self._send(request, phase, ctx.seat, ctx.round, attempt)
            if isinstance(response, str):
                return None, response
            calls = response.tool_calls
            if response.stop_reason == "refusal" or not calls:
                # Like the SDK's final text output: the run ends, here unanswered.
                _, error = parse(response)
                return None, error or "no tool call"
            if len(calls) == 1 and calls[0].name == agent_loop.NOTES_TOOL.name:
                notes, result = agent_loop.new_notes(calls[0])
                if notes is not None:
                    self.notes = notes
                    self._notes_event(ctx, phase)
                steps.append(agent_loop.Step(agent_loop.render_call(calls), result))
                continue
            value, error = parse(response)
            if error is None:
                return value, None
            steps.append(agent_loop.Step(agent_loop.render_call(calls), f"rejected: {error}"))
        return None, f"no answer within {agent_loop.MAX_TURNS} model calls"

    def _notes_event(self, ctx: ActionContext | MeetingContext, phase: str) -> None:
        if self.record_event is not None:
            self.record_event(
                "harness.notes",
                seat=ctx.seat,
                model=self.adapter.model.id,
                round=ctx.round,
                phase=phase,
                text=self.notes,
            )

    def choose_action(self, ctx: ActionContext) -> Action:
        retry = self._retry_reason(ctx.retry_reason)
        attempt = 2 if ctx.retry_reason else 1
        build = partial(agent_loop.action_request, self.rules, ctx, retry=retry)

        def parse(response: ProviderResponse) -> tuple[Any, str | None]:
            action, said = neutral.parse_turn(self.rules, response)
            if isinstance(action, Invalid):
                return None, action.reason
            return (action, said), None

        turn, error = self._run(build, parse, "action", ctx, attempt)
        self._last_error = error
        if error is not None:
            self._said = ""
            return Invalid(reason=error)
        # The Room Chat ``say`` that came with the deciding call (HarnessSeat.say).
        action, self._said = turn
        return action

    def speak(self, ctx: MeetingContext) -> str:
        retry: str | None = None
        for attempt in (1, 2):
            build = partial(agent_loop.speak_request, self.rules, ctx, retry=retry)
            text, retry = self._run(build, neutral.parse_speech, "speak", ctx, attempt)
            if text is not None:
                return text
            self._invalid_speech(ctx, attempt, retry or "invalid speech")
        return ""

    def vote(self, ctx: MeetingContext) -> int | neutral.BadVote | None:
        retry = self._retry_reason(ctx.retry_reason)
        attempt = 2 if ctx.retry_reason else 1
        build = partial(agent_loop.vote_request, self.rules, ctx, retry=retry)

        def parse(response: ProviderResponse) -> tuple[Any, str | None]:
            vote = neutral.parse_vote(response)
            return (None, vote.reason) if isinstance(vote, neutral.BadVote) else (vote, None)

        vote, error = self._run(build, parse, "vote", ctx, attempt)
        self._last_error = error
        return neutral.BadVote(error) if error is not None else vote
