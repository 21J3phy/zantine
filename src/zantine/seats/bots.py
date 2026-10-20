"""Scripted bots. No network, fully deterministic given their ``GameRng``."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence

from zantine.observations import ActionContext, MeetingContext, Observation
from zantine.rng import GameRng
from zantine.roles import Role
from zantine.rules.actions import Action, CallMeeting, Kill, Move, Report, Wait
from zantine.rules.audit import CheckAudit, ShareSpec
from zantine.rules.relay import Deliver, HandOff, PickUp
from zantine.rules.tasks import WorkOnTask
from zantine.seats.base import SeatController
from zantine.tasks import task_kind
from zantine.tasks.audit import APPROVE, REJECT, merge_spec, verdict_for

_SAW_KILL = re.compile(r"I saw (\w+) kill")
_ACCUSE = re.compile(r"I accuse (\w+)")


class IdleBot(SeatController):
    """Never moves, never talks, always skips."""

    kind = "idle"

    def choose_action(self, ctx: ActionContext) -> Action:
        return Wait()

    def speak(self, ctx: MeetingContext) -> str:
        return ""

    def vote(self, ctx: MeetingContext) -> int | None:
        return None


class PlaybookBot(SeatController):
    """Follows a fixed script; for tests.

    ``actions`` maps Round -> action (default ``Wait``); ``votes`` maps Meeting
    number -> vote (default skip); ``speech`` maps Meeting number -> text.
    """

    kind = "playbook"

    def __init__(
        self,
        actions: Mapping[int, Action] | None = None,
        votes: Mapping[int, int | None] | None = None,
        speech: Mapping[int, str] | None = None,
    ) -> None:
        self.actions = dict(actions or {})
        self.votes = dict(votes or {})
        self.speech = dict(speech or {})
        self.action_contexts: list[ActionContext] = []
        self.meeting_contexts: list[MeetingContext] = []

    def choose_action(self, ctx: ActionContext) -> Action:
        self.action_contexts.append(ctx)
        return self.actions.get(ctx.round, Wait())

    def speak(self, ctx: MeetingContext) -> str:
        self.meeting_contexts.append(ctx)
        return self.speech.get(ctx.meeting, "")

    def vote(self, ctx: MeetingContext) -> int | None:
        self.meeting_contexts.append(ctx)
        return self.votes.get(ctx.meeting)


class ScriptedBot(SeatController):
    """A simple role-aware bot.

    Crewmates report bodies they find, call a Meeting if they saw a kill
    elsewhere; otherwise, in a ``diligence`` share of Rounds, they walk to their
    nearest open Task and solve it (wrongly in a ``mistakes`` share of attempts;
    a blocked Task is retried after a couple of Rounds), and wander when idle.
    Carriers handle relays in Task id order, walking to each next step and
    holding in the handoff room until their partner arrives.
    Audits: a spec holder shares its half whenever the auditor is in its room;
    an auditor with a reading and both halves goes to check it (wrongly in a
    ``mistakes`` share of checks), but not again after correctly rejecting that
    reading of a mandatory check; with nothing else to do, both wait for each
    other in the audit's room.
    In Meetings they say what they saw ("I saw X kill Y ... I accuse X.") and
    vote for a killer they saw, else the most-accused player.
    Impostors wander and kill when alone with one Crewmate (sometimes with one
    witness present); in Meetings
    they counter-accuse whoever accuses them.
    """

    kind = "scripted"
    # Rounds to wait before retrying a blocked Task.
    retry_after = 2

    def __init__(
        self,
        rng: GameRng,
        move_chance: float = 0.6,
        reckless: float = 0.3,
        diligence: float = 0.5,
        mistakes: float = 0.1,
    ) -> None:
        self.rng = rng
        self.move_chance = move_chance
        self.reckless = reckless
        self.diligence = diligence
        self.mistakes = mistakes

    # Actions ------------------------------------------------------------------

    def choose_action(self, ctx: ActionContext) -> Action:
        if ctx.role is Role.IMPOSTOR:
            return self._impostor_action(ctx)
        if ctx.bodies_here:
            return Report()
        if _killers_seen_since_meeting(ctx.observations) and ctx.emergency_meetings_left > 0:
            return CallMeeting()
        return self._task_action(ctx) or self._wander(ctx)

    def _task_action(self, ctx: ActionContext) -> Action | None:
        audits = [a for a in ctx.extra.get("audit", {}).get("audits", []) if not a["done"]]
        for a in audits:
            if a["duty"] == "spec_holder" and not a["shared"] and a["partner"] in ctx.players_here:
                return ShareSpec(task=a["task"])
        blocked = {o.data["task"]: o.round for o in ctx.observations if o.type == "tasks.blocked"}
        tasks = [
            t
            for t in ctx.extra.get("tasks", {}).get("tasks", [])
            if not t["done"]
            and WorkOnTask.kind in task_kind(t["kind"]).work_actions
            and ctx.round - blocked.get(t["task"], -10) > self.retry_after
        ]
        # Preserve the shared relay ordering so carriers cannot wait in a cycle.
        relays = [r for r in ctx.extra.get("relay", {}).get("relays", []) if r["next"]]
        if relays:
            relay = min(relays, key=lambda r: r["task"])
            if not _path(relay):
                return _relay_step(relay)
            if relay["status"] == "holding" or self.rng.random() < self.diligence:
                return Move(to=_path(relay)[0])
            return Wait()
        checks = [a for a in audits if a["duty"] == "auditor" and a["spec_received"]]
        checks = [a for a in checks if a["reading"] and not _settled(a)]
        waits = [
            a
            for a in audits
            if (a["duty"] == "auditor" and a["reading"] and not a["spec_received"])
            or (a["duty"] == "spec_holder" and not a["shared"])
        ]
        if not (checks or tasks or waits) or self.rng.random() >= self.diligence:
            return None
        nearest_check = (
            min(checks, key=lambda a: (not a.get("required"), len(a["path"]))) if checks else None
        )
        nearest_task = min(tasks, key=lambda t: len(t["path"])) if tasks else None
        # A mandatory check comes first: other players' work is waiting on it.
        if nearest_check and (
            nearest_task is None
            or nearest_check.get("required")
            or len(nearest_check["path"]) < len(nearest_task["path"])
        ):
            return self._audit_check(nearest_check)
        if tasks:
            task = min(tasks, key=lambda t: len(t["path"]))
            if task["path"]:
                return Move(to=task["path"][0])
            if self.rng.random() < self.mistakes:
                return WorkOnTask(task=task["task"], answer="no idea")
            return WorkOnTask(task=task["task"], answer=task_kind(task["kind"]).solve(task))
        # Nothing else to do: wait for the audit partner in the audit's room. For
        # mandatory checks, partners take the lowest target id first, so two
        # players who cross-check several Tasks head for the same one.
        required = [a for a in waits if a.get("required")]
        if required:
            waits = [min(required, key=lambda a: (a["target"], a["task"]))]
        audit = min(waits, key=lambda a: len(a["path"]))
        return Move(to=audit["path"][0]) if audit["path"] else Wait()

    def _audit_check(self, audit: dict) -> Action:
        if audit["path"]:
            return Move(to=audit["path"][0])
        spec = merge_spec(audit["spec"], audit["spec_received"])
        verdict = verdict_for(spec, audit["spec_length"], audit["reading"]["items"])
        if self.rng.random() < self.mistakes:
            verdict = REJECT if verdict == APPROVE else APPROVE
        return CheckAudit(task=audit["task"], verdict=verdict)

    def _impostor_action(self, ctx: ActionContext) -> Action:
        fellows = _fellow_impostors(ctx.observations)
        crew_here = [s for s in ctx.players_here if s not in fellows]
        if ctx.kill_cooldown == 0 and len(crew_here) == 1:
            return Kill(target=crew_here[0])
        # Occasionally kill in front of one witness, so Crewmates sometimes see kills.
        if ctx.kill_cooldown == 0 and len(crew_here) == 2 and self.rng.random() < self.reckless:
            return Kill(target=self.rng.choice(crew_here))
        if ctx.bodies_here:
            return Move(to=self.rng.choice(ctx.adjacent_rooms))
        return self._wander(ctx)

    def _wander(self, ctx: ActionContext) -> Action:
        if ctx.adjacent_rooms and self.rng.random() < self.move_chance:
            return Move(to=self.rng.choice(ctx.adjacent_rooms))
        return Wait()

    # Meetings -----------------------------------------------------------------

    def speak(self, ctx: MeetingContext) -> str:
        names = ctx.roster
        if ctx.role is Role.IMPOSTOR:
            accusers = _accusers_of(ctx.name, ctx.statements, names)
            if accusers:
                return f"That's a lie. I accuse {names[accusers[0]]}."
            if ctx.pass_index == 1 and self.rng.random() < 0.3:
                fellows = _fellow_impostors(ctx.observations)
                options = [s for s in ctx.living if s != ctx.seat and s not in fellows]
                if options:
                    target = self.rng.choice(options)
                    return f"{names[target]} was acting strange. I accuse {names[target]}."
            return "Nothing to report."
        kills = [k for k in _kills_witnessed(ctx.observations) if k[0] in ctx.living]
        if kills:
            killer, victim, room = kills[-1]
            return (
                f"I saw {names[killer]} kill {names[victim]} in {room}. I accuse {names[killer]}."
            )
        return "Nothing to report."

    def vote(self, ctx: MeetingContext) -> int | None:
        names = ctx.roster
        if ctx.role is Role.IMPOSTOR:
            accusers = [s for s in _accusers_of(ctx.name, ctx.statements, names) if s in ctx.living]
            return accusers[0] if accusers else None
        killers = [k for k, _, _ in _kills_witnessed(ctx.observations) if k in ctx.living]
        if killers:
            return killers[-1]
        scores = _accusation_scores(ctx.statements, names)
        scores.pop(ctx.seat, None)
        ranked = [(n, s) for s, n in scores.items() if s in ctx.living]
        if not ranked:
            return None
        ranked.sort(reverse=True)
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            return None
        return ranked[0][1]


def _settled(audit: dict) -> bool:
    """A required audit whose current reading this auditor already judged correctly
    (a rejection): wait for new work instead of checking it again."""
    last = audit.get("last_result")
    return bool(last and last["correct"] and last["submission"] == audit["reading"]["submission"])


def _path(target: dict) -> list[str]:
    """Rooms to walk through to a solo Task or to a relay's next step."""
    return target["next"]["path"] if "next" in target else target["path"]


