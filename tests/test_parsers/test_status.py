"""Parser test: ``show status`` across every captured BIRD version."""
from __future__ import annotations

from datetime import datetime

import pytest

from rsbird.exceptions import ParseError
from rsbird.parsers.status import parse_status
from tests.conftest import load_fixture

# Lab values for each captured version. ``has_hostname`` reflects whether
# the BIRD release emits the ``Hostname is`` line at all — the value itself
# is a random docker container ID, so it isn't pinned.
EXPECTED = {
    "bird-1.6.8-ipv4": {"version": "1.6.8", "router_id": "10.123.123.68",  "has_hostname": False},
    "bird-1.6.8-ipv6": {"version": "1.6.8", "router_id": "10.123.123.168", "has_hostname": False},
    "bird-2.0.8":      {"version": "2.0.8", "router_id": "10.123.123.208", "has_hostname": True},
    "bird-3.1.2":      {"version": "3.1.2", "router_id": "10.123.123.31",  "has_hostname": True},
}


@pytest.mark.parametrize("version", list(EXPECTED.keys()))
def test_parse_status(version):
    raw = load_fixture(version, "show_status", "default.input")
    status = parse_status(raw)

    want = EXPECTED[version]
    assert status.version == want["version"]
    assert status.router_id == want["router_id"]
    if want["has_hostname"]:
        assert isinstance(status.hostname, str) and status.hostname
    else:
        assert status.hostname is None
    # Every version we captured records these three timestamps.
    assert isinstance(status.server_time, datetime)
    assert isinstance(status.last_reboot, datetime)
    assert isinstance(status.last_reconfiguration, datetime)


def test_parse_status_emits_dict_form():
    raw = load_fixture("bird-2.0.8", "show_status", "default.input")
    d = parse_status(raw).to_dict()
    assert d["version"] == "2.0.8"
    assert isinstance(d["hostname"], str) and d["hostname"]
    # Timestamps come out as ISO strings.
    assert isinstance(d["last_reboot"], str)
    assert "T" in d["last_reboot"]


def test_parse_status_handles_microseconds():
    """BIRD 2.x prints fractional seconds — parser must keep them."""
    raw = load_fixture("bird-2.0.8", "show_status", "default.input")
    status = parse_status(raw)
    assert status.last_reboot.microsecond > 0


def test_parse_status_rejects_garbage():
    with pytest.raises(ParseError):
        parse_status("0001 BIRD x.y ready.\n0000 \n")
