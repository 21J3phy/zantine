"""Game rules, assembled from modules. Add new modules to ``default_modules``."""

from __future__ import annotations

from collections.abc import Iterable

from zantine.rules.core import CoreModule
from zantine.rules.registry import GameModule, MeetingRequest, RoundContext, Rules

__all__ = ["GameModule", "MeetingRequest", "RoundContext", "Rules", "default_rules"]


def default_modules() -> list[GameModule]:
    # One line per module, e.g. TasksModule(), RoomChatModule(), CrisisModule().
    return [CoreModule()]


def default_rules(modules: Iterable[GameModule] | None = None) -> Rules:
    rules = Rules()
    for module in default_modules() if modules is None else modules:
        rules.install(module)
    return rules
