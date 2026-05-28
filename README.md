# rsbird

Async Python client and parsers for the BIRD route-server control socket.

`rsbird` opens a unix socket to BIRD, speaks BIRD's numbered-reply text protocol, and turns the output of every common `show ...` command into typed `dataclass` objects ready for an API to serve.

- **async-native** I/O (no thread pool around blocking sockets)
- **typed** output via stdlib `dataclasses` (no extra deps)
- **explicit** BIRD version handling — 1.6.x, 2.0.x and 3.x are first-class
- **clean** layering: protocol I/O, pure-function parsers, high-level client

## Supported BIRD versions

| BIRD | Sockets | Notes |
|---|---|---|
| 1.6.x | two daemons (`bird.ctl` + `bird6.ctl`) | single-line route summary; `BGP.community` attribute keys |
| 2.0.x | one daemon | two-line route summary; `Channel ipv4`/`ipv6` blocks; `BGP.*` keys |
| 3.x   | one daemon | adds `Created:` / `Import state:` / `Export state:`; `RX limit`/`limit` stat columns; **lowercase** attribute keys (`bgp_community`, `bgp_path`, ...) |

The parsers handle all three; the data model is unified.

## Installation

```bash
pip install rsbird            # once published
# or
pip install -e /path/to/rsbird   # local editable install
```

Stdlib only — no runtime dependencies.

## CLI

The package ships a standalone command-line tool — `bin/rsbird` — that talks to BIRD via this library. It is **not** part of the importable package (the library is what you `import`;
the CLI is what you run, like `birdc` for BIRD itself). `pip install rsbird` drops the script onto your PATH; from a source checkout you can run it directly with `./bin/rsbird ...`.

```bash
$ rsbird --socket /run/bird/bird.ctl status
BIRD 2.0.8
  Router ID:            10.0.0.1
  Hostname:             rs1
  Server time:          2026-05-21 12:00:00
  Last reboot:          2026-05-20 18:00:00
  Last reconfiguration: 2026-05-20 18:00:00

$ rsbird -s /run/bird/bird.ctl protocols --brief
NAME      PROTO  STATE  INFO
--------  -----  -----  -----------
rs1_ipv4  BGP    up     Established
rs2_ipv4  BGP    up     Established

$ rsbird -s /run/bird/bird.ctl routes -t example
   PREFIX        ORIGIN_AS  PEER_AS  PEER_IP         PEER_NAME  LEARNED              PREF  COMMUNITY
-  ------------  ---------  -------  --------------  ---------  -------------------  ----  ---------------------------
*  192.0.2.0/24  64500      64497    198.51.100.230  rs1_ipv4   2026-05-21 12:52:11  100   0:64496 65535:65281 64500:1:2
   192.0.2.0/24  64500      64498    198.51.100.231  rs2_ipv4   2026-05-21 12:52:10  100   0:64496

# the rich table fetches detail; --brief is the fast path (drops PEER_AS / COMMUNITY), -d switches to the verbose per-route block
$ rsbird -s /run/bird/bird.ctl routes -t example -p 192.0.2.1 -d
* 192.0.2.0/24  via 198.51.100.230  on eth0  [rs1_ipv4 2026-05-21 12:52:11]  (100)  AS64500
    Type:         BGP unicast univ
    Origin:       IGP
    Peer AS:      64497
    Origin AS:    64500
    AS path:      64497 64510 64511 64500
    Community:    0:64496 65535:65281

$ rsbird -s /run/bird/bird.ctl community 0:64496 -t master4
$ rsbird -s /run/bird/bird.ctl ext-community rt:65010:1 -t master4
$ rsbird -s /run/bird/bird.ctl large-community 65010:1:2 -t master4

# --where / -w passes a raw BIRD filter expression verbatim to `show route ... where <EXPR>`. Always quote it — it contains spaces and []().
$ rsbird -s /run/bird/bird.ctl routes -t example -w 'bgp_path ~ [= * 64496 * =]'   # AS64496 anywhere in the path
$ rsbird -s /run/bird/bird.ctl routes -t example -w 'bgp_path.last = 64500'         # origin AS (BIRD 2.x+)
$ rsbird -s /run/bird/bird.ctl routes -t example -w 'bgp_path.len > 3'              # path longer than 3 hops
$ rsbird -s /run/bird/bird.ctl routes -t example -w 'bgp_community ~ [(0, 64496)]'  # tagged 0:64496
$ rsbird -s /run/bird/bird.ctl routes -t example -w 'net ~ [192.0.2.0/24+]'         # that prefix and all more-specifics
$ rsbird -s /run/bird/bird.ctl routes -t example -w 'source = RTS_BGP' -b           # BGP-learned, best only (filters compose)

$ rsbird -s /run/bird/bird.ctl --json status | jq .router_id
"10.0.0.1"
```

