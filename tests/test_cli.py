"""End-to-end tests for ``bin/rsbird`` — the standalone CLI script.

The CLI lives outside the importable :mod:`rsbird` library package, so these
tests drive it the same way users do: spawn ``python bin/rsbird`` as a
subprocess. ``asyncio.create_subprocess_exec`` keeps the MockBird server
servicing the unix socket while the subprocess runs against it.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin" / "rsbird"

_STATUS_REPLY = (
    "1000-BIRD 2.0.8\n"
    "1011-Router ID is 10.0.0.1\n"
    " Hostname is lab\n"
    " Current server time is 2026-05-21 12:00:00\n"
    " Last reboot on 2026-05-20 18:00:00\n"
    " Last reconfiguration on 2026-05-20 18:00:00\n"
    "0013 Daemon is up\n"
)
_PROTOCOLS_ALL = (
    "1002-rs1_ipv4   BGP        ---        up     12:52:11.674  Established   \n"
    "1006-  BGP state:          Established\n"
    "     Neighbor address: 10.0.0.10\n"
    "     Neighbor AS:      65010\n"
    "0000 \n"
)
_PROTOCOLS_BRIEF = (
    "1002-rs1_ipv4   BGP        ---        up     12:52:11.674  Established   \n"
    " rs2_ipv4   BGP        ---        up     12:52:10.923  Established   \n"
    "0000 \n"
)
# Summary reply (no BGP attribute block) — what `show route` without `all` gives.
_ROUTES_REPLY = (
    "1007-Table master4:\n"
    " 10.5.5.0/24          unicast [rs1_ipv4 12:52:11.702] * (100) [AS65010i]\n"
    " \tvia 10.0.0.10 on eth0\n"
    "0000 \n"
)
# Detail reply (with the BGP block) — what `show route ... all` gives, so the
# rich table can fill PEER_AS (as_path head) and the COMMUNITY column.
_ROUTES_DETAIL_REPLY = (
    "1007-Table master4:\n"
    " 10.5.5.0/24          unicast [rs1_ipv4 12:52:11.702] * (100) [AS65010i]\n"
    " \tvia 10.0.0.10 on eth0\n"
    "1008-\tType: BGP univ\n"
    "1012-\tBGP.origin: IGP\n"
    " \tBGP.as_path: 65020 65010\n"
    " \tBGP.next_hop: 10.0.0.10\n"
    " \tBGP.local_pref: 100\n"
    " \tBGP.community: (0,13335) (65010,1)\n"
    "0000 \n"
)


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------

async def run_cli(
    *args: str,
    env: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, str, str]:
    """Spawn ``python bin/rsbird <args>``; return ``(rc, stdout, stderr)``."""
    real_env = os.environ.copy()
    real_env.pop("RSBIRD_SOCKET", None)
    if env:
        real_env.update(env)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(BIN), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=real_env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise
    return proc.returncode or 0, stdout.decode(), stderr.decode()


# ---------------------------------------------------------------------------
# Sanity / argparse
# ---------------------------------------------------------------------------

def test_bin_rsbird_exists_and_is_executable():
    assert BIN.is_file(), f"missing CLI script at {BIN}"
    assert os.access(BIN, os.X_OK), f"{BIN} is not executable"
    assert BIN.read_text().splitlines()[0].startswith("#!"), "missing shebang"


async def test_version_flag():
    from rsbird import __version__
    rc, out, _ = await run_cli("--version")
    assert rc == 0
    assert __version__ in out


async def test_help_lists_subcommands():
    rc, out, _ = await run_cli("--help")
    assert rc == 0
    for sub in ("status", "protocols", "routes", "raw", "config-check"):
        assert sub in out, f"--help missing {sub!r}"


async def test_subcommand_required_exits_nonzero():
    rc, _, err = await run_cli()
    assert rc != 0
    assert "required" in err.lower() or "command" in err.lower()


async def test_missing_socket_errors_out():
    rc, _, err = await run_cli("status")
    assert rc != 0
    assert "RSBIRD_SOCKET" in err or "--socket" in err


# ---------------------------------------------------------------------------
# Commands end-to-end via MockBird
# ---------------------------------------------------------------------------

async def test_status_human(mock_bird):
    mock_bird.on("show status", _STATUS_REPLY)
    rc, out, _ = await run_cli("-s", mock_bird.path, "status")
    assert rc == 0, out
    assert "BIRD 2.0.8" in out
    assert "10.0.0.1" in out
    assert "Hostname:" in out


async def test_status_json(mock_bird):
    mock_bird.on("show status", _STATUS_REPLY)
    rc, out, _ = await run_cli("-s", mock_bird.path, "--json", "status")
    assert rc == 0
    data = json.loads(out)
    assert data["version"] == "2.0.8"
    assert data["hostname"] == "lab"


async def test_socket_from_env_var(mock_bird):
    mock_bird.on("show status", _STATUS_REPLY)
    rc, out, _ = await run_cli("status", env={"RSBIRD_SOCKET": mock_bird.path})
    assert rc == 0
    assert "BIRD 2.0.8" in out


async def test_protocols_default_pulls_detail(mock_bird):
    mock_bird.on("show protocols all", _PROTOCOLS_ALL)
    rc, out, _ = await run_cli("-s", mock_bird.path, "protocols")
    assert rc == 0
    assert "rs1_ipv4" in out
    assert "10.0.0.10" in out


async def test_protocols_brief_skips_detail(mock_bird):
    mock_bird.on("show protocols", _PROTOCOLS_BRIEF)
    rc, out, _ = await run_cli("-s", mock_bird.path, "protocols", "--brief")
    assert rc == 0
    assert "rs1_ipv4" in out
    assert "rs2_ipv4" in out


async def test_tables(mock_bird):
    mock_bird.on(
        "show symbols table",
        "1010-master4\trouting table\n"
        " master6\trouting table\n"
        "0000 \n",
    )
    rc, out, _ = await run_cli("-s", mock_bird.path, "tables")
    assert rc == 0
    assert out.split() == ["master4", "master6"]


async def test_routes_rich_table_has_all_columns(mock_bird):
    """Default routes table fetches detail and shows origin/peer AS + community."""
    mock_bird.on("show route table master4 all", _ROUTES_DETAIL_REPLY)
    rc, out, _ = await run_cli("-s", mock_bird.path, "routes", "-t", "master4")
    assert rc == 0, out
    # Column headers
    for col in ("ORIGIN_AS", "PEER_AS", "PEER_IP", "PEER_NAME", "COMMUNITY"):
        assert col in out, f"missing column {col}"
    # Values
    assert "10.5.5.0/24" in out
    assert "65010" in out        # ORIGIN_AS (from the [AS65010i] marker)
    assert "65020" in out        # PEER_AS (head of as_path 65020 65010)
    assert "10.0.0.10" in out    # PEER_IP
    assert "rs1_ipv4" in out     # PEER_NAME
    assert "0:13335" in out      # COMMUNITY (all present)
    assert "65010:1" in out


async def test_routes_brief_skips_detail_and_extra_columns(mock_bird):
    """--brief is the fast path: no detail fetch, no PEER_AS/COMMUNITY columns."""
    mock_bird.on("show route table master4", _ROUTES_REPLY)
    rc, out, _ = await run_cli(
        "-s", mock_bird.path, "routes", "-t", "master4", "--brief",
    )
    assert rc == 0, out
    assert "10.5.5.0/24" in out
    assert "PEER_AS" not in out
    assert "COMMUNITY" not in out


async def test_route_shortcut(mock_bird):
    mock_bird.on("show route for 10.5.5.0 all", _ROUTES_DETAIL_REPLY)
    rc, out, _ = await run_cli("-s", mock_bird.path, "route", "10.5.5.0")
    assert rc == 0
    assert "10.5.5.0/24" in out
    assert "65020" in out  # PEER_AS populated from detail


async def test_routes_detail_renders_bgp_block(mock_bird):
    mock_bird.on(
        "show route table master4 all",
        "1007-Table master4:\n"
        " 10.5.5.0/24          unicast [rs1_ipv4 12:52:11.702] * (100) [AS65010i]\n"
        " \tvia 10.0.0.10 on eth0\n"
        "1008-\tType: BGP univ\n"
        "1012-\tBGP.origin: IGP\n"
        " \tBGP.as_path: 65010\n"
        " \tBGP.community: (65010,1) (65010,2)\n"
        "0000 \n",
    )
    rc, out, _ = await run_cli(
        "-s", mock_bird.path, "routes", "-t", "master4", "-d",
    )
    assert rc == 0
    assert "AS path:" in out
    assert "Community:" in out


async def test_neighbor_lookup(mock_bird):
    mock_bird.on("show protocols all", _PROTOCOLS_ALL)
    rc, out, _ = await run_cli("-s", mock_bird.path, "neighbor", "10.0.0.10")
    assert rc == 0
    assert "Peer: rs1_ipv4" in out
    assert "65010" in out


async def test_neighbor_unknown_returns_nonzero(mock_bird):
    mock_bird.on("show protocols all", _PROTOCOLS_ALL)
    rc, _, err = await run_cli("-s", mock_bird.path, "neighbor", "9.9.9.9")
    assert rc != 0
    assert "9.9.9.9" in err


async def test_community(mock_bird):
    # Community lookups fetch detail (note the trailing `all`).
    mock_bird.on(
        "show route table master4 where bgp_community ~ [(65010, 1)] all",
        _ROUTES_DETAIL_REPLY,
    )
    rc, out, _ = await run_cli(
        "-s", mock_bird.path, "community", "65010:1", "-t", "master4",
    )
    assert rc == 0
    assert "10.5.5.0/24" in out
    assert "0:13335" in out  # COMMUNITY column populated


async def test_ext_community(mock_bird):
    mock_bird.on(
        "show route table master4 where bgp_ext_community ~ [(rt, 65010, 1)] all",
        _ROUTES_DETAIL_REPLY,
    )
    rc, _, _ = await run_cli(
        "-s", mock_bird.path, "ext-community", "rt:65010:1", "-t", "master4",
    )
    assert rc == 0


async def test_large_community(mock_bird):
    mock_bird.on(
        "show route table master4 where bgp_large_community ~ [(65010, 1, 2)] all",
        _ROUTES_DETAIL_REPLY,
    )
    rc, _, _ = await run_cli(
        "-s", mock_bird.path, "large-community", "65010:1:2", "-t", "master4",
    )
    assert rc == 0


async def test_config_check_ok(mock_bird):
    mock_bird.on(
        "configure check",
        "0002-Reading configuration from /etc/bird.conf\n0020 Configuration OK\n",
    )
    rc, out, _ = await run_cli("-s", mock_bird.path, "config-check")
    assert rc == 0
    assert "[OK]" in out
    assert "/etc/bird.conf" in out


async def test_config_check_error_returns_nonzero(mock_bird):
    mock_bird.on(
        "configure check",
        "0002-Reading configuration from /etc/bird.conf\n"
        "8002 syntax error\n",
    )
    rc, out, _ = await run_cli("-s", mock_bird.path, "config-check")
    assert rc == 1
    assert "[ERROR]" in out


# ---- raw -------------------------------------------------------------------

async def test_raw_dumps_reply_verbatim(mock_bird):
    """The raw subcommand prints the bytes BIRD sent — no parsing."""
    payload = "1007-hand-rolled output\n with continuation\n0000 \n"
    mock_bird.on("show route count", payload)
    rc, out, _ = await run_cli("-s", mock_bird.path, "raw", "show route count")
    assert rc == 0
    assert "1007-hand-rolled output" in out
    assert "0000" in out


async def test_raw_handles_arbitrary_command(mock_bird):
    """The escape hatch for commands the typed API does not model."""
    mock_bird.on(
        "show memory",
        "1018-BIRD memory usage\n Routing tables:      256 kB\n0000 \n",
    )
    rc, out, _ = await run_cli("-s", mock_bird.path, "raw", "show memory")
    assert rc == 0
    assert "Routing tables" in out
