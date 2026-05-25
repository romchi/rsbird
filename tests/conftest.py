"""Shared pytest fixtures."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"

# Versions present in the captured corpus; tests parametrise over these.
ALL_VERSIONS = (
    "bird-1.6.8-ipv4",
    "bird-1.6.8-ipv6",
    "bird-2.0.8",
    "bird-3.1.2",
)


@pytest.fixture(scope="session")
def fixtures_root() -> Path:
    return FIXTURES_ROOT


def load_fixture(version: str, *parts: str) -> str:
    """Read a captured fixture file as text."""
    return (FIXTURES_ROOT / version / Path(*parts)).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# MockBird — a real unix-socket server that replays canned BIRD-style replies.
# ---------------------------------------------------------------------------

class MockBirdServer:
    """Minimal BIRD-style server for protocol/client integration tests.

    Listens on a temp unix socket, sends the greeting on connect, and replies
    to each line with whatever was registered via :meth:`on`. Unknown commands
    get a synthetic ``9001`` error reply so the connection always finishes.
    """

    def __init__(self, version: str = "2.0.8") -> None:
        self.version = version
        self.responses: dict[str, bytes] = {}
        self.path: str = ""
        self._server: asyncio.base_events.Server | None = None

    def on(self, command: str, response: str | bytes) -> None:
        if isinstance(response, str):
            response = response.encode("utf-8")
        if not response.endswith(b"\n"):
            response += b"\n"
        self.responses[command] = response

    async def start(self) -> str:
        tmp = tempfile.NamedTemporaryFile(prefix="mockbird-", suffix=".sock", delete=False)
        tmp.close()
        os.unlink(tmp.name)
        self.path = tmp.name
        self._server = await asyncio.start_unix_server(self._handle, path=self.path)
        return self.path

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self.path and os.path.exists(self.path):
            try:
                os.unlink(self.path)
            except OSError:
                pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(f"0001 BIRD {self.version} ready.\n".encode())
        await writer.drain()
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                command = line.decode("utf-8", "replace").strip()
                reply = self.responses.get(
                    command, f"9001 unknown command: {command}\n".encode(),
                )
                writer.write(reply)
                await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001 — closing is best-effort
                pass


@pytest_asyncio.fixture
async def mock_bird():
    """Yield a started :class:`MockBirdServer`; stop it on teardown."""
    server = MockBirdServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest_asyncio.fixture
async def mock_bird_pair():
    """Yield two started :class:`MockBirdServer`s (think v4 / v6 daemons)."""
    v4 = MockBirdServer(version="1.6.8")
    v6 = MockBirdServer(version="1.6.8")
    await v4.start()
    await v6.start()
    try:
        yield v4, v6
    finally:
        await v4.stop()
        await v6.stop()