The socket path can also come from `$RSBIRD_SOCKET`, so a typical session looks like `export RSBIRD_SOCKET=/run/bird/bird.ctl` and then plain `rsbird status`, `rsbird tables`, `rsbird routes`, etc.
The `raw` subcommand is the **escape hatch** — it opens the control socket, sends an arbitrary command, and prints BIRD's reply byte-for-byte. Use it for commands the typed API doesn't model (e.g. `show route count`, `show memory`) or when debugging parsers:

```bash
$ rsbird -s /run/bird/bird.ctl raw "show route count"
0001 BIRD 2.0.8 ready.
1007- 42 of 42 routes for 24 networks
0000
```

The `--where` / `-w` flag on `routes` is the **filter escape hatch**: its argument is handed to BIRD verbatim as the `where <EXPR>` clause of `show route`, so anything BIRD's filter language accepts works — including conditions the typed API doesn't model.
It composes with the other `routes` filters (`--table`, `--protocol`, `--prefix`, `--best`, `--filtered`), each adding another clause. Quote the whole expression: it contains spaces and `[]()` that the shell would otherwise mangle.

Handy expressions (BIRD attribute names are lowercase in the filter language across all versions, even though `show route all` prints `BGP.community`):

| Goal | `--where` expression |
| --- | --- |
| Origin AS (last hop) | `bgp_path.last = 64496` *(BIRD 2.x+)* |
| ASN anywhere in path | `bgp_path ~ [= * 64496 * =]` |
| Path longer than N | `bgp_path.len > 3` |
| Has standard community | `bgp_community ~ [(0, 64496)]` |
| Has large community | `bgp_large_community ~ [(65010, 1, 2)]` |
| Prefix + more-specifics | `net ~ [192.0.2.0/24+]` |
| Length range | `net ~ [0.0.0.0/0{20,24}]` |
| BGP-learned routes | `source = RTS_BGP` |
| Next hop | `bgp_next_hop = 198.51.100.230` |

The community shortcuts (`community` / `ext-community` / `large-community`) are just preset `--where` expressions — `community 0:64496` is exactly `routes -w 'bgp_community ~ [(0, 64496)]'`.

All subcommands accept `--json` for machine-readable output. Run `rsbird <command> --help` for per-subcommand flags.

## Quick start

```python
import asyncio
from rsbird import RsBird

async def main():
    async with RsBird("/run/bird/bird.ctl") as bird:
        status = await bird.status()
        print(status.version, status.router_id)

        for peer in await bird.protocols():
            if peer.proto == "BGP":
                print(peer.name, peer.bgp_state, peer.neighbor_address)

        routes = await bird.routes(table="master4", best=True)
        print(f"{len(routes)} best routes in master4")

asyncio.run(main())
```

For **BIRD 1.6** split-daemon deployments use `DualStackBird`:

```python
from rsbird import DualStackBird

async with DualStackBird(
    socket_v4="/run/bird/bird.ctl",
    socket_v6="/run/bird/bird6.ctl",
) as bird:
    v4 = await bird.routes(ip_version=4, table="master")
    v6 = await bird.routes(ip_version=6, table="master")
```

## Client API

```python
class RsBird:
    def __init__(socket_path: str, *, timeout: float = 30.0)
    async def __aenter__() -> RsBird       # opens socket, reads greeting
    async def __aexit__(*exc)              # closes
    bird_version: BirdVersion              # parsed from BIRD's greeting

    # queries
    async def status() -> Status
    async def symbols(kind: str | None = None) -> list[Symbol]
    async def tables() -> list[str]
    async def protocols(name: str | None = None, *, detail: bool = True) -> list[Protocol]

    async def routes(*, table=None, prefix=None, protocol=None, where=None,
                     detail=False, best=False, filtered=False) -> list[Route]
    async def routes_exported(protocol: str, *, detail=False) -> list[Route]
    async def routes_filtered(protocol: str, *, detail=False) -> list[Route]
    async def routes_by_community(community, *, table=None, detail=False, best=False)
    async def routes_by_ext_community(ext_community, *, table=None, detail=False, best=False)
    async def routes_by_large_community(large_community, *, table=None, detail=False, best=False)

    # config ops
    async def config_check() -> ConfigResult
    async def configure(*, soft: bool = False) -> ConfigResult
```