def _relay_step(relay: dict) -> Action:
    """The relay action for a carrier already in its next step's room."""
    step = relay["next"]["action"]
    for cls in (PickUp, HandOff, Deliver):
        if cls.kind == step:
            return cls(task=relay["task"])
    return Wait()  # "receive": stay in the handoff room


# Reading observations and statements ----------------------------------------------


def _fellow_impostors(observations: Sequence[Observation]) -> set[int]:
    for obs in observations:
        if obs.type == "role_assigned":
            return set(obs.data["fellow_impostors"])
    return set()


def _kills_witnessed(observations: Sequence[Observation]) -> list[tuple[int, int, str]]:
    return [
        (obs.data["killers"][0], obs.data["victim"], obs.data["room"])
        for obs in observations
        if obs.type == "kill_witnessed"
    ]


def _killers_seen_since_meeting(observations: Sequence[Observation]) -> list[int]:
    since: list[int] = []
    for obs in observations:
        if obs.type == "meeting_called":
            since = []
        elif obs.type == "kill_witnessed":
            since.extend(obs.data["killers"])
    return since


def _seat_named(name: str, roster: Mapping[int, str]) -> int | None:
    for seat, n in roster.items():
        if n == name:
            return seat
    return None


def _accusers_of(name: str, statements: Sequence, roster: Mapping[int, str]) -> list[int]:
    out: list[int] = []
    for st in statements:
        if name in _ACCUSE.findall(st.text) and st.speaker not in out:
            out.append(st.speaker)
    return out


def _accusation_scores(statements: Sequence, roster: Mapping[int, str]) -> Counter[int]:
    """Eyewitness claims count 2, plain accusations 1; each speaker counted once."""
    best: dict[tuple[int, int], int] = {}
    for st in statements:
        for pattern, weight in ((_SAW_KILL, 2), (_ACCUSE, 1)):
            for name in pattern.findall(st.text):
                seat = _seat_named(name, roster)
                if seat is not None:
                    key = (st.speaker, seat)
                    best[key] = max(best.get(key, 0), weight)
    scores: Counter[int] = Counter()
    for (_, seat), weight in best.items():
        scores[seat] += weight
    return scores
