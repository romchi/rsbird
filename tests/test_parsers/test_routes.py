"""Parser test: ``show route ...`` across every captured BIRD version."""
from __future__ import annotations

from datetime import datetime

import pytest

from rsbird.models import Community, CommunityKind
from rsbird.parsers.routes import parse_routes
from tests.conftest import load_fixture

ALL_VERSIONS = ("bird-1.6.8-ipv4", "bird-1.6.8-ipv6", "bird-2.0.8", "bird-3.1.2")
V2_PLUS = ("bird-2.0.8", "bird-3.1.2")
V_TABLES = {
    "bird-1.6.8-ipv4": "master",
    "bird-1.6.8-ipv6": "master",
    "bird-2.0.8":      "master4",
    "bird-3.1.2":      "master4",
}


# ---- summary ---------------------------------------------------------------

@pytest.mark.parametrize("version", ALL_VERSIONS)
def test_summary_returns_routes(version):
    table = V_TABLES[version]
    raw = load_fixture(version, "show_route_table", f"{table}.input")
    routes = parse_routes(raw)
    assert routes, "expected at least one route"
    for r in routes:
        assert r.prefix
        assert r.protocol


@pytest.mark.parametrize("version", ALL_VERSIONS)
def test_summary_marks_best(version):
    table = V_TABLES[version]
    raw = load_fixture(version, "show_route_table", f"{table}.input")
    routes = parse_routes(raw)
    # Every prefix must have exactly one best route — assert at least one
    # best exists per known multi-source prefix.
    by_prefix: dict[str, list] = {}
    for r in routes:
        by_prefix.setdefault(r.prefix, []).append(r)
    for prefix in ("10.50.0.0/24",) if version in V2_PLUS else ("10.50.0.0/24",):
        if prefix in by_prefix:
            assert sum(1 for r in by_prefix[prefix] if r.best) == 1


@pytest.mark.parametrize("version", ALL_VERSIONS)
def test_summary_multipath_carries_prefix(version):
    """Multipath continuation rows must inherit the previous PREFIX."""
    table = V_TABLES[version]
    raw = load_fixture(version, "show_route_table", f"{table}.input")
    routes = parse_routes(raw)
    by_prefix: dict[str, list] = {}
    for r in routes:
        by_prefix.setdefault(r.prefix, []).append(r)
    # In the lab every prefix is announced by rs1 and rs2 (and sometimes
    # kernel), so most have 2+ paths.
    multi = [p for p, rs in by_prefix.items() if len(rs) >= 2]
    assert multi, "expected at least one multipath prefix"


@pytest.mark.parametrize("version", V2_PLUS)
def test_v2plus_via_line_populates_nexthop(version):
    raw = load_fixture(version, "show_route_table", f"{V_TABLES[version]}.input")
    routes = parse_routes(raw)
    # rs1 routes must have a peer next-hop on eth0
    rs1 = [r for r in routes if r.protocol == "rs1_ipv4" and r.via]
    assert rs1
    assert all(r.interface == "eth0" for r in rs1)


def test_v1_inline_via_populates_nexthop():
    """BIRD 1.6 puts ``via X on Y`` on the same line as the route header."""
    raw = load_fixture("bird-1.6.8-ipv4", "show_route_table", "master.input")
    routes = parse_routes(raw)
    rs1 = [r for r in routes if r.protocol == "rs1_ipv4"]
    assert rs1
    for r in rs1:
        assert r.via == "10.123.123.10"
        assert r.interface == "eth0"


def test_origin_as_extracted_from_summary():
    """The trailing ``[AS65010i]`` token sets ``origin_as`` on the route."""
    raw = load_fixture("bird-2.0.8", "show_route_table", "master4.input")
    routes = parse_routes(raw)
    seen = {r.origin_as for r in routes if r.origin_as is not None}
    assert 65010 in seen


