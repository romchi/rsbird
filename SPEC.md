# rsbird — design specification

A Python library for talking to a BIRD route server: connect to the control
socket, send commands, parse the raw socket replies into typed Python data
structures suitable for use by APIs (e.g. voltron-api).

This document is the agreed contract for the rewrite that replaces
`pybird@mh-v1.3.1`. It captures decisions, the data model, the API surface,
the BIRD-version support matrix, the testing strategy and the work split
between you and me.

## 1. Status of decisions

| # | Decision | Choice |
|---|---|---|
| 1 | API shape | **Clean redesign** (voltron-api plugin updated in lockstep) |
| 2 | Concurrency model | **Async-native** (asyncio sockets) |
| 3 | Scope | **Read + config management** (configure / check / get / put) |
| 4 | Output representation | **stdlib `dataclasses` with `slots=True`** + `to_dict()` |
| 5 | BIRD versions | **1.6.x → 2.x → 3.0** |
| 6 | Socket topology | **Auto** — single socket (BIRD 2/3) or split v4+v6 (BIRD 1.6); per-daemon client instance |

## 2. Supported BIRD versions and topology

| BIRD | CLI output shape (notable) | Sockets |
|---|---|---|
| 1.6.x | `PREFIX via IP on IF [src time] * (pref) [ASn]`; dates `DD-MM-YYYY HH:MM:SS`; attrs `BGP.community: (...)` (capital + dot) | **two** daemons: `bird.ctl` (v4) + `bird6.ctl` (v6) |
| 2.0.x | two-line routes: `PREFIX unicast [src time] * (pref)` + `via IP on IF`; dates `YYYY-MM-DD HH:MM:SS.nnn`; per-channel detail (`Channel ipv4` / `Channel ipv6`); attrs `BGP.community: (...)` | **one** daemon, AF chosen via `table` |
| 3.0.x | superset of 2.x; **attribute names switched to lowercase+underscore**: `bgp_community`, `bgp_path`, `bgp_origin`, `bgp_next_hop`, `bgp_local_pref`, `bgp_ext_community`, `bgp_large_community` instead of `BGP.*`. New stat columns (`RX limit`, `limit`) in route-change stats. `Created:` / `Import state:` / `Export state:` lines in protocol detail. | **one** daemon |

A single `RsBird` instance speaks to **one** BIRD daemon. For BIRD 1.6 a caller
holds two instances; for BIRD 2/3 it holds one. A small `DualStackBird` helper
will be provided for the 1.6 case so callers don't repeat the v4/v6 split.

Version detection is automatic on first contact (via `show status`). Parsers
dispatch on the detected major version; each parser has 1–3 variants.

## 3. Package layout

```text
rsbird/
  __init__.py          public exports
  client.py            RsBird (async client) + DualStackBird helper
  protocol.py          asyncio control-socket I/O, reply-code framing, timeouts
  parsers/             pure sync functions: raw text -> dataclasses
    __init__.py        dispatch by command + BIRD version
    status.py
    protocols.py       show protocols [all [<name>]]
    routes.py          show route <variants>
    symbols.py
    common.py          shared helpers (dates, communities, AS-paths)
  models.py            dataclasses (Route, Protocol, Status, Symbol, Community, BgpAttrs, Channel)
  config.py            configure / configure check / get_config / put_config
  exceptions.py        BirdError, ParseError, BirdTimeout, AuthError ...
  py.typed             marker for type checkers
tools/
  capture_fixtures.py  capture raw BIRD socket output on the route server
tests/
  conftest.py
  data/
    commands/          curated <slug>/NNN.input + NNN.expected fixtures
    config/            sample bird.conf snippets per version
  test_protocol.py     socket I/O (against MockBird)
  test_parsers/        one file per parser, fixture-parametrised
  test_models.py
  test_client.py       end-to-end through MockBird
docs/
  index.md
  schema.md            reference for every dataclass field
  versions.md          BIRD version differences captured per parser
  examples.md
pyproject.toml
README.md
CHANGELOG.md
```

## 4. Data model (draft)

The structures below are the **proposed** schema. Final field set is fixed
once the fixture corpus lands (real data may surface fields we miss now).
All dataclasses use `slots=True` for memory efficiency on full tables (~130k
routes). Every class exposes `to_dict()` returning JSON-friendly primitives.

