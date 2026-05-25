"""Tests for the async control-socket layer."""
from __future__ import annotations

import pytest

from rsbird.exceptions import ParseError
from rsbird.protocol import TERMINAL_CODES, BirdConnection, BirdVersion, _is_terminal


class TestBirdVersion:
    def test_parse_three_components(self):
        v = BirdVersion.parse("2.0.8")
        assert (v.major, v.minor, v.patch) == (2, 0, 8)
        assert str(v) == "2.0.8"

    def test_parse_two_components(self):
        v = BirdVersion.parse("3.1")
        assert (v.major, v.minor, v.patch) == (3, 1, 0)

    def test_parse_rejects_garbage(self):
        with pytest.raises(ParseError):
            BirdVersion.parse("not-a-version")


class TestBirdConnection:
    async def test_open_parses_greeting(self, mock_bird):
        mock_bird.version = "2.0.8"
        async with await BirdConnection.open(mock_bird.path) as conn:
            assert conn.version.to_tuple() == (2, 0, 8)
            assert str(conn.version) == "2.0.8"

    async def test_query_reads_until_terminator(self, mock_bird):
        mock_bird.on("show fake", "1007-row one\n more data\n0000 \n")
        async with await BirdConnection.open(mock_bird.path) as conn:
            raw = await conn.query("show fake")
            assert "1007-row one" in raw
            assert "more data" in raw
            # The terminal "0000 " line is included.
            assert "0000" in raw.splitlines()[-1]

    async def test_query_stops_on_error_code(self, mock_bird):
        mock_bird.on("show bad", "9001 nope\n")
        async with await BirdConnection.open(mock_bird.path) as conn:
            raw = await conn.query("show bad")
            assert raw.startswith("9001")

    async def test_multiple_queries_share_connection(self, mock_bird):
        mock_bird.on("show one", "1000-one\n0000 \n")
        mock_bird.on("show two", "1000-two\n0000 \n")
        async with await BirdConnection.open(mock_bird.path) as conn:
            first = await conn.query("show one")
            second = await conn.query("show two")
            assert "one" in first
            assert "two" in second

    async def test_unknown_command_yields_9xxx(self, mock_bird):
        async with await BirdConnection.open(mock_bird.path) as conn:
            raw = await conn.query("show whatever")
            assert "9001" in raw

    async def test_close_marks_connection_closed(self, mock_bird):
        conn = await BirdConnection.open(mock_bird.path)
        await conn.close()
        assert conn.closed is True

    async def test_open_times_out_on_missing_socket(self):
        with pytest.raises(Exception):
            await BirdConnection.open("/nonexistent/socket.ctl", timeout=0.5)


def test_terminal_code_set_includes_success_and_implies_error_range():
    # success codes — explicit set
    for code in (0, 3, 4, 13, 18, 19, 20):
        assert code in TERMINAL_CODES
    # errors 8xxx / 9xxx — implicit via _is_terminal range check
    assert _is_terminal(8001)
    assert _is_terminal(9001)
    assert not _is_terminal(1007)  # data continuation
    assert not _is_terminal(1)     # greeting
