"""Game rules, assembled from modules. Add new modules to ``default_modules``."""

from __future__ import annotations

from collections.abc import Iterable

from zantine.rules.audit import AuditModule
from zantine.rules.containment import ContainmentModule
from zantine.rules.core import CoreModule
from zantine.rules.crisis import CrisisModule
from zantine.rules.registry import GameModule, MeetingRequest, RoundContext, Rules
from zantine.rules.relay import RelayModule
from zantine.rules.tasks import TasksModule
from zantine.rules.verification import VerificationModule

__all__ = ["GameModule", "MeetingRequest", "RoundContext", "Rules", "default_rules"]


def default_modules() -> list[GameModule]:
    # One line per module, e.g. TasksModule(), RoomChatModule(), CrisisModule().
    return [
        CoreModule(),
        CrisisModule(),
        TasksModule(),
        RelayModule(),
        AuditModule(),
        VerificationModule(),
        ContainmentModule(),
    ]


def default_rules(modules: Iterable[GameModule] | None = None) -> Rules:
    rules = Rules()
    for module in default_modules() if modules is None else modules:
        rules.install(module)
    return rules
