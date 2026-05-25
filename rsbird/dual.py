"""
:class:`DualStackBird` — convenience wrapper for the BIRD 1.6 split-daemon
deployment where IPv4 and IPv6 each have their own ``birdc.ctl`` socket.

A single :class:`DualStackBird` holds two :class:`RsBird` instances. Every
RPC-style method mirrors the corresponding ``RsBird`` method but takes a
required ``ip_version`` keyword (``4`` or ``6``) that selects which daemon
to talk to. Direct access via ``.v4`` / ``.v6`` is also exposed for callers
who prefer plain client objects.

For a single-daemon BIRD 2.x/3.x setup this class still works — pass the
same socket path twice and you get two cheap unix-socket connections to the
same daemon (BIRD is happy to accept that).
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from rsbird.client import RsBird

if TYPE_CHECKING:
    from rsbird.models import (
        Community,
        Protocol,
        Route,
        Status,
        Symbol,
    )


class DualStackBird:
    """Two :class:`RsBird` daemons stitched together by IP version."""

    def __init__(
        self,
        *,
        socket_v4: str,
        socket_v6: str,
        timeout: float = 30.0,
    ) -> None:
        self.v4 = RsBird(socket_v4, timeout=timeout)
        self.v6 = RsBird(socket_v6, timeout=timeout)

    # ---- lifecycle -----------------------------------------------------

    async def connect(self) -> DualStackBird:
        await asyncio.gather(self.v4.connect(), self.v6.connect())
        return self

    async def close(self) -> None:
        await asyncio.gather(
            self.v4.close(), self.v6.close(), return_exceptions=True,
        )

    async def __aenter__(self) -> DualStackBird:
        return await self.connect()

    async def __aexit__(self, *exc) -> None:
        await self.close()

    # ---- routing -------------------------------------------------------

    def for_version(self, ip_version: int) -> RsBird:
        """Return the underlying :class:`RsBird` for ``ip_version`` (4 or 6)."""
        if ip_version == 4:
            return self.v4
        if ip_version == 6:
            return self.v6
        raise ValueError(f"ip_version must be 4 or 6, got {ip_version!r}")

    # ---- mirrored queries ---------------------------------------------

    async def status(self, *, ip_version: int) -> Status:
        return await self.for_version(ip_version).status()

    async def symbols(
        self, kind: str | None = None, *, ip_version: int,
    ) -> list[Symbol]:
        return await self.for_version(ip_version).symbols(kind)

    async def tables(self, *, ip_version: int) -> list[str]:
        return await self.for_version(ip_version).tables()

    async def protocols(
        self, name: str | None = None, *, ip_version: int, detail: bool = True,
    ) -> list[Protocol]:
        return await self.for_version(ip_version).protocols(name, detail=detail)

    async def routes(self, *, ip_version: int, **kw) -> list[Route]:
        return await self.for_version(ip_version).routes(**kw)

    async def routes_exported(
        self, protocol: str, *, ip_version: int, **kw,
    ) -> list[Route]:
        return await self.for_version(ip_version).routes_exported(protocol, **kw)

    async def routes_filtered(
        self, protocol: str, *, ip_version: int, **kw,
    ) -> list[Route]:
        return await self.for_version(ip_version).routes_filtered(protocol, **kw)

    async def routes_by_community(
        self,
        community: Community | str | tuple,
        *, ip_version: int, **kw,
    ) -> list[Route]:
        return await self.for_version(ip_version).routes_by_community(community, **kw)

    async def routes_by_ext_community(
        self,
        ext_community: Community | str | tuple,
        *, ip_version: int, **kw,
    ) -> list[Route]:
        return await self.for_version(ip_version).routes_by_ext_community(
            ext_community, **kw,
        )

    async def routes_by_large_community(
        self,
        large_community: Community | str | tuple,
        *, ip_version: int, **kw,
    ) -> list[Route]:
        return await self.for_version(ip_version).routes_by_large_community(
            large_community, **kw,
        )
