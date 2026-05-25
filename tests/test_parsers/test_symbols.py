"""Parser test: ``show symbols`` across every captured BIRD version."""
from __future__ import annotations

import pytest

from rsbird.parsers.symbols import parse_symbols
from tests.conftest import load_fixture

# What we know lives in each version's lab config — at minimum.
EXPECTED_PROTOCOLS = {
    "bird-1.6.8-ipv4": {"rs1_ipv4", "rs2_ipv4"},
    "bird-1.6.8-ipv6": {"rs1_ipv6", "rs2_ipv6"},
    "bird-2.0.8":      {"rs1_ipv4", "rs2_ipv4", "rs1_ipv6", "rs2_ipv6"},
    "bird-3.1.2":      {"rs1_ipv4", "rs2_ipv4", "rs1_ipv6", "rs2_ipv6"},
}
EXPECTED_TABLES = {
    "bird-1.6.8-ipv4": {"master"},
    "bird-1.6.8-ipv6": {"master"},
    "bird-2.0.8":      {"master4", "master6"},
    "bird-3.1.2":      {"master4", "master6"},
}


@pytest.mark.parametrize("version", list(EXPECTED_PROTOCOLS.keys()))
def test_parse_symbols_finds_protocols(version):
    raw = load_fixture(version, "show_symbols", "default.input")
    syms = parse_symbols(raw)
    names = {s.name for s in syms if s.kind == "protocol"}
    assert EXPECTED_PROTOCOLS[version].issubset(names)


@pytest.mark.parametrize("version", list(EXPECTED_TABLES.keys()))
def test_parse_symbols_finds_routing_tables(version):
    raw = load_fixture(version, "show_symbols", "default.input")
    tables = {s.name for s in parse_symbols(raw) if s.kind == "routing table"}
    assert EXPECTED_TABLES[version] == tables


def test_parse_symbols_handles_multi_word_kind():
    """Kinds like ``routing table`` / ``custom attribute`` mustn't be split."""
    raw = load_fixture("bird-3.1.2", "show_symbols", "default.input")
    kinds = {s.kind for s in parse_symbols(raw)}
    assert "routing table" in kinds
    # BIRD 3 also emits "custom attribute" for bgp_community etc.
    assert any(" " in k for k in kinds), kinds


def test_parse_symbols_handles_3_1_unknown_type():
    """BIRD 3.1 surfaces unclassified things as ``unknown type``."""
    raw = load_fixture("bird-3.1.2", "show_symbols", "default.input")
    kinds = {s.kind for s in parse_symbols(raw)}
    assert "unknown type" in kinds


def test_parse_symbols_returns_empty_for_empty_reply():
    assert parse_symbols("0001 BIRD 2.0.8 ready.\n0000 \n") == []
