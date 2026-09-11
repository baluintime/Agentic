"""Test-wide guarantees: tests are offline and never touch the real runtime folder."""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path

import pytest

_real_connect = socket.socket.connect


@pytest.fixture(autouse=True, scope="session")
def runtime_dir_is_temporary() -> None:
    """No test may write into ~/upstox_runtime."""
    import os

    with tempfile.TemporaryDirectory(prefix="upstox-agents-tests-") as folder:
        os.environ["UPSTOX_RUNTIME_DIR"] = str(Path(folder))
        yield


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any real socket connection is a bug: fixtures and mocks only."""

    def refuse(self, address):  # noqa: ANN001
        raise AssertionError(f"tests must not open a network connection ({address!r})")

    monkeypatch.setattr(socket.socket, "connect", refuse)