```python
# rsbird/models.py — draft

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# --- enums ---------------------------------------------------------------

class BgpState(str, Enum):
    IDLE = "Idle"
    CONNECT = "Connect"
    ACTIVE = "Active"
    OPEN_SENT = "OpenSent"
    OPEN_CONFIRM = "OpenConfirm"
    ESTABLISHED = "Established"
    CLOSE = "Close"

# --- BGP attributes ------------------------------------------------------

@dataclass(slots=True, frozen=True)
class Community:
    """A BGP community. Three flavours; ``parts`` is heterogeneous for ext."""
    KIND_STANDARD = "standard"   # (asn, value)             e.g. (8954, 620)
    KIND_LARGE    = "large"      # (a, b, c)                e.g. (64500, 1, 2)
    KIND_EXTENDED = "extended"   # (kind_str, a, b)         e.g. ("rt", 0, 64550)

    kind: str
    parts: tuple

    def __str__(self) -> str:
        return ":".join(str(p) for p in self.parts)


# An AS-path element is an int (regular hop) or a frozenset of ints (AS_SET).
ASPathElement = int | frozenset

@dataclass(slots=True)
class BgpAttrs:
    origin: str | None = None                       # "IGP" | "EGP" | "Incomplete"
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

# --- routes --------------------------------------------------------------

@dataclass(slots=True)
class Route:
    prefix: str                                     # "143.0.143.0/24" / "2001:db8::/32"
    table: str | None = None
    protocol: str | None = None                     # source protocol name
    type: str | None = None                         # "BGP unicast univ" / "static" ...
    best: bool = False
    preference: int | None = None
    learned: datetime | None = None                 # the route's "time"
    via: str | None = None                          # primary next-hop ip
    interface: str | None = None
    origin_as: int | None = None                    # flat shortcut (BIRD 2+ flat field)
    bgp: BgpAttrs | None = None                     # populated when detail=True

# --- protocols (peers) ---------------------------------------------------

@dataclass(slots=True)
class RouteCounts:
    imported: int = 0
    filtered: int = 0
    exported: int = 0
    preferred: int = 0

@dataclass(slots=True)
class UpdateStats:
    """Route-change matrix: import/export × updates/withdraws ×
    received/rejected/filtered/ignored/accepted. None = '---' from BIRD."""
    received: int | None = None
    rejected: int | None = None
    filtered: int | None = None
    ignored:  int | None = None
    accepted: int | None = None

@dataclass(slots=True)
class Channel:
    """A BIRD 2+ per-AF channel of a protocol (ipv4 / ipv6 / ...)."""
    name: str                                       # "ipv4" / "ipv6"
    state: str | None = None
    input_filter: str | None = None
    output_filter: str | None = None
    routes: RouteCounts = field(default_factory=RouteCounts)
    import_updates: UpdateStats = field(default_factory=UpdateStats)
    import_withdraws: UpdateStats = field(default_factory=UpdateStats)
    export_updates: UpdateStats = field(default_factory=UpdateStats)
    export_withdraws: UpdateStats = field(default_factory=UpdateStats)

@dataclass(slots=True)
class Protocol:
    """A row from ``show protocols``; ``detail`` adds the deeper fields."""
    name: str
    proto: str                                      # "BGP" / "Device" / "Direct" ...
    table: str | None = None
    state: str = ""                                 # raw BIRD state word
    up: bool = False
    since: datetime | None = None
    info: str | None = None                         # rest-of-line summary info

    # detail (from `show protocols all`)
    description: str | None = None
    neighbor_id: str | None = None                  # router id
    neighbor_address: str | None = None
    neighbor_as: int | None = None
    source_address: str | None = None
    preference: int | None = None
    input_filter: str | None = None                 # BIRD 1.6 (no channels)
    output_filter: str | None = None
    hold_timer: str | None = None
    keepalive_timer: str | None = None
    route_limit: str | None = None
    routes: RouteCounts | None = None               # BIRD 1.6
    import_updates: UpdateStats | None = None
    import_withdraws: UpdateStats | None = None
    export_updates: UpdateStats | None = None
    export_withdraws: UpdateStats | None = None
    channels: list[Channel] = field(default_factory=list)  # BIRD 2+

# --- status & symbols ----------------------------------------------------

@dataclass(slots=True)
class Status:
    version: str
    router_id: str
    hostname: str | None = None
    server_time: datetime | None = None
    last_reboot: datetime | None = None
    last_reconfiguration: datetime | None = None

@dataclass(slots=True)
class Symbol:
    name: str
    kind: str                                       # "table" | "filter" | "function" | "protocol" | "template" | "roa"

# --- config ops ----------------------------------------------------------

@dataclass(slots=True)
class ConfigResult:
    ok: bool
    message: str
    file: str | None = None
```

