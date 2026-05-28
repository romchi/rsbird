"""
Data model for parsed BIRD output.

Every shape uses ``dataclass(slots=True)`` for cheap construction at full-table
scale (~130k routes) and provides ``to_dict()`` for JSON-friendly serialisation.
The same dataclasses cover BIRD 1.6, 2.x and 3.x — version-specific fields are
``Optional`` and only filled when present in the source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar

__all__ = [
    "BgpAttrs",
    "BgpState",
    "Channel",
    "Community",
    "CommunityKind",
    "ConfigResult",
    "Protocol",
    "Route",
    "RouteCounts",
    "Status",
    "Symbol",
    "UpdateStats",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _serialise(value: Any) -> Any:
    """Recursive JSON-friendly conversion for dataclass fields."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, (list, tuple)):
        return [_serialise(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_serialise(v) for v in value), key=str)
    if isinstance(value, dict):
        return {k: _serialise(v) for k, v in value.items()}
    if is_dataclass(value):
        return {f.name: _serialise(getattr(value, f.name)) for f in fields(value)}
    return value


class _DictMixin:
    """Adds ``to_dict()`` to a dataclass."""

    def to_dict(self) -> dict[str, Any]:
        return {f.name: _serialise(getattr(self, f.name)) for f in fields(self)}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# BGP communities
# ---------------------------------------------------------------------------

class CommunityKind(str, Enum):
    """Three BGP community flavours BIRD exposes."""

    STANDARD = "standard"   # (asn, value)
    EXTENDED = "extended"   # (kind, a, b) — kind is "rt" / "ro" / "soo" / ...
    LARGE = "large"         # (a, b, c)


@dataclass(slots=True, frozen=True)
class Community(_DictMixin):
    """A single BGP community.

    ``parts`` is heterogeneous to fit all three flavours:

    * standard: ``(int, int)``
    * large:    ``(int, int, int)``
    * extended: ``(str, int, int)`` — first element is the kind tag (``rt``/``ro``/...)
    """

    kind: CommunityKind
    parts: tuple

    # str | int part separator — used for both __str__ and parse()
    _SEP: ClassVar[str] = ":"

    def __str__(self) -> str:  # "0:64496" / "rt:0:64550" / "64500:1:2"
        return self._SEP.join(str(p) for p in self.parts)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "parts": list(self.parts), "str": str(self)}

    # ---- constructors --------------------------------------------------

    @classmethod
    def standard(cls, asn: int, value: int) -> Community:
        return cls(CommunityKind.STANDARD, (int(asn), int(value)))

    @classmethod
    def large(cls, a: int, b: int, c: int) -> Community:
        return cls(CommunityKind.LARGE, (int(a), int(b), int(c)))

    @classmethod
    def extended(cls, kind: str, a: int, b: int) -> Community:
        return cls(CommunityKind.EXTENDED, (str(kind), int(a), int(b)))

    @classmethod
    def parse(cls, value: Community | str | tuple | list) -> Community:
        """Parse from a Community / "a:b[:c]" / "a,b[,c]" string / sequence.

        Accepts both ``:`` and ``,`` separators so the same parser handles
        BIRD's comma-style output and the colon-style convention used by
        most other BGP tooling.
        """
        if isinstance(value, Community):
            return value
        if isinstance(value, str):
            parts: list = re.split(r"[:,]", value)
        else:
            parts = list(value)
        if not parts:
            raise ValueError("empty community")
        if len(parts) == 2:
            return cls.standard(int(parts[0]), int(parts[1]))
        if len(parts) == 3:
            head = parts[0]
            if isinstance(head, str) and not head.lstrip("-").isdigit():
                return cls.extended(head, int(parts[1]), int(parts[2]))
            return cls.large(int(parts[0]), int(parts[1]), int(parts[2]))
        raise ValueError(f"unrecognised community: {value!r}")


# ---------------------------------------------------------------------------
# BGP attributes (route detail)
# ---------------------------------------------------------------------------

# An AS-path element is an int (regular hop) or a frozenset of ints (AS_SET).
ASPathElement = int | frozenset


@dataclass(slots=True)
class BgpAttrs(_DictMixin):
    """The ``BGP.*`` / ``bgp_*`` block from a ``show route ... all`` reply."""

    origin: str | None = None                       # "IGP" / "EGP" / "Incomplete"
    as_path: list[ASPathElement] = field(default_factory=list)
    next_hop: list[str] = field(default_factory=list)
    local_pref: int | None = None
    med: int | None = None
    communities: list[Community] = field(default_factory=list)
    large_communities: list[Community] = field(default_factory=list)
    ext_communities: list[Community] = field(default_factory=list)
    atomic_aggregate: bool = False
    aggregator: str | None = None
    originator_id: str | None = None
    cluster_list: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Route(_DictMixin):
    """One route entry as seen in BIRD's routing table."""

    prefix: str
    table: str | None = None
    protocol: str | None = None            # source protocol name, e.g. "rs1_ipv4"
    type: str | None = None                # "BGP unicast univ" / "static" / ...
    best: bool = False
    preference: int | None = None
    learned: datetime | None = None        # the route's "time" / "Since"
    via: str | None = None                 # next-hop / peer IP shown on the via line
    interface: str | None = None
    origin_as: int | None = None           # BIRD 2+/3 flat "asn" shortcut, when present
    bgp: BgpAttrs | None = None            # populated only with detail=True


