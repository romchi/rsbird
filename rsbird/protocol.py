"""
Async I/O layer for the BIRD control socket.

BIRD speaks a line-oriented text protocol: lines are tagged with a 4-digit
numeric code that classifies their role.

    NNNN-...    continuation line within a multi-line block
     ...        space-prefixed body continuation
    NNNN ...    terminal line of a block / reply (when NNNN is a known code)

A reply is complete once a terminal line whose code is in
:data:`TERMINAL_CODES` arrives. On connect BIRD greets the client with
``0001 BIRD <version> ready.`` — that line is parsed into
:class:`BirdVersion` and consumed before any command can be sent.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from rsbird.exceptions import BirdTimeout, ParseError, RsBirdError

# Reply codes that terminate a BIRD response. Success codes are documented
# at https://gitlab.nic.cz/labs/bird/-/blob/master/nest/cli.c — error codes
# 8xxx / 9xxx (range-matched at runtime) terminate too.
TERMINAL_CODES: frozenset[int] = frozenset({0, 3, 4, 13, 18, 19, 20})
GREETING_CODE = 1

# 4 digits + space (terminal) or dash (continuation).
_TAG = re.compile(rb"^(\d{4})([- ])")


@dataclass(slots=True, frozen=True)
class BirdVersion:
    """Parsed ``BIRD x.y.z`` from the greeting line."""

    major: int
    minor: int
    patch: int = 0
    text: str = ""

    def __str__(self) -> str:
        return self.text or f"{self.major}.{self.minor}.{self.patch}"

    def to_tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    @classmethod
    def parse(cls, version: str) -> BirdVersion:
        m = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", version.strip())
        if not m:
            raise ParseError(f"unrecognised BIRD version: {version!r}")
        major, minor = int(m.group(1)), int(m.group(2))
        patch = int(m.group(3)) if m.group(3) else 0
        return cls(major, minor, patch, version.strip())


def _is_terminal(code: int) -> bool:
    return code in TERMINAL_CODES or 8000 <= code <= 9999


class BirdConnection:
    """A single open connection to the BIRD control socket.

    Connections are stateful: the greeting is consumed on open, and the same
    connection can serve any number of subsequent queries.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        version: BirdVersion,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._version = version
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._closed = False

    # ---- properties ----------------------------------------------------

    @property
    def version(self) -> BirdVersion:
        return self._version

    @property
    def closed(self) -> bool:
        return self._closed

    # ---- lifecycle -----------------------------------------------------

    @classmethod
    async def open(
        cls, socket_path: str, *, timeout: float = 30.0,
    ) -> BirdConnection:
        """Open the unix socket and parse BIRD's greeting line."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(socket_path), timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise BirdTimeout(f"timed out connecting to {socket_path}") from exc
        except OSError as exc:
            raise RsBirdError(f"cannot open BIRD socket {socket_path}: {exc}") from exc

        try:
            greeting = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            writer.close()
            raise BirdTimeout("timed out reading BIRD greeting") from exc

        version = cls._parse_greeting(greeting.decode("utf-8", "replace"))
        return cls(reader, writer, version, timeout=timeout)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:  # noqa: BLE001 — closing is best-effort
            pass

    async def __aenter__(self) -> BirdConnection:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    # ---- I/O -----------------------------------------------------------

    async def query(self, command: str, *, timeout: float | None = None) -> str:
        """Send one CLI command and return the full raw reply text.

        ``timeout`` overrides the connection default for this single call.
        """
        if self._closed:
            raise RsBirdError("connection is closed")
        deadline = timeout if timeout is not None else self._timeout
        async with self._lock:
            return await self._send_and_read(command, deadline)

    # ---- internals -----------------------------------------------------

    async def _send_and_read(self, command: str, timeout: float) -> str:
        payload = command.rstrip("\n").encode("utf-8") + b"\n"
        self._writer.write(payload)
        try:
            await asyncio.wait_for(self._writer.drain(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise BirdTimeout(f"send timeout on {command!r}") from exc

        parts: list[bytes] = []
        try:
            while True:
                line = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
                if not line:
                    break
                parts.append(line)
                tag = _TAG.match(line)
                if tag and tag.group(2) == b" ":
                    code = int(tag.group(1))
                    if _is_terminal(code):
                        break
        except asyncio.TimeoutError as exc:
            raise BirdTimeout(f"read timeout on {command!r}") from exc

        return b"".join(parts).decode("utf-8", "replace")

    # ---- greeting ------------------------------------------------------

    @staticmethod
    def _parse_greeting(line: str) -> BirdVersion:
        # Expected: "0001 BIRD 2.0.8 ready.\n"
        m = re.match(r"^0001\s+BIRD\s+(\S+)", line)
        if not m:
            raise ParseError(f"unexpected BIRD greeting: {line!r}", raw=line)
        return BirdVersion.parse(m.group(1))