def test_long_path_prepend_changes_origin_as():
    """rs1's `bgp_to_test` prepends turn 10.10.0.0/24's origin into AS64500."""
    raw = load_fixture("bird-1.6.8-ipv4", "show_route_table", "master.input")
    routes = parse_routes(raw)
    for r in routes:
        if r.prefix == "10.10.0.0/24" and r.protocol == "rs1_ipv4":
            assert r.origin_as == 64500
            return
    pytest.fail("10.10.0.0/24 via rs1_ipv4 not found")


# ---- detail / BGP attributes ----------------------------------------------

def _find_route_with_attrs(version: str, attr_marker: str) -> dict:
    """Walk every ``show_route_for/*.all.input`` until one mentions the marker."""
    import os
    base = f"fixtures/{version}/show_route_for"
    for name in sorted(os.listdir(base)):
        if not name.endswith(".all.input"):
            continue
        raw = load_fixture(version, "show_route_for", name)
        if attr_marker in raw:
            return {"raw": raw, "file": name}
    return {}


def test_bird1_detail_parses_standard_communities():
    """BIRD 1.6 attrs use the ``BGP.community: (a,b) (c,d) ...`` form."""
    found = _find_route_with_attrs("bird-1.6.8-ipv4", "BGP.community")
    assert found, "no 1.6 fixture with BGP.community"
    routes = parse_routes(found["raw"])
    with_comms = [r for r in routes if r.bgp and r.bgp.communities]
    assert with_comms
    c = with_comms[0].bgp.communities[0]
    assert c.kind is CommunityKind.STANDARD


def test_bird2_detail_parses_bgp_dot_attrs():
    """BIRD 2.x keeps the legacy ``BGP.*`` capital-dot attribute keys."""
    found = _find_route_with_attrs("bird-2.0.8", "BGP.community")
    assert found, "no 2.0 fixture with BGP.community"
    routes = parse_routes(found["raw"])
    r = next(r for r in routes if r.bgp and r.bgp.communities)
    assert r.bgp.origin == "IGP"
    assert r.bgp.as_path
    assert r.bgp.local_pref == 100


def test_bird3_detail_parses_lowercase_bgp_attrs():
    """BIRD 3.x emits ``bgp_origin``, ``bgp_path``, ``bgp_community`` lowercased."""
    found = _find_route_with_attrs("bird-3.1.2", "bgp_path")
    assert found, "no 3.1 fixture with bgp_path"
    routes = parse_routes(found["raw"])
    r = next(r for r in routes if r.bgp and r.bgp.as_path)
    assert r.bgp.origin == "IGP"
    assert r.bgp.as_path
    assert r.bgp.local_pref == 100


def test_detail_parses_extended_communities():
    """``BGP.ext_community: (rt, asn, val) ...`` -> typed Community objects."""
    found = _find_route_with_attrs("bird-2.0.8", "BGP.ext_community")
    if not found:
        pytest.skip("no fixture exercises BGP.ext_community in 2.0 capture")
    routes = parse_routes(found["raw"])
    ext = [c for r in routes if r.bgp for c in r.bgp.ext_communities]
    assert ext
    assert ext[0].kind is CommunityKind.EXTENDED
    assert ext[0].parts[0] in {"rt", "ro", "soo"}


def test_detail_parses_large_communities():
    found = _find_route_with_attrs("bird-3.1.2", "bgp_large_community")
    if not found:
        pytest.skip("no fixture exercises bgp_large_community in 3.1 capture")
    routes = parse_routes(found["raw"])
    large = [c for r in routes if r.bgp for c in r.bgp.large_communities]
    assert large
    assert large[0].kind is CommunityKind.LARGE


def test_detail_routes_have_learned_timestamp():
    raw = load_fixture("bird-2.0.8", "show_route_table", "master4.input")
    routes = parse_routes(raw)
    learned = {type(r.learned) for r in routes}
    assert datetime in learned