# ---------------------------------------------------------------------------
# Protocols (peers)
# ---------------------------------------------------------------------------

class BgpState(str, Enum):
    """Possible BGP session states seen in BIRD output."""

    IDLE = "Idle"
    CONNECT = "Connect"
    ACTIVE = "Active"
    OPEN_SENT = "OpenSent"
    OPEN_CONFIRM = "OpenConfirm"
    ESTABLISHED = "Established"
    CLOSE = "Close"
    PASSIVE = "Passive"


@dataclass(slots=True)
class RouteCounts(_DictMixin):
    """The ``Routes: X imported, Y filtered, Z exported, W preferred`` line."""

    imported: int = 0
    filtered: int = 0
    exported: int = 0
    preferred: int = 0


@dataclass(slots=True)
class UpdateStats(_DictMixin):
    """One row from the route-change statistics matrix.

    BIRD 3.x adds two extra columns (``RX limit`` and ``limit``) — they show
    up here only when present in the source; older BIRDs leave them ``None``.
    A ``"---"`` cell in the source becomes ``None``.
    """

    received: int | None = None
    rejected: int | None = None
    filtered: int | None = None
    ignored: int | None = None
    accepted: int | None = None
    rx_limit: int | None = None     # BIRD 3.x
    limit: int | None = None        # BIRD 3.x


@dataclass(slots=True)
class Channel(_DictMixin):
    """A BIRD 2+ per-AF channel inside a protocol (``Channel ipv4``/``Channel ipv6``)."""

    name: str                                       # "ipv4" / "ipv6"
    state: str | None = None
    import_state: str | None = None                 # BIRD 3.x
    export_state: str | None = None                 # BIRD 3.x
    table: str | None = None
    preference: int | None = None
    input_filter: str | None = None
    output_filter: str | None = None
    routes: RouteCounts = field(default_factory=RouteCounts)
    import_updates: UpdateStats = field(default_factory=UpdateStats)
    import_withdraws: UpdateStats = field(default_factory=UpdateStats)
    export_updates: UpdateStats = field(default_factory=UpdateStats)
    export_withdraws: UpdateStats = field(default_factory=UpdateStats)


@dataclass(slots=True)
class Protocol(_DictMixin):
    """A row from ``show protocols``; ``show protocols all`` populates the rest."""

    name: str
    proto: str                                      # "BGP" / "Device" / "Direct" / ...
    table: str | None = None
    state: str = ""                                 # raw BIRD state word ("up", "start", "down")
    up: bool = False
    since: datetime | None = None
    info: str | None = None                         # rest-of-line summary info

    # -- detail (`show protocols all`) -----------------------------------
    description: str | None = None
    neighbor_id: str | None = None                  # router id of the remote peer
    neighbor_address: str | None = None
    neighbor_as: int | None = None
    source_address: str | None = None
    preference: int | None = None
    hold_timer: str | None = None
    keepalive_timer: str | None = None
    route_limit: str | None = None
    bgp_state: BgpState | None = None
    last_error: str | None = None
    created: datetime | None = None                 # BIRD 3.x "Created:" line

    # BIRD 1.6: counters live at the protocol level (no channels).
    input_filter: str | None = None
    output_filter: str | None = None
    routes: RouteCounts | None = None
    import_updates: UpdateStats | None = None
    import_withdraws: UpdateStats | None = None
    export_updates: UpdateStats | None = None
    export_withdraws: UpdateStats | None = None

    # BIRD 2+: one or more channels carry per-AF counters / filters.
    channels: list[Channel] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Status / symbols
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Status(_DictMixin):
    """The result of ``show status``."""

    version: str                                    # e.g. "2.0.8" / "3.1.2"
    router_id: str
    hostname: str | None = None                     # BIRD 2.0+
    server_time: datetime | None = None
    last_reboot: datetime | None = None
    last_reconfiguration: datetime | None = None


@dataclass(slots=True)
class Symbol(_DictMixin):
    """One row from ``show symbols``."""

    name: str
    kind: str                                       # "routing table" / "filter" / "protocol" / ...


@dataclass(slots=True)
class ConfigResult(_DictMixin):
    """Outcome of a ``configure`` / ``configure check`` / ``configure soft``.

    ``ok`` mirrors BIRD's terminator class — success codes ``0003``, ``0004``,
    ``0018``, ``0019``, ``0020`` map to ``True``; any ``8xxx`` / ``9xxx``
    error code maps to ``False`` and lights up ``message``. ``code`` carries
    the raw terminator so callers can disambiguate (e.g. ``19`` = "nothing to
    do"). ``file`` is the path BIRD reported reading, if it told us.
    """

    ok: bool
    code: int
    message: str
    file: str | None = None

