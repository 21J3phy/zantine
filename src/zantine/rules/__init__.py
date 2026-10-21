"""Game rules, assembled from modules. Add new modules to ``default_modules``."""

from __future__ import annotations

from collections.abc import Iterable

from zantine.rules.audit import AuditModule
from zantine.rules.chat import ChatModule
from zantine.rules.containment import ContainmentModule
from zantine.rules.core import CoreModule
from zantine.rules.crisis import CrisisModule
from zantine.rules.registry import GameModule, MeetingRequest, RoundContext, Rules
from zantine.rules.relay import RelayModule
from zantine.rules.sabotage import SabotageModule
from zantine.rules.splitkey import SplitKeyModule
from zantine.rules.tasks import TasksModule

__all__ = ["GameModule", "MeetingRequest", "RoundContext", "Rules", "default_rules"]


def default_modules() -> list[GameModule]:
    # One line per module, e.g. TasksModule(), RoomChatModule(), CrisisModule().
    # Task modules that place Tasks (relay, split-key, audit) come after TasksModule.
    return [
        CoreModule(),
        CrisisModule(),
        TasksModule(),
        # Right after Tasks: Fake Tasks must exist before other modules balance loads.
        SabotageModule(),
        RelayModule(),
        SplitKeyModule(),
        AuditModule(),
        ChatModule(),
        ContainmentModule(),
    ]


def default_rules(modules: Iterable[GameModule] | None = None) -> Rules:
    rules = Rules()
    for module in default_modules() if modules is None else modules:
        rules.install(module)
    return rules
