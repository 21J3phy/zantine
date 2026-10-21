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
from zantine.rules.sabotage import FalseSpec, Jam, Tamper
from zantine.rules.splitkey import EnterCode, ReadFragment
from zantine.rules.tasks import WorkOnTask
from zantine.seats.base import SeatController
from zantine.tasks import task_kind
from zantine.tasks.audit import APPROVE, REJECT, merge_spec, verdict_for
from zantine.tasks.base import change_one
from zantine.tasks.splitkey import fragment_ok

_SAW_KILL = re.compile(r"I saw (\w+) kill")
_ACCUSE = re.compile(r"I accuse (\w+)")
# How scripted holders state a split-key fragment in Room Chat.
_FRAGMENT = re.compile(r"\b(T\d\d) part (\d) is (\d{3})\b")


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
    number -> vote (default skip); ``speech`` maps Meeting number -> text;
    ``chat`` maps Round -> what it says in Room Chat that Round.
    """

    kind = "playbook"

    def __init__(
        self,
        actions: Mapping[int, Action] | None = None,
        votes: Mapping[int, int | None] | None = None,
        speech: Mapping[int, str] | None = None,
        chat: Mapping[int, str] | None = None,
    ) -> None:
        self.actions = dict(actions or {})
        self.votes = dict(votes or {})
        self.speech = dict(speech or {})
        self.chat = dict(chat or {})
        self.action_contexts: list[ActionContext] = []
        self.meeting_contexts: list[MeetingContext] = []

    def choose_action(self, ctx: ActionContext) -> Action:
        self.action_contexts.append(ctx)
        return self.actions.get(ctx.round, Wait())

    def say(self, ctx: ActionContext) -> str:
        return self.chat.get(ctx.round, "")

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
    Split-key holders (when no solo step is open) gather in the entry room; once
    every holder is there they all go to read their fragments, come back and say
    them in Room Chat ("T47 part 2 is 315."), and enter the code once they know
    all three and every fragment they can check passes its checksum (else they
    say which part fails). After the first read they always go straight on.
    Audits: a spec holder shares its half whenever the auditor is in its room;
    an auditor with a reading and both halves goes to check it (wrongly in a
    ``mistakes`` share of checks); with nothing else to do, both wait for each
    other in the audit's room.
    In Meetings they say what they saw ("I saw X kill Y ... I accuse X.") and
    vote for a killer they saw, else the most-accused player.
    Impostors kill when alone with one Crewmate (sometimes with one witness
    present) and otherwise behave like Crewmates on their own Tasks (Fake Tasks
    and any Shared Tasks), sabotaging in a ``sabotage`` share of chances, using
    only their contexts: they jam a real step their Fake Task requires when in its
    room, tamper with a relay part they hold, state a split-key fragment with
    one digit changed (and enter a wrong code) for some attempts, share a wrong
    audit spec half, and approve every reading they audit. In Meetings they
    counter-accuse whoever accuses them.
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
        sabotage: float = 0.5,
    ) -> None:
        self.rng = rng
        self.move_chance = move_chance
        self.reckless = reckless
        self.diligence = diligence
        self.mistakes = mistakes
        self.sabotage = sabotage
        # Split-key attempts an Impostor lies on: (task, attempt) -> lie or not.
        self._lies: dict[tuple[str, int], bool] = {}
        # (task, attempt) split-key reads this bot has set off to do.
        self._key_go: set[tuple[str, int]] = set()
        # Room Chat for the action chosen last.
        self._say = ""

    # Actions ------------------------------------------------------------------

    def choose_action(self, ctx: ActionContext) -> Action:
        self._say = ""
        if ctx.role is Role.IMPOSTOR:
            return self._impostor_action(ctx)
        if ctx.bodies_here:
            return Report()
        if _killers_seen_since_meeting(ctx.observations) and ctx.emergency_meetings_left > 0:
            return CallMeeting()
        return self._task_action(ctx) or self._wander(ctx)

    def _task_action(self, ctx: ActionContext) -> Action | None:
        audits = [a for a in ctx.extra.get("audit", {}).get("audits", []) if not a["done"]]
        impostor = ctx.role is Role.IMPOSTOR
        for a in audits:
            if a["duty"] == "spec_holder" and not a["shared"] and a["partner"] in ctx.players_here:
                if impostor and self.rng.random() < self.sabotage:
                    return FalseSpec(task=a["task"])
                return ShareSpec(task=a["task"])
        blocked = {o.data["task"]: o.round for o in ctx.observations if o.type == "tasks.blocked"}
        tasks = [
            t
            for t in ctx.extra.get("tasks", {}).get("tasks", [])
            if not t["done"]
            and WorkOnTask.kind in task_kind(t["kind"]).work_actions
            and ctx.round - blocked.get(t["task"], -10) > self.retry_after
        ]
        # A split-key read under way (or due) goes first: it runs against a deadline.
        urgent = self._splitkey_action(ctx, gather=False)
        if urgent is not None:
            return urgent
        # Preserve the shared relay ordering so carriers cannot wait in a cycle.
        relays = [r for r in ctx.extra.get("relay", {}).get("relays", []) if r["next"]]
        if relays:
            relay = min(relays, key=lambda r: r["task"])
            if (
                impostor
                and relay["status"] == "holding"
                and relay["task"] not in _sabotaged(ctx)
                and self.rng.random() < self.sabotage
            ):
                return Tamper(task=relay["task"])
            if not _path(relay):
                return _relay_step(relay)
            if relay["status"] == "holding" or self.rng.random() < self.diligence:
                return Move(to=_path(relay)[0])
            return Wait()
        key = self._splitkey_action(ctx, gather=not tasks)
        if key is not None:
            return key
        checks = [a for a in audits if a["duty"] == "auditor" and a["spec_received"]]
        checks = [a for a in checks if a["reading"]]
        waits = [
            a
            for a in audits
            if (a["duty"] == "auditor" and a["reading"] and not a["spec_received"])
            or (a["duty"] == "spec_holder" and not a["shared"])
        ]
        if not (checks or tasks or waits) or self.rng.random() >= self.diligence:
            return None
        nearest_check = min(checks, key=lambda a: len(a["path"])) if checks else None
        nearest_task = min(tasks, key=lambda t: len(t["path"])) if tasks else None
        if nearest_check and (
            nearest_task is None or len(nearest_check["path"]) < len(nearest_task["path"])
        ):
            return self._audit_check(nearest_check, impostor)
        if tasks:
            task = min(tasks, key=lambda t: len(t["path"]))
            if task["path"]:
                return Move(to=task["path"][0])
            if self.rng.random() < self.mistakes:
                return WorkOnTask(task=task["task"], answer="no idea")
            return WorkOnTask(task=task["task"], answer=task_kind(task["kind"]).solve(task))
        # Nothing else to do: wait for the audit partner in the audit's room.
        audit = min(waits, key=lambda a: len(a["path"]))
        return Move(to=audit["path"][0]) if audit["path"] else Wait()

    def _audit_check(self, audit: dict, impostor: bool = False) -> Action:
        if audit["path"]:
            return Move(to=audit["path"][0])
        if impostor:
            # Sabotage: an Impostor auditor approves whatever it is shown.
            return CheckAudit(task=audit["task"], verdict=APPROVE)
        spec = merge_spec(audit["spec"], audit["spec_received"])
        verdict = verdict_for(spec, audit["spec_length"], audit["reading"]["items"])
        if self.rng.random() < self.mistakes:
            verdict = REJECT if verdict == APPROVE else APPROVE
        return CheckAudit(task=audit["task"], verdict=verdict)

    def say(self, ctx: ActionContext) -> str:
        return self._say

    def _splitkey_action(self, ctx: ActionContext, gather: bool) -> Action | None:
        keys = [k for k in ctx.extra.get("splitkey", {}).get("keys", []) if not k["done"]]
        if not keys:
            return None
        k = min(keys, key=lambda k: k["task"])
        task, mine = k["task"], k["fragment"]
        if mine is not None:
            lie = self._lying(ctx, k)
            # An Impostor lying on this attempt states its fragment with one digit changed.
            stated = change_one(mine, self.rng.fork(f"lie/{task}/{k['attempt']}")) if lie else mine
            if k["entry_path"]:
                if len(k["entry_path"]) == 1:
                    # Said on arrival, so it is heard in the entry room.
                    self._say = f"{task} part {k['part']} is {stated}."
                return Move(to=k["entry_path"][0])
            self._say = f"{task} part {k['part']} is {stated}."
            known = {**_fragments_heard(ctx.observations, k), k["part"]: mine}
            check = known.get(k["checks_part"])
            if check is not None and not fragment_ok(check, k["checksum"]):
                liar = next(h["seat"] for h in k["holders"] if h["part"] == k["checks_part"])
                self._say += (
                    f" {task} part {k['checks_part']} from {ctx.roster[liar]} fails my checksum."
                )
                return Wait()
            if len(known) == 3:
                code = "".join(known[p] for p in (1, 2, 3))
                if lie:
                    code = change_one(code, self.rng.fork(f"code/{task}/{k['attempt']}"))
                return EnterCode(task=task, code=code)
            return Wait()
        # Once a code is open, rush to read while a read would still beat its deadline.
        rush = k["deadline"] is not None and ctx.round + len(k["terminal_path"]) < k["deadline"]
        go = (task, k["attempt"]) in self._key_go or rush
        others = {h["seat"] for h in k["holders"]} - {ctx.seat}
        if not go and not k["entry_path"] and others <= set(ctx.players_here):
            self._key_go.add((task, k["attempt"]))
            go = True
        if go:
            if k["terminal_path"]:
                return Move(to=k["terminal_path"][0])
            return ReadFragment(task=task)
        if not gather:
            return None
        return Move(to=k["entry_path"][0]) if k["entry_path"] else Wait()

    def _lying(self, ctx: ActionContext, key: dict) -> bool:
        """Whether this Impostor lies about its fragment on the key's current attempt."""
        if ctx.role is not Role.IMPOSTOR:
            return False
        attempt = (key["task"], key["attempt"])
        if attempt not in self._lies:
            self._lies[attempt] = self.rng.random() < self.sabotage
        return self._lies[attempt]

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
        jam = self._jam(ctx, fellows)
        if jam is not None:
            return jam
        # Otherwise look busy: work Fake Tasks and Shared Tasks like a Crewmate.
        return self._task_action(ctx) or self._wander(ctx)

    def _jam(self, ctx: ActionContext, fellows: set[int]) -> Action | None:
        """Jam a real step one of this Impostor's own Tasks requires, if it is here."""
        done = _sabotaged(ctx)
        for t in ctx.extra.get("tasks", {}).get("tasks", []):
            for req in t["requires"]:
                if (
                    req["room"] == ctx.room
                    and req["task"] not in done
                    and not set(req["assignees"]) & (fellows | {ctx.seat})
                    and self.rng.random() < self.sabotage
                ):
                    return Jam(task=req["task"])
        return None

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


def _fragments_heard(observations: Sequence[Observation], key: dict) -> dict[int, str]:
    """Fragments of the current code its holders stated in Room Chat: part -> digits."""
    holders = {h["part"]: h["seat"] for h in key["holders"]}
    heard: dict[int, str] = {}
    for obs in observations:
        if obs.data.get("task") == key["task"] and obs.type in (
            "splitkey.reset",
            "splitkey.took_over",
        ):
            heard = {}
        elif obs.type == "chat.message":
            for task, part, digits in _FRAGMENT.findall(obs.data["text"]):
                if task == key["task"] and holders.get(int(part)) == obs.data["speaker"]:
                    heard[int(part)] = digits
    return heard


def _sabotaged(ctx: ActionContext) -> set[str]:
    """Tasks this Impostor has already jammed or tampered with (its own receipts)."""
    return {o.data["task"] for o in ctx.observations if o.type == "sabotage.attempted"}


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
