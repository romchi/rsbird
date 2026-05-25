"""End-to-end tests for the config-ops client surface."""
from __future__ import annotations

from rsbird.client import RsBird


async def test_config_check_clean(mock_bird):
    mock_bird.on(
        "configure check",
        "0002-Reading configuration from /etc/bird.conf\n0020 Configuration OK\n",
    )
    async with RsBird(mock_bird.path) as bird:
        result = await bird.config_check()
    assert result.ok is True
    assert result.code == 20
    assert result.file == "/etc/bird.conf"


async def test_config_check_syntax_error(mock_bird):
    mock_bird.on(
        "configure check",
        "0002-Reading configuration from /etc/bird.conf\n"
        "8002 /etc/bird.conf, line 3: syntax error\n",
    )
    async with RsBird(mock_bird.path) as bird:
        result = await bird.config_check()
    assert result.ok is False
    assert result.code == 8002
    assert "syntax error" in result.message


async def test_configure_reload(mock_bird):
    mock_bird.on(
        "configure",
        "0002-Reading configuration from /etc/bird.conf\n0003 Reconfigured\n",
    )
    async with RsBird(mock_bird.path) as bird:
        result = await bird.configure()
    assert result.ok is True
    assert result.code == 3


async def test_configure_soft(mock_bird):
    mock_bird.on(
        "configure soft",
        "0002-Reading configuration from /etc/bird.conf\n0003 Reconfigured\n",
    )
    async with RsBird(mock_bird.path) as bird:
        result = await bird.configure(soft=True)
    assert result.ok is True
    assert result.code == 3


async def test_configure_error_surfaces(mock_bird):
    mock_bird.on(
        "configure",
        "0002-Reading configuration from /etc/bird.conf\n"
        "9001 reload busy\n",
    )
    async with RsBird(mock_bird.path) as bird:
        result = await bird.configure()
    assert result.ok is False
    assert result.code == 9001
