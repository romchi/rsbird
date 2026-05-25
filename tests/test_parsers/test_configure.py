"""Parser test: ``configure`` / ``configure check`` / ``configure soft``."""
from __future__ import annotations

import pytest

from rsbird.parsers.configure import parse_configure


def test_configure_check_clean():
    raw = (
        "0001 BIRD 2.0.8 ready.\n"
        "0002-Reading configuration from /etc/bird.conf\n"
        "0020 Configuration OK\n"
    )
    result = parse_configure(raw)
    assert result.ok is True
    assert result.code == 20
    assert "Configuration OK" in result.message
    assert result.file == "/etc/bird.conf"


def test_reconfigure_success():
    raw = (
        "0001 BIRD 2.0.8 ready.\n"
        "0002-Reading configuration from /etc/bird.conf\n"
        "0003 Reconfigured\n"
    )
    result = parse_configure(raw)
    assert result.ok is True
    assert result.code == 3


def test_reconfigure_in_progress():
    raw = (
        "0001 BIRD 2.0.8 ready.\n"
        "0004 Reconfiguration in progress\n"
    )
    result = parse_configure(raw)
    assert result.ok is True
    assert result.code == 4


def test_nothing_to_do_undo():
    raw = "0019 Nothing to do\n"
    result = parse_configure(raw)
    assert result.ok is True
    assert result.code == 19


def test_syntax_error_lights_failure():
    raw = (
        "0001 BIRD 2.0.8 ready.\n"
        "0002-Reading configuration from /etc/bird.conf\n"
        "8002 /etc/bird.conf, line 3: syntax error\n"
    )
    result = parse_configure(raw)
    assert result.ok is False
    assert result.code == 8002
    assert "syntax error" in result.message
    assert result.file == "/etc/bird.conf"


def test_unrecognised_reply_falls_back_to_failure():
    """No tagged terminator -> return the raw text as the error message."""
    result = parse_configure("nothing structured\n")
    assert result.ok is False
    assert result.code == 0
    assert "nothing structured" in result.message


@pytest.mark.parametrize("code,success", [
    (3, True), (4, True), (18, True), (19, True), (20, True),
    (8001, False), (8002, False), (9001, False), (9999, False),
])
def test_terminator_classification(code, success):
    raw = f"{code:04d} test\n"
    assert parse_configure(raw).ok is success
