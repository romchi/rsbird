"""
Parser for ``configure`` / ``configure check`` / ``configure soft`` replies.

BIRD speaks the same numbered-line protocol; the response ends with one of:

* ``0002-Reading configuration from /etc/bird.conf`` (informational; carries
  the path BIRD opened — captured for the caller).
* ``0003 Reconfigured`` — soft / hard reload succeeded.
* ``0004 Reconfiguration in progress`` — multi-step reload accepted.
* ``0018 Reconfiguration confirmed`` — undo confirm acknowledged.
* ``0019 Nothing to do`` — no pending undo (still a success).
* ``0020 Configuration OK`` — ``configure check`` finished cleanly.
* ``8xxx`` / ``9xxx`` — error (syntax / I/O); the message tells you what.
"""
from __future__ import annotations

import re

from rsbird.models import ConfigResult

_SUCCESS_CODES = frozenset({3, 4, 18, 19, 20})
_LINE = re.compile(r"^(\d{4})([- ])(.*)$")


def parse_configure(raw: str) -> ConfigResult:
    """Classify a configure-style reply into a :class:`ConfigResult`."""
    file_path: str | None = None
    terminator_code: int | None = None
    terminator_msg: str = ""

    for raw_line in raw.splitlines():
        m = _LINE.match(raw_line)
        if not m:
            continue
        code = int(m.group(1))
        is_terminal = m.group(2) == " "
        body = m.group(3).strip()

        # `0002-Reading configuration from /etc/bird.conf` — capture the path.
        if code == 2 and "Reading configuration from " in body:
            file_path = body.split("Reading configuration from ", 1)[1].split()[0]
            continue

        if is_terminal and code != 1:  # 0001 is the greeting, not a terminator
            terminator_code = code
            terminator_msg = body

    if terminator_code is None:
        # No clear terminator — treat as failure with the raw text for context.
        return ConfigResult(ok=False, code=0, message=raw.strip(), file=file_path)

    ok = terminator_code in _SUCCESS_CODES
    return ConfigResult(
        ok=ok, code=terminator_code, message=terminator_msg, file=file_path,
    )
