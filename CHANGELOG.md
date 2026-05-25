# Changelog

All notable changes to **rsbird** are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — initial release

First public version. Replaces `pybird@mh-v1.3.1` for the voltron-api BIRD
plugin and is otherwise feature-complete for read-side BGP queries on BIRD
1.6 / 2.0 / 3.x.

### Added

- **`RsBird`** — async client over the BIRD control socket. Opens a unix
  socket, consumes BIRD's greeting (parsed into `BirdVersion`), and serves
  any number of queries on the same connection until it is closed. Used as
  `async with RsBird(socket_path) as bird: ...`.
- **`DualStackBird`** — convenience wrapper that holds two `RsBird` daemons
  side-by-side; every method takes a required `ip_version=4|6` keyword for
  BIRD 1.6 split-daemon deployments.
- **Read-side query methods**:
  - `status()` — `show status`.
  - `symbols(kind=None)` / `tables()` — `show symbols [<kind>]`.
  - `protocols(name=None, detail=True)` — `show protocols [all [<name>]]`.
  - `routes(table, prefix, protocol, where, detail, best, filtered)` plus
    `routes_exported(protocol)`, `routes_filtered(protocol)` and the
    `routes_by_community` / `routes_by_ext_community` /
    `routes_by_large_community` helpers.
- **Config-change methods** — `config_check()` validates the on-disk config,
  `configure(soft=False)` applies it. Both return a typed `ConfigResult`
  that classifies BIRD's terminator code into `ok=True/False`.
- **Typed data model** in `rsbird.models` — `Status`, `Symbol`, `Protocol`,
  `Channel`, `RouteCounts`, `UpdateStats`, `Route`, `BgpAttrs`, `Community`,
  `ConfigResult`. All `@dataclass(slots=True)`; every class has `to_dict()`
  for JSON-friendly serialisation.
- **Parsers** for every command, version-aware where they need to be:
  - BIRD 1.6: single-line route summary; `BGP.community` (capital-dot) attrs;
    protocol-level counters/filters.
  - BIRD 2.x: two-line route summary; `Channel ipv4`/`ipv6` sub-blocks.
  - BIRD 3.x: lowercase `bgp_*` attrs; `Created:` / `Import state:` /
    `Export state:` channel lines; extra `RX limit` / `limit` columns in
    route-change stats.
- **Async protocol layer** in `rsbird.protocol` — unix-socket I/O,
  greeting parsing, terminator-coded framing, per-call timeouts.
- **Tooling**:
  - `tools/capture_fixtures.py` — talks to a BIRD control socket directly
    and captures raw replies into a self-documenting fixture layout.
  - `lab/bird/` — docker-friendly BIRD configs that inject every community
    flavour, multipath, blackhole/unreachable/dev next-hops and a long-AS-path
    test prefix, so the captured fixtures exercise every parser branch.
- **CLI** — `bin/rsbird`, a **standalone** script (intentionally not part
  of the importable library, in the spirit of `birdc`) with subcommands
  `status` / `protocols` / `tables` / `routes` / `route` / `neighbor` /
  `community` / `ext-community` / `large-community` / `config-check` /
  `raw`. Every subcommand supports `--json`, the socket path can come from
  `$RSBIRD_SOCKET`, and the `raw` subcommand is an escape hatch that
  prints BIRD's reply byte-for-byte for commands the typed API doesn't
  model. ``pip install`` drops the script onto your PATH.
- **Tests** — 143-test pytest suite covering models, the protocol layer
  (via a real-unix-socket `MockBird`), every parser (parametrised over the
  captured BIRD 1.6.8 / 2.0.8 / 3.1.2 fixtures), the CLI, and end-to-end
  client scenarios.

### Compared to `pybird@mh-v1.3.1`

- Async-native: no `asyncio.to_thread` wrapper needed by callers.
- Typed: `@dataclass` outputs with `to_dict()`, no more bare `dict`s.
- BIRD 3 first-class: the lowercase `bgp_*` attribute scheme and the
  extra route-change-stats columns are handled by the parsers, not by
  ad-hoc fix-ups in callers.
- Stdlib only: no `requests` / `aiohttp` / SSH wrapper at the bottom.
- Read-side only by design — `get_config()` / `put_config()` from `pybird`
  are not part of this release; mediate config-file changes through the
  filesystem in your deploy pipeline and `config_check()` + `configure()`
  here.
