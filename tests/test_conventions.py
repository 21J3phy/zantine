import socket

import pytest


def test_network_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="network access is disabled"):
        socket.create_connection(("example.com", 80))


@pytest.mark.live
def test_live_tests_are_skipped_by_default() -> None:
    raise AssertionError("live tests must not run without ZANTINE_LIVE=1")
