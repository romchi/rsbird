"""
:class:`RsBird` — high-level async client.

One :class:`RsBird` instance talks to one BIRD daemon (one control socket).
For BIRD 1.6 deployments with separate v4 and v6 daemons, hold two clients.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from rsbird.exceptions import RsBirdError
from rsbird.models import Community
from rsbird.parsers.configure import parse_configure
from rsbird.parsers.protocols import parse_protocols
from rsbird.parsers.routes import parse_routes
from rsbird.parsers.status import parse_status
from rsbird.parsers.symbols import parse_symbols
from rsbird.protocol import BirdConnection

if TYPE_CHECKING:
    from rsbird.models import ConfigResult, Protocol, Route, Status, Symbol
    from rsbird.protocol import BirdVersion


class RsBird:
    """Async client for one BIRD control socket."""

    def __init__(self, socket_path: str, *, timeout: float = 30.0) -> None:
        self._socket_path = socket_path
        self._timeout = timeout
        self._conn: BirdConnection | None = None

    # ---- lifecycle -----------------------------------------------------

    async def connect(self) -> RsBird:
        """Open the underlying control-socket connection."""
        if self._conn is None or self._conn.closed:
            self._conn = await BirdConnection.open(self._socket_path, timeout=self._timeout)
        return self

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> RsBird:
        return await self.connect()

    async def __aexit__(self, *exc) -> None:
        await self.close()

    @property
    def bird_version(self) -> BirdVersion | None:
        return self._conn.version if self._conn is not None else None

    @property
    def socket_path(self) -> str:
        return self._socket_path

    # ---- queries -------------------------------------------------------

    async def status(self) -> Status:
        """Return parsed ``show status``."""
        raw = await self._query("show status")
        return parse_status(raw, version=self.bird_version)

    async def symbols(self, kind: str | None = None) -> list[Symbol]:
        """Return parsed ``show symbols [<kind>]``.

        ``kind`` is BIRD's symbol-type filter (``"table"``, ``"protocol"``,
        ``"filter"``, ``"function"``, ``"template"``, ``"roa"``). Omit it
        to enumerate everything.
        """
        command = "show symbols" if kind is None else f"show symbols {kind}"
        raw = await self._query(command)
        return parse_symbols(raw, version=self.bird_version)

    async def tables(self) -> list[str]:
        """Names of routing tables BIRD exposes (e.g. ``master4``, ``giganet``)."""
        return [s.name for s in await self.symbols(kind="table")]

    async def protocols(
        self, name: str | None = None, *, detail: bool = True,
    ) -> list[Protocol]:
        """Return parsed ``show protocols [all [<name>]]``.

        With ``detail=False`` issues the bare ``show protocols`` (summary table
        only — no channels, no BGP-state block, no route-change stats).
        ``name`` narrows the query to a single protocol; pair it with
        ``detail=True`` (the default) for the full per-peer dump.
        """
        if not detail:
            command = "show protocols"
        elif name is None:
            command = "show protocols all"
        else:
            command = f"show protocols all {name}"
        raw = await self._query(command)
        return parse_protocols(raw, version=self.bird_version)

    # -------- routes ---------------------------------------------------

    async def routes(
        self, *,
        table: str | None = None,
        prefix: str | None = None,
        protocol: str | None = None,
        where: str | None = None,
        detail: bool = False,
        best: bool = False,
        filtered: bool = False,
    ) -> list[Route]:
        """Return parsed ``show route ...``.

        Parameters compose into BIRD's filter clauses in order:
        ``show route [for PREFIX] [table T] [protocol P] [where EXPR]
        [primary] [filtered] [all]``. ``where`` is the escape hatch for raw
        BIRD filter expressions that this API doesn't model directly.
        """
        parts = ["show route"]
        if prefix is not None:
            parts.append(f"for {prefix}")
        if table is not None:
            parts.append(f"table {table}")
        if protocol is not None:
            parts.append(f"protocol {protocol}")
        if where is not None:
            parts.append(f"where {where}")
        if best:
            parts.append("primary")
        if filtered:
            parts.append("filtered")
        if detail:
            parts.append("all")
        raw = await self._query(" ".join(parts))
        return parse_routes(raw, version=self.bird_version)

    async def routes_exported(
        self, protocol: str, *, detail: bool = False,
    ) -> list[Route]:
        """Return the routes lg-front announces *to* ``protocol``.

        BIRD's ``show route export <peer>`` syntax doesn't accept the usual
        ``table`` / ``where`` filters, so this is a focused helper.
        """
        command = f"show route export {protocol}"
        if detail:
            command += " all"
        raw = await self._query(command)
        return parse_routes(raw, version=self.bird_version)

    async def routes_filtered(
        self, protocol: str, *, detail: bool = False, **kw,
    ) -> list[Route]:
        """Return the routes BIRD's import filter rejected from ``protocol``."""
        return await self.routes(protocol=protocol, filtered=True, detail=detail, **kw)

    # -------- routes by community --------------------------------------

    async def routes_by_community(
        self,
        community: "Community | str | tuple",
        *, table: str | None = None, detail: bool = False, best: bool = False,
    ) -> list[Route]:
        """Routes matching a standard BGP community (``(asn, value)``)."""
        return await self._routes_where_community(
            "bgp_community", community, table=table, detail=detail, best=best,
        )

    async def routes_by_ext_community(
        self,
        ext_community: "Community | str | tuple",
        *, table: str | None = None, detail: bool = False, best: bool = False,
    ) -> list[Route]:
        """Routes matching an extended BGP community (``(rt, asn, value)``)."""
        return await self._routes_where_community(
            "bgp_ext_community", ext_community, table=table, detail=detail, best=best,
        )

    async def routes_by_large_community(
        self,
        large_community: "Community | str | tuple",
        *, table: str | None = None, detail: bool = False, best: bool = False,
    ) -> list[Route]:
        """Routes matching a large BGP community (``(a, b, c)``)."""
        return await self._routes_where_community(
            "bgp_large_community", large_community, table=table, detail=detail, best=best,
        )

    async def _routes_where_community(
        self, attr: str, value, *, table, detail, best,
    ) -> list[Route]:
        c = Community.parse(value)
        # BIRD wants `(a, b)` / `(rt, a, b)` / `(a, b, c)` — comma-separated.
        expr = "(" + ", ".join(str(p) for p in c.parts) + ")"
        return await self.routes(
            table=table, where=f"{attr} ~ [{expr}]", detail=detail, best=best,
        )

    # -------- config ops ------------------------------------------------

    async def config_check(self) -> ConfigResult:
        """Run ``configure check`` — validate the on-disk config without applying.

        Returns ``ok=True`` (terminator ``0020 Configuration OK``) for a clean
        validate, ``ok=False`` for any ``8xxx`` / ``9xxx`` error (typically
        ``8002`` syntax errors) with the BIRD message attached.
        """
        return parse_configure(await self._query("configure check"))

    async def configure(self, *, soft: bool = False) -> ConfigResult:
        """Apply the on-disk config to the running BIRD.

        ``soft=True`` performs a graceful reload (``configure soft``) — peers
        stay up while filters / routes are re-evaluated. Default is a regular
        ``configure`` which BIRD finishes with ``0003 Reconfigured`` (or
        ``0004`` if a multi-step reconfiguration is staged).
        """
        command = "configure soft" if soft else "configure"
        return parse_configure(await self._query(command))

    # ---- internals -----------------------------------------------------

    async def _query(self, command: str) -> str:
        if self._conn is None or self._conn.closed:
            raise RsBirdError("not connected — use `async with RsBird(...)` or call .connect()")
        return await self._conn.query(command)