`DualStackBird` mirrors this surface, with an extra required
`ip_version=4|6` keyword on every query method.

## Data model

All output structures live in `rsbird.models` as `@dataclass(slots=True)`:

- `Status(version, router_id, hostname, server_time, last_reboot, last_reconfiguration)`
- `Symbol(name, kind)` — `kind` is BIRD's free-form classifier (`routing table`, `protocol`, `filter`, ...)
- `Protocol(name, proto, state, up, since, info, ... + channels[])` — BIRD 1.6 counters/filters sit on the protocol itself; BIRD 2.x/3.x lives in `channels`
- `Channel(name, state, import_state, export_state, table, preference, input_filter, output_filter, routes, import_updates, ...)`
- `Route(prefix, protocol, type, best, preference, learned, via, interface, origin_as, bgp)`
- `BgpAttrs(origin, as_path, next_hop, local_pref, med, communities, large_communities, ext_communities, ...)`
- `Community(kind, parts)` — `standard` / `large` / `extended`, with `.parse()` from a `"a:b"` / `"a:b:c"` / `"rt:a:b"` string or tuple

Every dataclass has a `to_dict()` that returns JSON-friendly primitives (datetimes serialised as ISO strings, `AS_SET` represented as sorted lists, nested dataclasses recursively dictified).

```python
route = Route(prefix="10.5.5.0/24", best=True, origin_as=65010)
route.to_dict()
# {'prefix': '10.5.5.0/24', 'best': True, 'origin_as': 65010, 'bgp': None, ...}
```

## Error handling

The library raises only `rsbird.exceptions`:

- `RsBirdError` — base.
- `BirdError(code, message)` — BIRD returned a 4-digit error code (`8xxx`/`9xxx`).
- `BirdTimeout` — socket I/O exceeded the configured timeout.
- `ParseError(message, raw=...)` — the parser couldn't make sense of a reply.

## BIRD socket protocol — what the parsers see

BIRD replies are line-oriented, each line tagged with a 4-digit numeric code:

```
NNNN-...    continuation line within a multi-line block
 ...        space-prefixed body continuation
NNNN ...    terminal line of a block / reply (when NNNN is in the success/error set)
```

A reply ends when a terminal-coded line arrives. `rsbird.protocol.TERMINAL_CODES` holds the success codes (`0`, `3`, `4`, `13`, `18`, `19`, `20`); any `8xxx`/`9xxx` code is also recognised as a terminator.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

The suite uses a real unix-socket `MockBird` server (in `tests/conftest.py`) to exercise the protocol layer and the high-level client; parser tests are **fixture-driven** — each `tests/test_parsers/test_*.py` parametrises over the captured `.input` files in `fixtures/<bird-version>/`.

To record fresh fixtures from a live BIRD run:

```bash
python3 tools/capture_fixtures.py --socket /run/bird/bird.ctl --out fixtures/bird-2.0.8
```

The tool writes one file per command into a self-documenting layout (e.g. `show_route_for/10.5.5.0_24.all.input`).

## Project layout

```
rsbird/                  importable library — the API
  __init__.py            public exports
  client.py              RsBird (async client)
  dual.py                DualStackBird helper for BIRD 1.6
  protocol.py            async unix-socket I/O, greeting/terminator framing
  models.py              dataclasses for every output shape
  exceptions.py          RsBirdError / BirdError / BirdTimeout / ParseError
  parsers/               pure sync functions, one module per command
    status.py / symbols.py / protocols.py / routes.py / configure.py
  py.typed               marker for type checkers

bin/                     standalone command-line tools
  rsbird                 birdc-style CLI; pip installs it onto PATH

tools/                   developer-only utilities
  capture_fixtures.py    capture raw BIRD socket output

tests/                   pytest suite + MockBird helper
fixtures/                captured raw replies, organised by BIRD version
lab/                     docker-friendly BIRD configs for synthesising fixtures
```

## License

MIT.
