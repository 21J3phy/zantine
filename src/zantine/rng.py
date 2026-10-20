"""The single seeded random source threaded through the engine.

Every random decision in a game must come from a ``GameRng`` derived from the
game seed. Never use the ``random`` module's global functions, ``hash()`` (it is
salted per process) or wall-clock time in engine code.

Use ``fork(label)`` to give each consumer (meeting order, each bot, a future
generator) its own independent stream. Adding a new consumer then does not
shift the random sequence any other consumer sees.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import MutableSequence, Sequence
from typing import TypeVar

T = TypeVar("T")


class GameRng:
    def __init__(self, seed: int, path: str = "root") -> None:
        self.seed = seed
        self.path = path
        self._random = random.Random(_derive(seed, path))

    def fork(self, label: str) -> GameRng:
        """An independent stream whose output depends only on (seed, path/label)."""
        return GameRng(self.seed, f"{self.path}/{label}")

    def random(self) -> float:
        return self._random.random()

    def randint(self, a: int, b: int) -> int:
        return self._random.randint(a, b)

    def choice(self, seq: Sequence[T]) -> T:
        return self._random.choice(seq)

    def sample(self, seq: Sequence[T], k: int) -> list[T]:
        return self._random.sample(seq, k)

    def shuffle(self, seq: MutableSequence[T]) -> None:
        self._random.shuffle(seq)

    def shuffled(self, seq: Sequence[T]) -> list[T]:
        out = list(seq)
        self._random.shuffle(out)
        return out


def _derive(seed: int, path: str) -> int:
    digest = hashlib.sha256(f"{seed}:{path}".encode()).digest()
    return int.from_bytes(digest[:8], "big")
