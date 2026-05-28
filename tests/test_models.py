"""Tests for the data model — Community parsing, to_dict serialisation."""
from __future__ import annotations

from datetime import datetime

import pytest

from rsbird.models import (
    BgpAttrs,
    Community,
    CommunityKind,
    Route,
    Status,
)


# ---- Community --------------------------------------------------------------

class TestCommunity:
    def test_standard_from_string(self):
        c = Community.parse("0:64496")
        assert c.kind is CommunityKind.STANDARD
        assert c.parts == (0, 64496)
        assert str(c) == "0:64496"

    def test_large_from_string(self):
        c = Community.parse("65010:1:2")
        assert c.kind is CommunityKind.LARGE
        assert c.parts == (65010, 1, 2)
        assert str(c) == "65010:1:2"

    def test_extended_from_string(self):
        c = Community.parse("rt:65010:1")
        assert c.kind is CommunityKind.EXTENDED
        assert c.parts == ("rt", 65010, 1)
        assert str(c) == "rt:65010:1"

    def test_parse_from_tuple(self):
        assert Community.parse((0, 64496)).kind is CommunityKind.STANDARD
        assert Community.parse(("rt", 0, 64550)).kind is CommunityKind.EXTENDED
        assert Community.parse((64500, 1, 2)).kind is CommunityKind.LARGE

    def test_parse_passes_existing_community_through(self):
        c = Community.standard(0, 64496)
        assert Community.parse(c) is c

    def test_parse_rejects_garbage(self):
        with pytest.raises(ValueError):
            Community.parse("")
        with pytest.raises(ValueError):
            Community.parse("just-one")

    def test_to_dict_serialises_kind_and_parts(self):
        d = Community.parse("rt:65010:1").to_dict()
        assert d == {"kind": "extended", "parts": ["rt", 65010, 1], "str": "rt:65010:1"}


# ---- to_dict / serialisation -----------------------------------------------

class TestSerialisation:
    def test_status_round_trip(self):
        s = Status(
            version="2.0.8",
            router_id="10.0.0.1",
            hostname="lab",
            server_time=datetime(2026, 5, 21, 12, 0, 0),
            last_reboot=datetime(2026, 5, 20, 20, 18, 4),
            last_reconfiguration=None,
        )
        d = s.to_dict()
        assert d["version"] == "2.0.8"
        assert d["router_id"] == "10.0.0.1"
        assert d["hostname"] == "lab"
        assert d["server_time"] == "2026-05-21T12:00:00"
        assert d["last_reboot"] == "2026-05-20T20:18:04"
        assert d["last_reconfiguration"] is None

    def test_route_with_bgp_attrs(self):
        bgp = BgpAttrs(
            origin="IGP",
            as_path=[65010, 65020],
            next_hop=["10.0.0.1"],
            local_pref=100,
            communities=[Community.parse("0:64496")],
        )
        r = Route(prefix="10.5.5.0/24", best=True, bgp=bgp)
        d = r.to_dict()
        assert d["prefix"] == "10.5.5.0/24"
        assert d["best"] is True
        assert d["bgp"]["origin"] == "IGP"
        assert d["bgp"]["as_path"] == [65010, 65020]
        assert d["bgp"]["communities"][0]["str"] == "0:64496"

    def test_as_set_in_path_serialises_as_sorted_list(self):
        bgp = BgpAttrs(as_path=[65010, frozenset({64500, 64501, 64502})])
        d = bgp.to_dict()
        # frozenset gets a stable JSON shape: sorted list
        assert d["as_path"][0] == 65010
        assert sorted(d["as_path"][1]) == [64500, 64501, 64502]
