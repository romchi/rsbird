"""End-to-end client tests through MockBird."""
from __future__ import annotations

import pytest

from rsbird.client import RsBird
from rsbird.exceptions import RsBirdError


async def test_status_round_trip(mock_bird):
    """The async client wires protocol + parser into a single call."""
    mock_bird.version = "2.0.8"
    mock_bird.on(
        "show status",
        "1000-BIRD 2.0.8\n"
        "1011-Router ID is 10.0.0.1\n"
        " Hostname is lab\n"
        " Current server time is 2026-05-21 12:00:00.123\n"
        " Last reboot on 2026-05-20 18:00:00.000\n"
        " Last reconfiguration on 2026-05-20 18:00:00.000\n"
        "0013 Daemon is up and running\n",
    )
    async with RsBird(mock_bird.path) as client:
        assert client.bird_version is not None
        assert client.bird_version.to_tuple() == (2, 0, 8)
        status = await client.status()
        assert status.version == "2.0.8"
        assert status.router_id == "10.0.0.1"
        assert status.hostname == "lab"


async def test_query_without_connect_raises(mock_bird):
    client = RsBird(mock_bird.path)
    with pytest.raises(RsBirdError):
        await client.status()


async def test_explicit_connect_close(mock_bird):
    mock_bird.on("show status",
                 "1000-BIRD 2.0.8\n1011-Router ID is 1.1.1.1\n0013 OK\n")
    client = RsBird(mock_bird.path)
    await client.connect()
    try:
        s = await client.status()
        assert s.router_id == "1.1.1.1"
    finally:
        await client.close()


async def test_symbols_and_tables(mock_bird):
    """`tables()` is a thin filter over `symbols()`."""
    mock_bird.on(
        "show symbols",
        "1010-master4\trouting table\n"
        " master6\trouting table\n"
        " rs1_ipv4\tprotocol\n"
        " ips_site\tconstant\n"
        "0000 \n",
    )
    mock_bird.on(
        "show symbols table",
        "1010-master4\trouting table\n"
        " master6\trouting table\n"
        "0000 \n",
    )
    async with RsBird(mock_bird.path) as client:
        all_syms = await client.symbols()
        assert {s.name for s in all_syms} == {"master4", "master6", "rs1_ipv4", "ips_site"}
        assert await client.tables() == ["master4", "master6"]


# ---- routes ---------------------------------------------------------------

_ROUTE_REPLY = (
    "1007-Table master4:\n"
    " 10.5.5.0/24          unicast [rs1_ipv4 12:52:11.702] * (100) [AS65010i]\n"
    " \tvia 10.123.123.10 on eth0\n"
    "1008-\tType: BGP univ\n"
    "1012-\tBGP.origin: IGP\n"
    " \tBGP.as_path: 65010\n"
    " \tBGP.next_hop: 10.123.123.10\n"
    " \tBGP.local_pref: 100\n"
    " \tBGP.community: (65010,1) (65010,2)\n"
    "0000 \n"
)


async def test_routes_builds_full_command(mock_bird):
    """Every option composes into the canonical BIRD filter ordering."""
    mock_bird.on(
        "show route for 10.5.5.0/24 table master4 protocol rs1_ipv4 where bgp_med = 150 primary all",
        _ROUTE_REPLY,
    )
    async with RsBird(mock_bird.path) as client:
        result = await client.routes(
            table="master4", prefix="10.5.5.0/24",
            protocol="rs1_ipv4", where="bgp_med = 150",
            best=True, detail=True,
        )
    assert len(result) == 1
    r = result[0]
    assert r.prefix == "10.5.5.0/24"
    assert r.protocol == "rs1_ipv4"
    assert r.best
    assert r.bgp and r.bgp.origin == "IGP"


async def test_routes_by_community_uses_where_clause(mock_bird):
    """`routes_by_community` synthesises a BIRD `where bgp_community ~ [(...)]`."""
    mock_bird.on(
        "show route table master4 where bgp_community ~ [(65010, 1)]",
        _ROUTE_REPLY,
    )
    async with RsBird(mock_bird.path) as client:
        result = await client.routes_by_community("65010:1", table="master4")
    assert result[0].prefix == "10.5.5.0/24"


async def test_routes_by_ext_community_uses_ext_attr(mock_bird):
    mock_bird.on(
        "show route table master4 where bgp_ext_community ~ [(rt, 65010, 1)]",
        _ROUTE_REPLY,
    )
    async with RsBird(mock_bird.path) as client:
        result = await client.routes_by_ext_community("rt:65010:1", table="master4")
    assert result[0].prefix == "10.5.5.0/24"


async def test_routes_by_large_community_uses_large_attr(mock_bird):
    mock_bird.on(
        "show route table master4 where bgp_large_community ~ [(65010, 1, 2)]",
        _ROUTE_REPLY,
    )
    async with RsBird(mock_bird.path) as client:
        result = await client.routes_by_large_community("65010:1:2", table="master4")
    assert result[0].prefix == "10.5.5.0/24"


async def test_routes_exported_and_filtered(mock_bird):
    mock_bird.on("show route export rs1_ipv4", _ROUTE_REPLY)
    mock_bird.on("show route protocol rs1_ipv4 filtered", _ROUTE_REPLY)
    async with RsBird(mock_bird.path) as client:
        exported = await client.routes_exported("rs1_ipv4")
        filtered = await client.routes_filtered("rs1_ipv4")
    assert exported[0].prefix == "10.5.5.0/24"
    assert filtered[0].prefix == "10.5.5.0/24"
