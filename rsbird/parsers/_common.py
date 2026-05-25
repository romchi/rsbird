"""Helpers shared by individual parsers."""
from __future__ import annotations

import re
from datetime import datetime

# Date / time formats BIRD has emitted over the years, in the order we try them.
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",   # BIRD 2.x / 3.x: 2026-05-21 11:44:40.277
    "%Y-%m-%d %H:%M:%S",      # BIRD 1.6.x:    2026-05-21 12:52:07
    "%d-%m-%Y %H:%M:%S",      # very old BIRD: 21-05-2026 12:52:07
    "%Y-%m-%d",
    "%H:%M:%S.%f",
    "%H:%M:%S",
    "%H:%M",
)

_LINE_TAG = re.compile(r"^(\d{4})([- ])")


def parse_datetime(value: str) -> datetime | None:
    """Best-effort parse for the timestamp formats BIRD prints."""
    text = value.strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def iter_blocks(raw: str):
    """Iterate ``(code, is_terminal, body, raw_line)`` tuples for BIRD output.

    Continuation lines (space-prefixed or untagged) carry ``code = None`` and
    the previous block's code in ``parent_code`` via :func:`iter_blocks_with_parent`
    if you need it — most parsers only need the tag and the body text.
    """
    for raw_line in raw.splitlines():
        m = _LINE_TAG.match(raw_line)
        if m:
            code = int(m.group(1))
            terminal = m.group(2) == " "
            body = raw_line[5:]  # strip "NNNN-" or "NNNN "
            yield code, terminal, body, raw_line
        else:
            yield None, False, raw_line, raw_line


def strip_continuation(line: str) -> str:
    """Drop leading whitespace from a body-continuation line."""
    return line.lstrip()