### Notes on the schema

- Timestamps are `datetime` objects; `to_dict()` emits ISO-8601 strings.
- `as_path` keeps AS_SET as `frozenset[int]` — exactly conveys the set semantics.
- Communities are first-class objects with `__str__` (`"0:13335"` / `"rt:0:64550"`); helpers like `Community.standard(0, 13335)` for construction.
- Optional fields default to `None`/empty so BIRD 1.6 protocols (no channels)
  and BIRD 2+ protocols (channel list) share the same `Protocol` class.

## 5. API surface

```python
class RsBird:
    """Async client for ONE BIRD daemon (one control socket)."""
    def __init__(self, socket_path: str, *, timeout: float = 30.0): ...

    async def __aenter__(self) -> "RsBird": ...
    async def __aexit__(self, *exc) -> None: ...
    async def close(self) -> None: ...

    # ---- queries ----
    async def status(self) -> Status
    async def tables(self) -> list[str]
    async def symbols(self, kind: str | None = None) -> list[Symbol]
    async def route_count(self, table: str | None = None) -> int

    async def protocols(
        self, name: str | None = None, *, detail: bool = True
    ) -> list[Protocol]

    async def routes(
        self, *,
        table: str | None = None,
        prefix: str | None = None,
        protocol: str | None = None,
        where: str | None = None,         # raw BIRD filter expression — escape hatch
        detail: bool = False,
        best: bool = False,
        filtered: bool = False,
    ) -> list[Route]

    async def routes_exported(self, protocol: str, **kw) -> list[Route]
    async def routes_filtered(self, protocol: str, **kw) -> list[Route]

    # BGP-attribute lookups — typed helpers over `routes(where=...)`. Each
    # accepts the matching value as a Community object, an "a:b[:c]" string
    # or a tuple, and produces the BIRD `where bgp_*community ~ [(...)]`
    # query under the hood.
    async def routes_by_community(
        self, community: Community | str | tuple,
        *, table: str | None = None, **kw,
    ) -> list[Route]
    async def routes_by_ext_community(
        self, ext_community: Community | str | tuple,
        *, table: str | None = None, **kw,
    ) -> list[Route]
    async def routes_by_large_community(
        self, large_community: Community | str | tuple,
        *, table: str | None = None, **kw,
    ) -> list[Route]

    # streaming iterator for full-table dumps (~130k routes)
    def iter_routes(self, **kw) -> AsyncIterator[Route]

    # ---- config ops ----
    async def configure(self, *, check: bool = False, soft: bool = False) -> ConfigResult
    async def get_config(self) -> str
    async def put_config(self, text: str) -> None


class DualStackBird:
    """Convenience wrapper for BIRD 1.6 (two daemons). Dispatches to the
    right :class:`RsBird` by an explicit ``ip_version`` argument."""
    def __init__(self, *, socket_v4: str, socket_v6: str, timeout: float = 30.0): ...
    async def routes(self, *, ip_version: int, **kw) -> list[Route]
    async def protocols(self, *, ip_version: int, **kw) -> list[Protocol]
    # ... same surface as RsBird, with ip_version selecting the daemon
```

## 6. BIRD control-socket protocol — implementation notes

- Unix domain socket, line-oriented text.
- On connect BIRD greets: `0001 BIRD <ver> ready.`
- Each reply consists of lines tagged with a 4-digit code:
  - `NNNN-...` continuation line within a block
  - `NNNN ...` (space) terminal line of a block / reply
  - ` ...` (space-prefixed) data continuation
- Reply ends when a terminal line with a known terminator code appears:
  - success: `0000`, `0003`, `0004`, `0013`, `0018`, `0019`, `0020`
  - error: `8xxx`, `9xxx`
