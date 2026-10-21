"""Hidden Identity: nothing a player sees names the Model behind any Seat.

Scans every context, observation and Neutral Harness prompt of scheduled games
(dry-run seats, and live seats on every provider's fake client) for Model IDs, their name
parts and lab names. The transcript header is ground truth and does carry the
Models; that is checked too, so the scans can't pass vacuously.
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Any

import pytest

from fake_providers import fake_adapter
from zantine.harness import neutral
from zantine.harness.live import play_live
from zantine.harness.pricing import ModelSpec, load_models
from zantine.harness.provider import ProviderAdapter
from zantine.observations import ActionContext, MeetingContext
from zantine.rules.actions import Action
from zantine.runner import new_game
from zantine.runs import game_setup
from zantine.schedule import load_pool, make_manifest
from zantine.seats.base import SeatController

MODELS = load_models()
POOL = load_pool()


def identity_patterns() -> list[re.Pattern[str]]:
    words = {spec.lab for spec in MODELS.values()}
    for model_id in MODELS:
        words.add(model_id)
        words |= {part for part in re.split(r"[-.\d]+", model_id) if len(part) >= 3}
    return [re.compile(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", re.IGNORECASE) for w in words]


PATTERNS = identity_patterns()


def identity_leaks(value: Any) -> list[str]:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return sorted({m.group(0) for p in PATTERNS for m in p.finditer(text)})


def test_the_scan_finds_model_names() -> None:
    found = identity_leaks("I think Blue is a Claude, or maybe gpt-6.1-sol")
    assert {"Claude", "gpt-6.1-sol"} <= set(found)
    assert identity_leaks({"x": "Gemini plays like Grok; Meta and xAI"})
    assert identity_leaks("Sterling solved the Task in Navigation") == []


class Recorder(SeatController):
    """Passes every call through and keeps every context it was shown."""

    kind = "recorder"

    def __init__(self, inner: SeatController) -> None:
        self.inner = inner
        self.contexts: list[ActionContext | MeetingContext] = []

    def describe(self) -> dict[str, Any]:
        return self.inner.describe()

    def choose_action(self, ctx: ActionContext) -> Action:
        self.contexts.append(ctx)
        return self.inner.choose_action(ctx)

    def say(self, ctx: ActionContext) -> str:
        return self.inner.say(ctx)

    def speak(self, ctx: MeetingContext) -> str:
        self.contexts.append(ctx)
        return self.inner.speak(ctx)

    def vote(self, ctx: MeetingContext) -> int | None:
        self.contexts.append(ctx)
        return self.inner.vote(ctx)


def harness_prompt(game: Any, ctx: ActionContext | MeetingContext) -> dict[str, Any]:
    if isinstance(ctx, ActionContext):
        request = neutral.action_request(game.rules, ctx)
    elif ctx.pass_index is None:
        request = neutral.vote_request(game.rules, ctx)
    else:
        request = neutral.speak_request(game.rules, ctx)
    return dataclasses.asdict(request)


MANIFEST = make_manifest(POOL, 42, blocks=1, mirror=True)
# A different-Model and a same-Model Impostor Pairing, a Benign Match and a Mirror Match.
SAMPLE = [
    next(g for g in MANIFEST["games"] if g["kind"] == "lobby" and len(set(g["impostors"])) == n)
    for n in (2, 1)
] + [next(g for g in MANIFEST["games"] if g["kind"] == kind) for kind in ("benign", "mirror")]


def scanned_dry_run(game_plan: dict[str, Any]) -> tuple[Any, list[Recorder]]:
    config, spec = game_setup(MANIFEST, game_plan, "dry")
    game = new_game(config, spec)
    recorders = [Recorder(c) for c in game.controllers]
    game.controllers = list(recorders)
    game.run()
    return game, recorders


@pytest.mark.parametrize("game_plan", SAMPLE, ids=lambda g: g["id"])
def test_dry_run_players_never_see_a_model_identity(game_plan: dict[str, Any]) -> None:
    game, recorders = scanned_dry_run(game_plan)
    header = game.transcript.records[0]
    assert identity_leaks(header["seats"])  # ground truth has the Models ...
    for seat, recorder in enumerate(recorders):
        assert recorder.contexts
        contexts = [dataclasses.asdict(c) for c in recorder.contexts]
        observations = [r for r in game.transcript.observations(seat)]
        prompts = [harness_prompt(game, c) for c in recorder.contexts[::4]]
        # ... but nothing a player is shown or would be prompted with does.
        assert identity_leaks(contexts) == [], f"seat {seat} contexts"
        assert identity_leaks(observations) == [], f"seat {seat} observations"
        assert identity_leaks(prompts) == [], f"seat {seat} prompts"


def test_dry_run_room_chat_reaches_the_scan_without_a_model_identity() -> None:
    """Recorder forwards ``say``, so delivered Room Chat is part of what is scanned.
    Scripted holders often die before reading, so take the first scheduled game
    whose dry run has Room Chat."""
    for game_plan in MANIFEST["games"]:
        game, recorders = scanned_dry_run(game_plan)
        chat = list(game.transcript.events("chat.message"))
        if chat:
            break
    else:
        raise AssertionError("no scheduled dry run has Room Chat")
    for message in chat:
        listener = message["data"]["listeners"][0]
        heard = [
            o["data"]["text"]
            for o in game.transcript.observations(listener)
            if o["type"] == "chat.message"
        ]
        assert message["data"]["text"] in heard
        # Every later prompt of that listener shows it, and none names a Model.
        later = [
            c
            for c in recorders[listener].contexts
            if isinstance(c, ActionContext) and c.round > message["round"]
        ]
        if later:
            prompt = harness_prompt(game, later[0])
            assert message["data"]["text"] in json.dumps(prompt, ensure_ascii=False)
            assert identity_leaks(prompt) == []
    for record in game.transcript.observations():
        assert identity_leaks(record) == []


def covering_lobbies() -> list[dict[str, Any]]:
    """Balanced Lobbies whose lineups together seat every Model in the pool."""
    chosen: list[dict[str, Any]] = []
    seated: set[str] = set()
    for g in MANIFEST["games"]:
        if g["kind"] == "lobby" and not set(g["lineup"]) <= seated:
            chosen.append(g)
            seated |= set(g["lineup"])
    assert seated == set(POOL)
    return chosen


@pytest.mark.parametrize("game_plan", covering_lobbies(), ids=lambda g: g["id"])
def test_live_seats_prompts_never_name_a_model(game_plan: dict[str, Any]) -> None:
    """Every provider adapter's wire request, not just Anthropic's, is scanned."""
    config, spec = game_setup(MANIFEST, game_plan, "live")
    config = dataclasses.replace(config, round_cap=4)
    adapters: list[ProviderAdapter] = []

    def factory(model: ModelSpec) -> ProviderAdapter:
        adapters.append(fake_adapter(model))
        return adapters[-1]

    models = {k: dataclasses.replace(v, price_valid_until=None) for k, v in MODELS.items()}
    run = play_live(config, spec, spend_cap_usd=1000, adapter_factory=factory, models=models)
    assert run.aborted is None
    assert {a.model.id for a in adapters} == set(game_plan["lineup"])
    for adapter in adapters:
        calls = recorded_calls(adapter)
        assert len(calls) > 2, adapter.model.id
        for params in calls:
            # The wire Model id is addressed to the lab, never shown to the player.
            assert params.get("model") == adapter.model.id
            shown = {k: v for k, v in params.items() if k != "model"}
            assert identity_leaks(shown) == [], adapter.model.id
    for record in run.game.transcript.observations():
        assert identity_leaks(record) == []
    assert identity_leaks(run.game.transcript.records[0]["seats"])


def recorded_calls(adapter: ProviderAdapter) -> list[dict[str, Any]]:
    client = adapter.client  # type: ignore[attr-defined]
    for path in ("messages", "responses", "models"):
        if hasattr(client, path):
            return getattr(client, path).calls
    raise AssertionError("unknown fake client")
