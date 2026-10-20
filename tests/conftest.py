from __future__ import annotations

import os
import socket
from typing import Any

import pytest

LIVE = os.environ.get("ZANTINE_LIVE") == "1"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--live", action="store_true", help="run tests that call paid APIs")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Tests marked ``live`` may call a paid API; they run only with ZANTINE_LIVE=1 and --live."""
    if LIVE and config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="live API test; needs ZANTINE_LIVE=1 and pytest --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never touch the network (and so never call a paid API), except live tests."""
    if request.node.get_closest_marker("live"):
        return

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("network access is disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
