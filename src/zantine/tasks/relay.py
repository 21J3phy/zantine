"""Chain relay: a part carried through 3 rooms by 2 carriers.

The first carrier picks the part up in the pickup room and hands it to the
second carrier in the handoff room; the second carrier delivers it to the
delivery room. The work is coordination (meeting points, staying alive), not a
puzzle, so a relay has no solo form.

The answer key is the part's serial number, which no player is ever shown. The
part's actual serial travels with it (``zantine.rules.relay``); tampering
changes it, so a tampered part fails grading at delivery while looking exactly
like an intact one to every player.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from zantine.rng import GameRng
from zantine.tasks.base import Answer, TaskContent, TaskKind, register_task_kind

_PARTS = (
    ("coolant valve", "CV"),
    ("navigation chip", "NC"),
    ("power coupling", "PC"),
    ("oxygen filter", "OF"),
    ("shield capacitor", "SC"),
    ("fuel injector", "FI"),
    ("comms relay board", "CR"),
    ("scanner lens", "SL"),
)

# Room roles along the route, in order.
STOPS = ("pickup", "handoff", "delivery")


@register_task_kind
class ChainRelay(TaskKind):
    """Carry a part from pickup to delivery, handing it over halfway."""

    name = "chain_relay"
    seats = 2
    work_actions = ("relay.pick_up", "relay.hand_off", "relay.deliver")

    def generate(self, rng: GameRng) -> TaskContent:
        part, prefix = rng.choice(_PARTS)
        serial = f"{prefix}-{rng.randint(1000, 9999)}"
        return TaskContent(
            kind=self.name,
            title=f"Relay the {part}",
            instructions=(
                f"Carry the {part} from the pickup room to the delivery room. The first "
                "carrier picks it up and hands it to the second carrier in the handoff "
                "room; a handoff only happens if both carriers end the Round there. The "
                "second carrier delivers it."
            ),
            # Rooms are bound when the relay is placed in a game (``bind``).
            inputs={"part": part, "rooms": None},
            answer=serial,
        )

    def bind(self, content: TaskContent, pickup: str, handoff: str, delivery: str) -> TaskContent:
        """Place the relay on a map: fix its three rooms."""
        rooms = dict(zip(STOPS, (pickup, handoff, delivery), strict=True))
        return replace(content, inputs={**content.inputs, "rooms": rooms})

    def grade(self, content: TaskContent, answer: Answer) -> bool:
        """``answer`` is the delivered part's actual serial (never player input)."""
        return answer == content.answer

    def brief(self, content: TaskContent, slot: int = 0) -> dict[str, Any]:
        rooms = content.inputs["rooms"]
        if slot == 0:
            role = (
                f"You are the first carrier: pick up the {content.inputs['part']} in "
                f"{rooms['pickup']} and hand it off in {rooms['handoff']}."
            )
        else:
            role = (
                f"You are the second carrier: receive the {content.inputs['part']} in "
                f"{rooms['handoff']} and deliver it to {rooms['delivery']}."
            )
        return {
            **super().brief(content, slot),
            "instructions": f"{content.instructions} {role}",
            "carrier": slot + 1,
        }

    def solo_brief(self, content: TaskContent) -> dict[str, Any]:
        raise NotImplementedError("a chain relay needs two carriers on a map; it has no solo form")

    def solve(self, brief: dict[str, Any]) -> Answer:
        """The carrier's plan: which relay action to take in which room, in order."""
        rooms = brief["inputs"]["rooms"]
        if brief["carrier"] == 1:
            return [
                {"action": "relay.pick_up", "room": rooms["pickup"]},
                {"action": "relay.hand_off", "room": rooms["handoff"]},
            ]
        return [
            {"action": "receive", "room": rooms["handoff"]},
            {"action": "relay.deliver", "room": rooms["delivery"]},
        ]
