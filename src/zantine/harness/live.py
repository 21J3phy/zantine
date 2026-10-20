"""Running a game with live Model Seats: the live gate, wiring, spend cap abort, cost report.

``play_live(config, "live:claude-fable-5-1,scripted*9", spend_cap_usd=10, live=True)``
plays one game. Real adapters are built only when ``live`` is true *and*
``ZANTINE_LIVE=1``; tests pass ``adapter_factory`` with a fake client instead.

A live Seat can name its effort level, ``live:<model>[@<harness>]?effort=high``; the
``effort`` argument sets it for every live Seat instead. A Model plays at one
level per game. The level and the lab's own setting are in the header's Seat
controllers and on every ``harness.call`` event.

Every call is written to the transcript as a ``harness.call`` event. When the
spend cap (or a fatal provider error) stops the game, the transcript so far is
kept and gets an ``aborted`` record. Either way it ends with a ``cost`` record:
tokens, dollars and Invalid Action counts per Seat.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from zantine.config import GameConfig
from zantine.engine import Game
from zantine.harness.ledger import CallRecord, Ledger, SpendCapReached, summarize
from zantine.harness.pricing import ModelSpec, load_models, model_spec, split_effort
from zantine.harness.provider import ProviderAdapter, ProviderError, make_adapter
from zantine.rng import GameRng
from zantine.scenario import build_scenario
from zantine.seats.base import SeatController
from zantine.seats.live import HarnessSeat, parse_live_arg
from zantine.seats.registry import make_controller, parse_spec

LIVE_ENV = "ZANTINE_LIVE"


class LiveDisabled(ValueError):
    """Live Model calls were requested without both opt-ins."""


def require_live(flag: bool) -> None:
    if not flag:
        raise LiveDisabled("live Model seats need the explicit --live flag")
    if os.environ.get(LIVE_ENV) != "1":
        raise LiveDisabled(f"live Model seats need {LIVE_ENV}=1 in the environment")


def live_seats(entries: Sequence[str]) -> list[str]:
    """The ``<model>[@<harness>][?effort=<level>]`` argument of each ``live:`` entry."""
    out = []
    for entry in entries:
        name, _, arg = entry.partition(":")
        if name == "live":
            parse_live_arg(split_effort(arg)[0])  # refuse a bad model or Harness early
            out.append(arg)
    return out


def live_models(entries: Sequence[str]) -> list[str]:
    """Model IDs of the ``live:<model>[@<harness>][?effort=<level>]`` entries in a bot spec."""
    return [parse_live_arg(split_effort(arg)[0])[0] for arg in live_seats(entries)]


def seat_spec(arg: str, models: dict[str, ModelSpec], effort: str | None = None) -> ModelSpec:
    """The ModelSpec a ``live:`` argument plays, at its effort level.

    The level comes from the Seat's ``?effort=`` or else ``effort``; naming two
    different levels is refused.
    """
    model_part, level = split_effort(arg)
    model_id = parse_live_arg(model_part)[0]
    if level is not None and effort is not None and level != effort:
        raise ValueError(f"{arg!r} asks for effort {level!r} but the run sets {effort!r}")
    spec = model_spec(model_id, models)
    level = level or effort
    return spec if level is None else spec.at_effort(level)


@dataclass
class LiveRun:
    game: Game
    ledger: Ledger
    # None if the game finished; else why it stopped ("spend_cap", "provider_error").
    aborted: str | None
    detail: str | None

    @property
    def cost(self) -> dict[str, Any]:
        return next(r for r in reversed(self.game.transcript.records) if r["kind"] == "cost")


def play_live(
    config: GameConfig,
    bots: str,
    *,
    spend_cap_usd: float,
    live: bool = False,
    adapter_factory: Callable[[ModelSpec], ProviderAdapter] | None = None,
    models: dict[str, ModelSpec] | None = None,
    ledger: Ledger | None = None,
    effort: str | None = None,
) -> LiveRun:
    """Play one game in which ``live:<model>`` seats are played by Models.

    ``live:<model>@<harness>`` picks the Seat's Harness (default ``neutral``);
    every Harness shares the adapters, pricing, ``ledger`` and spend cap.
    Pass a shared ``ledger`` to make several games draw on one spend cap.
    ``effort`` (``low`` / ``medium`` / ``high``) overrides every live Model's
    configured level, as ``live:<model>?effort=<level>`` does for one Model;
    each adapter sends its lab's own setting for that level.
    """
    models = load_models() if models is None else models
    entries = parse_spec(bots, config.num_seats)
    wanted = live_seats(entries)
    specs = {arg: seat_spec(arg, models, effort) for arg in wanted}
    levels: dict[str, str] = {}
    for spec in specs.values():
        if levels.setdefault(spec.id, spec.effort) != spec.effort:
            raise ValueError(f"{spec.id} is seated at two effort levels; use one per game")
    if adapter_factory is None:
        if wanted:
            require_live(live)
        adapter_factory = make_adapter
    ledger = ledger or Ledger(spend_cap_usd)
    adapters: dict[str, ProviderAdapter] = {}
    built: dict[str, ProviderAdapter] = {}
    for arg, spec in specs.items():
        if spec.id not in built:
            spec.check_price_current()
            try:
                built[spec.id] = adapter_factory(spec)
            except ProviderError as exc:
                raise ValueError(f"cannot start {spec.id}: {exc}") from exc
        adapters[arg] = built[spec.id]

    # Same scenario, seating and RNG streams as runner.new_game, so bots behave alike.
    rng = GameRng(config.seed)
    scenario = build_scenario(config, rng.fork("scenario"))
    seats_rng = rng.fork("seats")
    controllers: list[SeatController] = []
    for seat, entry in enumerate(scenario.seat(entries)):
        name, _, arg = entry.partition(":")
        if name == "live":
            _, seat_class = parse_live_arg(split_effort(arg)[0])
            controllers.append(seat_class(adapters[arg], ledger))
        else:
            controllers.append(make_controller(entry, seats_rng.fork(f"seat-{seat}")))
    game = Game(config, controllers, rng=rng, scenario=scenario)
    for c in controllers:
        if isinstance(c, HarnessSeat):
            c.rules = game.rules
            c.record_event = game.event

    first = len(ledger.records)
    ledger.sink = lambda rec: game.event("harness.call", **rec.to_dict())
    aborted: str | None = None
    detail: str | None = None
    try:
        game.run()
    except SpendCapReached as exc:
        aborted, detail = "spend_cap", str(exc)
    except ProviderError as exc:
        aborted, detail = "provider_error", str(exc)
    finally:
        ledger.sink = None
    if aborted:
        game.transcript.record(
            "aborted",
            reason=aborted,
            detail=detail,
            round=game.state.round,
            meetings=game.state.meetings_held,
        )
    game.transcript.record("cost", **cost_report(game, ledger, ledger.records[first:], aborted))
    return LiveRun(game, ledger, aborted, detail)


def cost_report(
    game: Game, ledger: Ledger, records: Sequence[CallRecord], aborted: str | None
) -> dict[str, Any]:
    """Per-Seat, per-Model and per-Harness tokens (incl. thinking), dollars and Invalid Actions."""
    invalid = {seat: 0 for seat in range(game.config.num_seats)}
    forfeits = dict(invalid)
    for r in game.transcript.records:
        if r["kind"] != "event":
            continue
        d = r["data"]
        bad = r["type"] in ("action", "vote") and not d["valid"]
        bad = bad or r["type"] == "harness.invalid_speech"
        if bad:
            invalid[d["seat"]] += 1
            forfeits[d["seat"]] += d["attempt"] == 2
    specs = {
        c.adapter.model.id: c.adapter.model for c in game.controllers if isinstance(c, HarnessSeat)
    }
    models = {
        model_id: {
            "provider": spec.provider,
            "effort": spec.effort,
            "effort_setting": spec.effort_setting,
            **summarize(r for r in records if r.model == model_id),
        }
        for model_id, spec in specs.items()
    }
    live_seats = [(s, c) for s, c in enumerate(game.controllers) if isinstance(c, HarnessSeat)]
    harnesses = {
        label: {
            "seats": [s for s, c in live_seats if c.harness_label() == label],
            **summarize(
                r for r in records if f"{r.harness}-v{r.harness_version}" == label and r.harness
            ),
        }
        for label in sorted({c.harness_label() for _, c in live_seats})
    }
    seats = []
    for seat, controller in enumerate(game.controllers):
        mine = [r for r in records if r.seat == seat]
        seats.append(
            {
                "seat": seat,
                "controller": controller.describe(),
                "invalid_actions": invalid[seat],
                "forfeits": forfeits[seat],
                **summarize(mine),
            }
        )
    return {
        "complete": aborted is None,
        "seats": seats,
        "models": models,
        "harnesses": harnesses,
        "game": summarize(records),
        "spend_cap_usd": ledger.spend_cap_usd,
        "run_spent_usd": round(ledger.spent_usd, 6),
    }
