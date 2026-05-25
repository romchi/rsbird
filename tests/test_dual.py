"""Tests for :class:`DualStackBird` — the BIRD 1.6 two-daemon helper."""
from __future__ import annotations

import pytest

from rsbird.dual import DualStackBird

_STATUS_REPLY = (
    "1000-BIRD 1.6.8\n"
    "1011-Router ID is {router_id}\n"
    " Current server time is 2026-05-21 12:00:00\n"
    " Last reboot on 2026-05-21 11:00:00\n"
    " Last reconfiguration on 2026-05-21 11:00:00\n"
    "0013 Daemon is up\n"
)


async def test_routes_ip_version_picks_correct_socket(mock_bird_pair):
    """Each method routes by ``ip_version`` to the right daemon."""
    v4, v6 = mock_bird_pair
    v4.on("show status", _STATUS_REPLY.format(router_id="10.0.0.4"))
    v6.on("show status", _STATUS_REPLY.format(router_id="10.0.0.6"))

    async with DualStackBird(socket_v4=v4.path, socket_v6=v6.path) as bird:
        s4 = await bird.status(ip_version=4)
        s6 = await bird.status(ip_version=6)
        assert s4.router_id == "10.0.0.4"
        assert s6.router_id == "10.0.0.6"


async def test_for_version_returns_underlying_client(mock_bird_pair):
    v4, v6 = mock_bird_pair
    async with DualStackBird(socket_v4=v4.path, socket_v6=v6.path) as bird:
        assert bird.for_version(4) is bird.v4
        assert bird.for_version(6) is bird.v6


async def test_invalid_ip_version_raises(mock_bird_pair):
    v4, v6 = mock_bird_pair
    async with DualStackBird(socket_v4=v4.path, socket_v6=v6.path) as bird:
        with pytest.raises(ValueError):
            await bird.status(ip_version=42)


async def test_connect_and_close_run_in_parallel(mock_bird_pair):
    """``connect()`` and ``close()`` fan out both daemons concurrently."""
    v4, v6 = mock_bird_pair
    bird = DualStackBird(socket_v4=v4.path, socket_v6=v6.path)
    await bird.connect()
    assert bird.v4.bird_version is not None
    assert bird.v6.bird_version is not None
    await bird.close()


async def test_routes_kwargs_flow_through(mock_bird_pair):
    """Method kwargs reach the underlying client unchanged."""
    v4, v6 = mock_bird_pair
    canned = (
        "1007-Table master:\n"
        " 10.5.5.0/24          unicast [rs1_ipv4 12:00:00] * (100) [AS65010i]\n"
        " \tvia 10.0.0.1 on eth0\n"
        "0000 \n"
    )
    v4.on("show route table master4", canned)
    async with DualStackBird(socket_v4=v4.path, socket_v6=v6.path) as bird:
        routes = await bird.routes(ip_version=4, table="master4")
    assert routes[0].prefix == "10.5.5.0/24"