- All I/O is `asyncio` (`asyncio.open_unix_connection`); per-call timeout.
- Connection model — open per query (simple, stateless, matches BIRD's model)
  with a fast path that keeps one connection alive within a single
  `async with RsBird(...)` block.

## 7. Command coverage

Commands rsbird sends (read-only set; config ops listed in §5):

```text
show status
show symbols
show symbols table
show protocols
show protocols all
show protocols all <NAME>
show route count
show route table <T> count
show route table <T>            [primary | filtered | all]
show route for <IP>             [all]
show route for <IP> table <T>   [all]
show route protocol <NAME>      [all | filtered]
show route export <NAME>        [all]
show route table <T> where bgp_community       ~ [<EXPR>]
show route table <T> where bgp_ext_community   ~ [<EXPR>]
show route table <T> where bgp_large_community ~ [<EXPR>]
show route table <T> where <ANY-BIRD-FILTER>             # generic escape hatch
```

Note: `bgp_large_community` is BIRD 2.0+. Issuing it against a 1.6 daemon
returns an "unknown attribute" error reply (rsbird surfaces this as
`BirdError`).

## 8. Testing strategy

- **Parser tests** — per command, per BIRD version. Fixture pairs
  `tests/data/commands/<slug>/NNN.input` (raw socket output) +
  `NNN.expected` (JSON dump of the parsed dataclass via `to_dict()`).
  pytest parametrises over the directory (same pattern as pybird).
- **Edge-case fixtures** — one file each for: empty result, `AS_SET`,
  large/extended communities, blackhole/static/device routes, multipath,
  long AS-paths with prepends, peer in every BGP state, IPv6, route with
  `from` (RR client).
- **Protocol-layer tests** — async `MockBird` server that replays fixtures
  over a real unix socket; exercises framing, timeouts, errors, the
  greeting handshake.
- **Integration (optional)** — keep pybird's docker harness in spirit: one
  container per BIRD version (1.6.x, 2.0.x, 3.0) with a minimal config that
  exercises every command; CI matrix runs the suite against each.

## 9. What I need from you

| # | Item | Notes | Status |
|---|---|---|---|
| 1 | **Raw `birdc` socket fixtures** | Run `tools/capture_fixtures.py` on each version's RS; send back the resulting `fixtures/` dir | **blocker** |
| 2 | Sanitised `bird.conf` samples | One per BIRD version if possible; used to write parser-friendly docker integration | nice-to-have |
| 3 | Sign-off on the §4 data model | After you read §4; can be tweaked once fixtures land | required before coding |
| 4 | Confirmation on `DualStackBird` shape | The §5 sketch — accept or push back | required |
| 5 | Whether BIRD 3.0 support is scoped now or staged | If no 3.0 fixtures available yet, 3.0 parsers ship as a follow-up | required |

For #1: the capture script is at [`tools/capture_fixtures.py`](tools/capture_fixtures.py).
It supports both topologies and the full 1.6–3.0 range automatically. On each
route server run, e.g.:

```bash
# BIRD 1.6 (two daemons)
python3 tools/capture_fixtures.py \
    --socket-v4 /var/run/bird/bird.ctl \
    --socket-v6 /var/run/bird/bird6.ctl

# BIRD 2 / 3 (single daemon)
python3 tools/capture_fixtures.py --socket /run/bird/bird.ctl
```

Hand over the resulting `fixtures/` directory (`tar -czf fixtures.tgz fixtures/`).
For each BIRD version please run once; label the tarball with the BIRD version.

## 10. Implementation plan (after fixtures land)

1. Lock the data model from §4 against the real fixtures.
2. Socket/protocol layer + `MockBird`.
3. Parser per command, version by version (start with the version on
   production); fixture-driven tests as we go.
4. `RsBird` async client wiring everything together.
5. Config ops.
6. `DualStackBird` helper.
7. voltron-api `methods/bird/plugin.py` updated to the new API (in lockstep
   so the API never breaks).
8. Docs + CHANGELOG + packaging.

## 11. Open / to be discussed later

- Connection pooling — keep one socket per call (simple) vs reuse within a
  client lifetime (faster). Default to reuse-within-`async with`.
- Streaming parser for full-table dumps (`iter_routes`) — useful for the
  pmacct export path; implement after the batch parser is stable.
- Should `Community` carry helper constructors / parsing from text?
  (`Community.parse("rt:0:64550")`) — likely yes.
- Should the library include a tiny `birdc`-like CLI for diagnostics? Out of
  scope for v1.
