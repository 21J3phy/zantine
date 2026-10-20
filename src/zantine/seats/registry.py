"""Named controller factories, used by the CLI's ``--bots`` option.

A spec is a comma-separated list of ``name[:arg][*count]`` entries covering every
seat, or a single name used for all seats. Examples::

    scripted
    scripted*9,idle
    live:claude-fable-5-1,scripted*9      # live Seats: only via `zantine play --live`

Register new kinds with ``@register_controller("name")``. Factories receive the
seat's own ``GameRng`` fork and the optional ``arg``. Controllers that call a
paid API must refuse to construct unless explicitly enabled (see AGENTS.md).
"""

from __future__ import annotations

from collections.abc import Callable

from zantine.rng import GameRng
from zantine.seats.base import SeatController
from zantine.seats.bots import IdleBot, ScriptedBot

Factory = Callable[[GameRng, str | None], SeatController]

_FACTORIES: dict[str, Factory] = {}


def register_controller(name: str) -> Callable[[Factory], Factory]:
    def decorator(factory: Factory) -> Factory:
        if name in _FACTORIES:
            raise ValueError(f"controller {name!r} already registered")
        _FACTORIES[name] = factory
        return factory

    return decorator


def controller_names() -> list[str]:
    return sorted(_FACTORIES)


def parse_spec(spec: str, num_seats: int) -> list[str]:
    entries: list[str] = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        name, _, count = part.partition("*")
        entries.extend([name.strip()] * (int(count) if count else 1))
    if len(entries) == 1:
        entries *= num_seats
    if len(entries) != num_seats:
        raise ValueError(f"bot spec {spec!r} covers {len(entries)} seats, need {num_seats}")
    return entries


def make_controller(entry: str, rng: GameRng) -> SeatController:
    name, _, arg = entry.partition(":")
    if name not in _FACTORIES:
        raise ValueError(f"unknown controller {name!r}; known: {', '.join(controller_names())}")
    return _FACTORIES[name](rng, arg or None)


def make_controllers(spec: str, num_seats: int, rng: GameRng) -> list[SeatController]:
    return [
        make_controller(entry, rng.fork(f"seat-{seat}"))
        for seat, entry in enumerate(parse_spec(spec, num_seats))
    ]


register_controller("scripted")(lambda rng, arg: ScriptedBot(rng))
register_controller("idle")(lambda rng, arg: IdleBot())

from zantine.seats import live as _live  # noqa: E402, F401  (registers "live")
