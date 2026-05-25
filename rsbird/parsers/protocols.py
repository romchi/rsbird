"""
Parser for ``show protocols`` and ``show protocols all [<name>]``.

Three BIRD generations are handled by the same dispatcher because the surface
shape is consistent — what changes is *where* fields live:

* **BIRD 1.6** has no per-AF channels; all counters and filters sit directly
  under the protocol's ``1006-`` block.
* **BIRD 2.x** introduces ``Channel ipv4`` / ``Channel ipv6`` sub-blocks that
  hold filters and counters; the protocol block carries only BGP-state info.
* **BIRD 3.x** is BIRD 2.x plus ``Created:``, ``Import state:`` / ``Export state:``
  inside channels, and two extra columns in the route-change stats matrix
  (``RX limit`` and ``limit``).

Indentation is the navigation key. After stripping the ``1006-`` tag from
the first line of a detail block and the single-space continuation marker
from every subsequent line, the body indent is one of:

* 2 spaces — top-level section header (``Channel ipv4``, ``BGP state:``,
  ``Created:``, or — in BIRD 1.6 — ``Preference:`` / ``Routes:`` etc.)
* 4 spaces — sub-field of the current section
* 6 spaces — a row inside the ``Route change stats:`` matrix
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rsbird.models import (
    BgpState,
    Channel,
    Protocol,
    RouteCounts,
    UpdateStats,
)
from rsbird.parsers._common import parse_datetime

# Column header in the "Route change stats:" matrix -> UpdateStats attribute.
_STATS_COL_MAP = {
    "received": "received",
    "rejected": "rejected",
    "filtered": "filtered",
    "ignored":  "ignored",
    "accepted": "accepted",
    "RX limit": "rx_limit",     # BIRD 3.x
    "limit":    "limit",        # BIRD 3.x
}

# "Import updates" -> "import_updates" -> Protocol/Channel attribute prefix
_STATS_ROW_MAP = {
    "Import updates":   "import_updates",
    "Import withdraws": "import_withdraws",
    "Export updates":   "export_updates",
    "Export withdraws": "export_withdraws",
}

_BGP_STATE_VALUES = {s.value for s in BgpState}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _value_after_colon(line: str) -> str:
    return line.split(":", 1)[1].strip() if ":" in line else ""


def _int_or_none(text: str) -> int | None:
    text = text.strip()
    if not text or text == "---":
        return None
    try:
        return int(text)
    except ValueError:
        return None


_ROUTES_PART = re.compile(r"\s*(\d+)\s+(\w+)")


def _parse_routes_line(value: str) -> RouteCounts:
    """``15 imported, 1 filtered, 4 exported, 12 preferred`` -> RouteCounts."""
    rc = RouteCounts()
    for part in value.split(","):
        m = _ROUTES_PART.match(part)
        if not m:
            continue
        count, kind = int(m.group(1)), m.group(2).lower()
        if hasattr(rc, kind):
            setattr(rc, kind, count)
    return rc


# Split the stats-matrix header into columns. Columns are separated by 2+
# spaces; this preserves multi-word column names ("RX limit").
_DOUBLE_SPACE = re.compile(r" {2,}")


def _parse_stats_header(value: str) -> list[str]:
    return [c.strip() for c in _DOUBLE_SPACE.split(value.strip()) if c.strip()]


def _populate_stats(target: UpdateStats, columns: list[str], values: list[str]) -> None:
    for col, raw in zip(columns, values, strict=False):
        attr = _STATS_COL_MAP.get(col)
        if attr is not None:
            setattr(target, attr, _int_or_none(raw))


# ---------------------------------------------------------------------------
# Parse state
# ---------------------------------------------------------------------------

@dataclass
class _State:
    """Mutable parser scratch-space carried across lines of a detail block."""
    protocol: Protocol | None = None
    channel: Channel | None = None
    stats_section: str | None = None       # "import_updates" / "import_withdraws" / ...
    stats_target: UpdateStats | None = None
    stats_columns: list[str] = field(default_factory=list)
    in_bgp_state: bool = False


def _parse_summary_row(body: str) -> Protocol | None:
    """``NAME PROTO TABLE STATE SINCE [INFO...]`` -> Protocol."""
    parts = body.split(None, 5)
    if len(parts) < 5:
        return None
    name, proto, table, state, since = parts[:5]
    info = parts[5].strip() if len(parts) > 5 else None
    return Protocol(
        name=name,
        proto=proto,
        table=None if table == "---" else table,
        state=state,
        up=(state.lower() == "up"),
        since=parse_datetime(since),
        info=info or None,
    )


def _handle_detail_line(body: str, st: _State, version: object | None) -> None:  # noqa: ARG001
    """Dispatch one stripped detail-block line into the current protocol."""
    if st.protocol is None:
        return
    stripped = body.lstrip()
    indent = len(body) - len(stripped)

    # ---------- stats-matrix rows (any indent while a header is open) -
    # BIRD 1.6 has the rows at indent 4 (the "Route change stats:" header is
    # itself at indent 2), BIRD 2.x/3.x put both header and rows two levels
    # deeper inside the channel. Distinguishing by indent alone is fragile,
    # so we key off the row name and the open stats context instead.
    if st.stats_target is not None and st.stats_columns:
        for row_name, attr in _STATS_ROW_MAP.items():
            if stripped.startswith(row_name + ":"):
                values_text = stripped[len(row_name) + 1:]
                stats = UpdateStats()
                _populate_stats(stats, st.stats_columns, values_text.split())
                setattr(st.stats_target, attr, stats)
                return

    # ---------- 2-space top-level ------------------------------------
    if indent == 2:
        st.channel = None
        st.in_bgp_state = False
        st.stats_target = None
        st.stats_columns = []

        if stripped.startswith("Channel "):
            # `Channel ipv4` / `Channel ipv6` / `Channel ipv4 mpls` ...
            name = stripped[len("Channel "):].strip().split()[0]
            st.channel = Channel(name=name)
            st.protocol.channels.append(st.channel)
            return

        if stripped.startswith("BGP state:"):
            st.in_bgp_state = True
            value = _value_after_colon(stripped)
            if value in _BGP_STATE_VALUES:
                st.protocol.bgp_state = BgpState(value)
            return

        # BIRD 1.6 (no channels) — counters live at top-level on the protocol.
        if stripped.startswith("Preference:"):
            st.protocol.preference = _int_or_none(_value_after_colon(stripped))
        elif stripped.startswith("Input filter:"):
            st.protocol.input_filter = _value_after_colon(stripped)
        elif stripped.startswith("Output filter:"):
            st.protocol.output_filter = _value_after_colon(stripped)
        elif stripped.startswith("Routes:"):
            st.protocol.routes = _parse_routes_line(_value_after_colon(stripped))
        elif stripped.startswith("Route change stats:"):
            st.stats_columns = _parse_stats_header(_value_after_colon(stripped))
            # In 1.6 stats target the protocol; in 2.x/3.x they target the channel
            # but those have indent 4 (handled below).
            st.stats_target = st.protocol
        elif stripped.startswith("Created:"):
            st.protocol.created = parse_datetime(_value_after_colon(stripped))
        elif stripped.startswith("Description:"):
            st.protocol.description = _value_after_colon(stripped)
        return

    # ---------- 4-space sub-fields -----------------------------------
    if indent == 4:
        # `Route change stats:` lives at indent 4 inside a channel (BIRD 2/3).
        if stripped.startswith("Route change stats:"):
            st.stats_columns = _parse_stats_header(_value_after_colon(stripped))
            st.stats_target = st.channel if st.channel is not None else st.protocol
            return

        if st.channel is not None:
            _set_channel_field(st.channel, stripped)
            return

        if st.in_bgp_state:
            _set_bgp_state_field(st.protocol, stripped)


def _set_channel_field(ch: Channel, stripped: str) -> None:
    if stripped.startswith("State:"):
        ch.state = _value_after_colon(stripped)
    elif stripped.startswith("Import state:"):
        ch.import_state = _value_after_colon(stripped)
    elif stripped.startswith("Export state:"):
        ch.export_state = _value_after_colon(stripped)
    elif stripped.startswith("Table:"):
        ch.table = _value_after_colon(stripped)
    elif stripped.startswith("Preference:"):
        ch.preference = _int_or_none(_value_after_colon(stripped))
    elif stripped.startswith("Input filter:"):
        ch.input_filter = _value_after_colon(stripped)
    elif stripped.startswith("Output filter:"):
        ch.output_filter = _value_after_colon(stripped)
    elif stripped.startswith("Routes:"):
        ch.routes = _parse_routes_line(_value_after_colon(stripped))


def _set_bgp_state_field(p: Protocol, stripped: str) -> None:
    if stripped.startswith("Neighbor address:"):
        p.neighbor_address = _value_after_colon(stripped)
    elif stripped.startswith("Neighbor AS:"):
        p.neighbor_as = _int_or_none(_value_after_colon(stripped))
    elif stripped.startswith("Neighbor ID:"):
        p.neighbor_id = _value_after_colon(stripped)
    elif stripped.startswith("Source address:"):
        p.source_address = _value_after_colon(stripped)
    elif stripped.startswith("Hold timer:"):
        p.hold_timer = _value_after_colon(stripped)
    elif stripped.startswith("Keepalive timer:"):
        p.keepalive_timer = _value_after_colon(stripped)
    elif stripped.startswith("Route limit:"):
        p.route_limit = _value_after_colon(stripped)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_protocols(raw: str, *, version: object | None = None) -> list[Protocol]:
    """Parse ``show protocols`` or ``show protocols all [<name>]`` into Protocols."""
    out: list[Protocol] = []
    st = _State()

    def finalise():
        if st.protocol is not None:
            out.append(st.protocol)
            st.protocol = None
            st.channel = None
            st.stats_target = None
            st.stats_columns = []
            st.in_bgp_state = False

    for raw_line in raw.splitlines():
        # New protocol summary row.
        if raw_line.startswith("1002-"):
            finalise()
            st.protocol = _parse_summary_row(raw_line[5:])
            continue

        # First line of a detail block.
        if raw_line.startswith("1006-"):
            body = raw_line[5:]
            if body.strip():
                _handle_detail_line(body, st, version)
            continue

        if not raw_line.startswith(" "):
            # Greeting (0001), header (2002-...), terminator (0000) — skip.
            continue

        body = raw_line[1:]
        if not body.strip():
            # Blank line — closes any open stats block but keeps the protocol open.
            st.stats_target = None
            st.stats_columns = []
            continue

        if body[0] != " ":
            # Summary-mode continuation: another protocol row, no detail block.
            finalise()
            st.protocol = _parse_summary_row(body)
            continue

        # Indented body — detail content.
        _handle_detail_line(body, st, version)

    finalise()
    return out
