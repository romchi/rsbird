"""Parser test: ``show protocols`` and ``show protocols all``."""
from __future__ import annotations

import pytest

from rsbird.models import BgpState
from rsbird.parsers.protocols import parse_protocols
from tests.conftest import load_fixture

ALL_VERSIONS = ("bird-1.6.8-ipv4", "bird-1.6.8-ipv6", "bird-2.0.8", "bird-3.1.2")
V2_PLUS = ("bird-2.0.8", "bird-3.1.2")
V1 = ("bird-1.6.8-ipv4", "bird-1.6.8-ipv6")


# ---- summary table (`show protocols`) --------------------------------------

@pytest.mark.parametrize("version", ALL_VERSIONS)
def test_summary_lists_every_protocol(version):
    raw = load_fixture(version, "show_protocols", "default.input")
    protos = parse_protocols(raw)
    names = {p.name for p in protos}
    # rs1 must be there regardless of BIRD version / AF
    assert any(n.startswith("rs1_") for n in names), names


@pytest.mark.parametrize("version", ALL_VERSIONS)
def test_summary_bgp_peers_are_up(version):
    raw = load_fixture(version, "show_protocols", "default.input")
    bgp = [p for p in parse_protocols(raw) if p.proto == "BGP"]
    assert bgp, "expected at least one BGP protocol"
    for p in bgp:
        assert p.up is True
        # Summary "info" column carries the BGP session state for BGP rows.
        assert p.info == "Established"


def test_summary_handles_no_info_column():
    """Non-BGP protocols leave the trailing info column blank."""
    raw = load_fixture("bird-2.0.8", "show_protocols", "default.input")
    by = {p.name: p for p in parse_protocols(raw)}
    assert by["device1"].info is None
    assert by["device1"].proto == "Device"


def test_summary_translates_dashed_table():
    """BIRD 2.x prints ``---`` for protocols without a single table."""
    raw = load_fixture("bird-2.0.8", "show_protocols", "default.input")
    by = {p.name: p for p in parse_protocols(raw)}
    assert by["device1"].table is None
    assert by["kernel4"].table == "master4"


# ---- per-peer detail (`show protocols all <name>`) -------------------------

@pytest.mark.parametrize("version", V1)
def test_v1_detail_uses_protocol_level_counters(version):
    """BIRD 1.6 has no channels: filters/counters sit on the Protocol itself."""
    raw = load_fixture(version, "show_protocols_all", "rs1_ipv4.input"
                       if "ipv4" in version else "rs1_ipv6.input")
    p = parse_protocols(raw)[0]
    assert p.proto == "BGP"
    assert p.channels == []
    assert p.input_filter == "reject_test_filtered"
    assert p.output_filter == "ACCEPT"
    assert p.routes is not None and p.routes.imported >= 1
    assert p.import_updates is not None and p.import_updates.received is not None


@pytest.mark.parametrize("version", V2_PLUS)
def test_v2plus_detail_uses_channels(version):
    """BIRD 2.x/3.x: counters and filters live inside the Channel sub-block."""
    raw = load_fixture(version, "show_protocols_all", "rs1_ipv4.input")
    p = parse_protocols(raw)[0]
    assert p.proto == "BGP"
    assert len(p.channels) == 1
    ch = p.channels[0]
    assert ch.name == "ipv4"
    assert ch.table == "master4"
    assert ch.input_filter == "reject_test_filtered_v4"
    assert ch.output_filter == "ACCEPT"
    assert ch.routes.imported >= 1
    assert ch.import_updates.received is not None


@pytest.mark.parametrize("version", ALL_VERSIONS)
def test_detail_carries_bgp_neighbour_info(version):
    """Every version reports neighbour address/AS/ID in the BGP state block."""
    fname = "rs1_ipv4.input" if "ipv4" in version or "ipv6" not in version else "rs1_ipv6.input"
    raw = load_fixture(version, "show_protocols_all", fname)
    p = parse_protocols(raw)[0]
    assert p.bgp_state == BgpState.ESTABLISHED
    assert p.neighbor_as == 65010
    assert p.neighbor_address is not None
    assert p.neighbor_id is not None
    assert p.hold_timer is not None
    assert p.keepalive_timer is not None


def test_v3_detail_carries_created_and_channel_states():
    """BIRD 3.x extras: Created on the protocol, Import/Export state on the channel."""
    raw = load_fixture("bird-3.1.2", "show_protocols_all", "rs1_ipv4.input")
    p = parse_protocols(raw)[0]
    assert p.created is not None
    ch = p.channels[0]
    assert ch.import_state == "UP"
    assert ch.export_state == "READY"


def test_v3_detail_captures_rx_limit_and_limit_columns():
    """BIRD 3.x widens the route-change stats matrix by two columns."""
    raw = load_fixture("bird-3.1.2", "show_protocols_all", "rs1_ipv4.input")
    p = parse_protocols(raw)[0]
    ch = p.channels[0]
    # These attributes only get set when the BIRD-3 header is parsed.
    assert ch.import_updates.rx_limit is not None
    assert ch.import_updates.limit is not None


# ---- multi-protocol detail (`show protocols all`) --------------------------

@pytest.mark.parametrize("version", V2_PLUS)
def test_show_protocols_all_default_includes_every_protocol(version):
    """Default `show protocols all` dumps every protocol with its detail block."""
    raw = load_fixture(version, "show_protocols_all", "default.input")
    protos = parse_protocols(raw)
    names = {p.name for p in protos}
    # Lab has kernel/static for each AF + 4 BGP peers
    for must in ("kernel4", "kernel6", "static4", "static6",
                 "rs1_ipv4", "rs2_ipv4", "rs1_ipv6", "rs2_ipv6"):
        assert must in names, (must, names)


def test_show_protocols_all_default_finalises_last_protocol():
    """The final protocol must be flushed even though no `1002-` follows it."""
    raw = load_fixture("bird-3.1.2", "show_protocols_all", "default.input")
    protos = parse_protocols(raw)
    assert protos[-1].name in {"direct4", "direct6", "device1", "rs2_ipv6"}
