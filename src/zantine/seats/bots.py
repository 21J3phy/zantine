"""Scripted bots. No network, fully deterministic given their ``GameRng``."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence

from zantine.observations import ActionContext, MeetingContext, Observation
from zantine.rng import GameRng
from zantine.roles import Role
from zantine.rules.actions import Action, CallMeeting, Kill, Move, Report, Wait
from zantine.rules.room_chat import Say
from zantine.rules.split_key import ReadPart
from zantine.rules.tasks import WorkOnTask
from zantine.seats.base import SeatController
from zantine.tasks import task_kind
from zantine.tasks.split_key import checksum

_SAW_KILL = re.compile(r"I saw (\w+) kill")
_ACCUSE = re.compile(r"I accuse (\w+)")
# How scripted bots share split-key parts in Room Chat: "T9 part 2: 418".
_PART = re.compile(r"\b(T\d+) part (\d+): (\d+)")


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
    elsewhere; otherwise they work on a split-key Task they hold (read their
    part at its terminal, walk to the entry room, say their part in Room Chat
    to every holder there, check the part their checksum covers and enter the
    code once they know all parts), else, in a ``diligence`` share of Rounds,
    walk to their nearest open solo step and solve it (wrongly in a
    ``mistakes`` share of attempts; a blocked step is retried after a couple of
    Rounds), and wander when idle. A bot with ``lies`` gives a wrong split-key
    part and never enters the code (a stand-in for Sabotage).
    In Meetings they say what they saw ("I saw X kill Y ... I accuse X.") and
    vote for a killer they saw, else the most-accused player.
    Impostors wander and kill when alone with one Crewmate (sometimes with one
    witness present); in Meetings
    they counter-accuse whoever accuses them.
    """

    kind = "scripted"
    # Rounds to wait before retrying a blocked Task.
    retry_after = 2
    # Split-key holders open the window only on Rounds divisible by this.
    read_every = 8

    def __init__(
        self,
        rng: GameRng,
        move_chance: float = 0.6,
        reckless: float = 0.3,
        diligence: float = 0.5,
        mistakes: float = 0.1,
        lies: bool = False,
    ) -> None:
        self.rng = rng
        self.move_chance = move_chance
        self.reckless = reckless
        self.diligence = diligence
        self.mistakes = mistakes
        self.lies = lies

    # Actions ------------------------------------------------------------------

    def choose_action(self, ctx: ActionContext) -> Action:
        if ctx.role is Role.IMPOSTOR:
            return self._impostor_action(ctx)
        if ctx.bodies_here:
            return Report()
        if _killers_seen_since_meeting(ctx.observations) and ctx.emergency_meetings_left > 0:
            return CallMeeting()
        return self._split_key_action(ctx) or self._task_action(ctx) or self._wander(ctx)

    def _split_key_action(self, ctx: ActionContext) -> Action | None:
        for key in ctx.extra.get("split_key", {}).get("tasks", []):
            action = self._split_key_step(ctx, key)
            if action is not None:
                return action
        return None

    def _split_key_step(self, ctx: ActionContext, key: dict) -> Action | None:
        task = key["task"]
        if key["digits"] is None:
            # Reading opens the window for everyone, so scripted holders all
            # read on the same Rounds (a fixed convention standing in for
            # agreeing a time in Room Chat), or at once if it is already open.
            # Until it is time to head to the terminal, do other work.
            wait = -ctx.round % self.read_every if key["deadline"] is None else 0
            if len(key["part_path"]) < wait:
                return None
            if key["part_path"]:
                return Move(to=key["part_path"][0])
            return ReadPart(task=task) if wait == 0 else Wait()
        if key["entry_path"]:
            return Move(to=key["entry_path"][0])
        holders = {h["part"]: h["seat"] for h in key["holders"]}
        mine = _corrupt(key["digits"]) if self.lies else key["digits"]
        # Parts of the current code can only have been said after its window opened.
        heard, told, said = _split_key_chat(ctx, task, key["deadline"] - key["window"])
        if (set(holders.values()) & set(ctx.players_here)) - told - {ctx.seat}:
            return Say(text=f"{task} part {key['part']}: {mine}")
        parts = {**heard, key["part"]: key["digits"]}
        if self.lies or len(parts) < len(holders):
            return Wait()
        checked = parts[key["checks_part"]]
        if checksum(checked) != key["checksum"]:
            # The checksum exposes a wrong part: say whose, and don't enter it.
            warning = (
                f"{task} part {key['checks_part']} from "
                f"{ctx.roster[holders[key['checks_part']]]} fails my checksum."
            )
            return Say(text=warning) if warning not in said else Wait()
        brief = {"inputs": {"parts": [parts[p] for p in sorted(parts)]}}
        return WorkOnTask(task=task, answer=task_kind("split_key").solve(brief))

    def _task_action(self, ctx: ActionContext) -> Action | None:
        blocked = {o.data["task"]: o.round for o in ctx.observations if o.type == "tasks.blocked"}
        tasks = [
            t
            for t in ctx.extra.get("tasks", {}).get("tasks", [])
            if not t["done"]
            and task_kind(t["kind"]).seats == 1
            and ctx.round - blocked.get(t["task"], -10) > self.retry_after
        ]
        if not tasks or self.rng.random() >= self.diligence:
            return None
        task = min(tasks, key=lambda t: len(t["path"]))
        if task["path"]:
            return Move(to=task["path"][0])
        if self.rng.random() < self.mistakes:
            return WorkOnTask(task=task["task"], answer="no idea")
        return WorkOnTask(task=task["task"], answer=task_kind(task["kind"]).solve(task))

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


# Reading observations and statements ----------------------------------------------


def _split_key_chat(
    ctx: ActionContext, task: str, since: int
) -> tuple[dict[int, str], set[int], set[str]]:
    """Room Chat about ``task`` after Round ``since`` (when the current code's
    window opened): parts heard from others, seats that heard my part, and
    everything I said."""
    heard: dict[int, str] = {}
    told: set[int] = set()
    said: set[str] = set()
    for obs in ctx.observations:
        if obs.type != "room_chat.message" or obs.round <= since:
            continue
        text = obs.data["text"]
        if obs.data["speaker"] == ctx.seat:
            said.add(text)
            if any(t == task for t, _, _ in _PART.findall(text)):
                told.update(obs.data["listeners"])
            continue
        for t, part, digits in _PART.findall(text):
            if t == task:
                heard[int(part)] = digits
    return heard, told, said


def _corrupt(digits: str) -> str:
    """A wrong part that fails its checksum (last digit changed)."""
    return digits[:-1] + str((int(digits[-1]) + 1) % 10)


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
