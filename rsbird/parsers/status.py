"""
Parser for ``show status`` — common across BIRD 1.6 / 2.x / 3.x.

Sample reply (BIRD 2.0.8)::

    0001 BIRD 2.0.8 ready.
    1000-BIRD 2.0.8
    1011-Router ID is 10.123.123.208
     Hostname is 9f530bca4aa7
     Current server time is 2026-05-21 11:36:21.948
     Last reboot on 2026-05-20 20:18:04.833
     Last reconfiguration on 2026-05-20 20:18:04.833
    0013 Daemon is up and running

BIRD 1.6 omits the ``Hostname is`` line; otherwise the shape is identical.
"""
from __future__ import annotations

from rsbird.exceptions import ParseError
from rsbird.models import Status
from rsbird.parsers._common import parse_datetime

_VERSION_TAG = "1000-BIRD "
_ROUTER_TAG = "1011-Router ID is "

_FIELDS = (
    ("Hostname is ",              "hostname",             lambda v: v),
    ("Current server time is ",   "server_time",          parse_datetime),
    ("Last reboot on ",           "last_reboot",          parse_datetime),
    ("Last reconfiguration on ",  "last_reconfiguration", parse_datetime),
)


def parse_status(raw: str, *, version: object | None = None) -> Status:  # noqa: ARG001 — kept for API parity
    """Parse a ``show status`` reply into a :class:`Status`."""
    lines = raw.splitlines()

    out: dict = {"version": "", "router_id": ""}

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(_VERSION_TAG):
            out["version"] = line[len(_VERSION_TAG):].strip()
        elif line.startswith(_ROUTER_TAG):
            out["router_id"] = line[len(_ROUTER_TAG):].strip()
            # Consume continuation lines (space-prefixed) until next tag/blank.
            j = i + 1
            while j < len(lines) and lines[j].startswith(" "):
                body = lines[j].lstrip()
                for prefix, key, convert in _FIELDS:
                    if body.startswith(prefix):
                        out[key] = convert(body[len(prefix):].strip())
                        break
                j += 1
            i = j
            continue
        i += 1

    if not out["version"] or not out["router_id"]:
        raise ParseError("show status: missing version or router_id", raw=raw)

    return Status(
        version=out["version"],
        router_id=out["router_id"],
        hostname=out.get("hostname"),
        server_time=out.get("server_time"),
        last_reboot=out.get("last_reboot"),
        last_reconfiguration=out.get("last_reconfiguration"),
    )
