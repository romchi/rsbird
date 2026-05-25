#!/usr/bin/env python3
"""
capture_fixtures.py — capture raw BIRD control-socket output for rsbird tests.

Run this ON a BIRD route server (needs read access to the control socket).
It speaks the BIRD CLI socket protocol directly, so the saved files contain
the raw numbered-reply output that rsbird's parsers consume — NOT the
reformatted text that ``birdc`` prints.

Usage
-----
    python3 capture_fixtures.py --socket /run/bird/bird.ctl --out fixtures/<ver>

For BIRD 1.6 (separate v4 and v6 daemons) just run the script twice — one
invocation per daemon, into its own --out directory. The socket protocol is
identical regardless of address family, so the script doesn't care.

Output
------
    ./fixtures/<command-slug>/NNN.input   raw socket reply, one file per capture
    ./fixtures/_manifest.json             index: slug -> exact command + meta

Hand the whole ./fixtures/ directory back for curation into the rsbird test
suite. Stdlib only; runs on Python 3.6+.
"""
import argparse
import json
import os
import re
import socket
import time

# ---------------------------------------------------------------------------
# Optional overrides — edit to capture extra interesting cases. Leave empty to
# rely on auto-discovery. TABLES/PEERS override discovery; PREFIXES/COMMUNITIES
# are always added on top of whatever is discovered.
# ---------------------------------------------------------------------------
TABLES = []        # e.g. ["example", "master", "test_ix"]
PEERS = []         # e.g. ["PS1", "rs_client_as123"]
PREFIXES = []      # e.g. ["8.8.8.8", "2001:4860:4860::8888"]

# BIRD `where bgp_*community ~ [(...)]` filter samples. Each list entry is
# the contents of the [( ... )] tuple — one community per fixture. Defaults
# point at the synthetic values the rsbird lab injects; override for prod.
COMMUNITIES = [
    "(65010, 1)",       # rs1-injected standard community
    "(0, 13335)",       # well-known transit-style community
    "(65535, 65281)",   # NO_EXPORT
]
EXT_COMMUNITIES = [
    "(rt, 65010, 1)",   # route target
    "(ro, 65010, 100)", # route origin
]
LARGE_COMMUNITIES = [
    "(65010, 1, 2)",
]

SAMPLE_PEERS = 8       # how many discovered peers to sample (varied states)
SAMPLE_PREFIXES = 6    # how many discovered prefixes to sample for detail
SOCKET_TIMEOUT = 90.0  # seconds

# A BIRD reply ends with one of these codes at the start of a line. The
# greeting "0001 BIRD ... ready." is deliberately NOT in this set.
_TERMINAL = re.compile(
    r"(?:^|\n)(?:0000|0003|0004|0013|0018|0019|0020|8\d{3}|9\d{3})(?: |\n|$)"
)


def query(socket_path, command, timeout=SOCKET_TIMEOUT):
    """Send one command to a BIRD control socket; return the raw reply text."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
        sock.sendall((command + "\n").encode("utf-8"))
        buf = b""
        while True:
            chunk = sock.recv(1 << 16)
            if not chunk:
                break
            buf += chunk
            if _TERMINAL.search(buf.decode("utf-8", "replace")):
                break
        return buf.decode("utf-8", "replace")
    finally:
        sock.close()


def _safe(value):
    """Sanitise a path/peer/prefix for use inside a filename."""
    return re.sub(r"[/:]+", "_", value.strip('"'))


def fixture_key(command):
    """Return ``(subdir, name)`` for a BIRD ``command`` — readable, 1:1, stable.

    Files end up at ``<subdir>/<name>.input`` so the filename itself answers
    "which command produced this?" without consulting the manifest.
    """
    tokens = command.split()

    # ---- no-argument singletons --------------------------------------
    flat = {
        "show status":           ("show_status",          "default"),
        "show symbols":          ("show_symbols",         "default"),
        "show symbols table":    ("show_symbols_table",   "default"),
        "show protocols":        ("show_protocols",       "default"),
        "show protocols all":    ("show_protocols_all",   "default"),
        "show route count":      ("show_route_count",     "default"),
    }
    if command in flat:
        return flat[command]

    # ---- show protocols all <peer> -----------------------------------
    if tokens[:3] == ["show", "protocols", "all"] and len(tokens) >= 4:
        return ("show_protocols_all", _safe(tokens[3]))

    # ---- show route table <T> [count | where ...] --------------------
    if tokens[:3] == ["show", "route", "table"] and len(tokens) >= 4:
        table = _safe(tokens[3])
        if tokens[-1] == "count":
            return ("show_route_count", table)
        if "where" in tokens:
            expr = command.split("where", 1)[1].strip()
            # `bgp_<kind> ~ [(a, b, ...)]` is the common case — emit a tidy
            # filename like `master.community_0_13335.input`.
            m = re.match(r"bgp_(\w+)\s*~\s*\[\s*\(([^)]+)\)\s*\]", expr)
            if m:
                kind, value = m.group(1), m.group(2)
                value_clean = re.sub(r"[^\w]+", "_", value).strip("_")
                return ("show_route_where", "%s.%s_%s" % (table, kind, value_clean))
            return ("show_route_where", "%s.%s" % (table, _safe(expr)))
        return ("show_route_table", table)

    # ---- show route for <prefix> [all] -------------------------------
    if tokens[:3] == ["show", "route", "for"] and len(tokens) >= 4:
        prefix = _safe(tokens[3])
        return ("show_route_for", prefix + (".all" if "all" in tokens[4:] else ""))

    # ---- show route protocol <name> [filtered] -----------------------
    if tokens[:3] == ["show", "route", "protocol"] and len(tokens) >= 4:
        name = _safe(tokens[3])
        return ("show_route_protocol",
                name + (".filtered" if "filtered" in tokens[4:] else ""))

    # ---- show route export <name> ------------------------------------
    if tokens[:3] == ["show", "route", "export"] and len(tokens) >= 4:
        return ("show_route_export", _safe(tokens[3]))

    # ---- fallback ----------------------------------------------------
    slug = re.sub(r"[^\w]+", "_", command).strip("_").lower()
    return ("misc", slug or "default")


def save(out_dir, command, raw, manifest):
    subdir, name = fixture_key(command)
    folder = os.path.join(out_dir, subdir)
    os.makedirs(folder, exist_ok=True)
    rel = "%s/%s.input" % (subdir, name)
    with open(os.path.join(out_dir, rel), "w", encoding="utf-8") as fobj:
        fobj.write(raw)
    manifest.append({"file": rel, "command": command, "bytes": len(raw)})
    print("  %-46s %-30s %9d B" % (command[:46], rel[-30:], len(raw)))
    return raw


def discover_peers(raw_protocols):
    """Pull BGP protocol names + their state line out of `show protocols`.

    BIRD prints the first protocol with a ``1002-`` code and every following
    one as a space-prefixed continuation line — strip both before matching.
    """
    peers = []
    for raw_line in raw_protocols.splitlines():
        line = re.sub(r"^1002-", "", raw_line.lstrip())
        m = re.match(r"(\S+)\s+BGP\s+\S+\s+(\S+)\s+\S+\s*(.*)", line)
        if m:
            peers.append((m.group(1), (m.group(3) or m.group(2)).strip()))
    return peers


def discover_tables(raw_symbols):
    """Pull routing-table names out of `show symbols`."""
    tables = []
    for raw_line in raw_symbols.splitlines():
        line = re.sub(r"^10\d\d-", "", raw_line.lstrip())
        m = re.match(r"(\S+)\s+routing\s+table", line)
        if m:
            tables.append(m.group(1))
    return tables


def discover_prefixes(raw_routes, limit):
    """Pull a few distinct prefixes out of a `show route table` reply."""
    found = []
    for line in raw_routes.splitlines():
        m = re.search(r"([0-9a-fA-F:.]+/\d+)", line)
        if m and m.group(1) not in found:
            found.append(m.group(1))
        if len(found) >= limit:
            break
    return found


def pick_varied(peers, count):
    """Pick up to `count` peers covering as many distinct states as possible."""
    by_state = {}
    for name, state in peers:
        by_state.setdefault(state, []).append(name)
    chosen, pools = [], list(by_state.values())
    while pools and len(chosen) < count:
        for pool in list(pools):
            if pool:
                chosen.append(pool.pop(0))
            if not pool:
                pools.remove(pool)
            if len(chosen) >= count:
                break
    return chosen


def quoted(name):
    """Quote a protocol name for the CLI if it has non-word characters."""
    return name if re.match(r"^\w+$", name) else '"%s"' % name


def run_socket(socket_path, out_dir, manifest):
    print("\n=== socket: %s ===" % socket_path)

    def cap(command):
        try:
            raw = query(socket_path, command)
        except Exception as exc:  # noqa: BLE001 — capture tool, log and continue
            print("  !! %-44s FAILED: %s" % (command, exc))
            return ""
        return save(out_dir, command, raw, manifest)

    status = cap("show status")
    ver = re.search(r"BIRD\s+([\w.]+)", status)
    print("  -> BIRD version: %s" % (ver.group(1) if ver else "unknown"))

    symbols = cap("show symbols")
    cap("show route count")

    protocols = cap("show protocols")
    cap("show protocols all")

    tables = TABLES or discover_tables(symbols)
    peers = discover_peers(protocols)
    peer_names = PEERS or pick_varied(peers, SAMPLE_PEERS)
    print("  -> tables: %s" % (", ".join(tables) or "none"))
    print("  -> %d BGP protocols; sampling: %s" % (len(peers), ", ".join(peer_names)))

    for peer in peer_names:
        cap("show protocols all %s" % quoted(peer))

    sample_prefixes = list(PREFIXES)
    for table in tables:
        cap("show route table %s count" % table)
        routes = cap("show route table %s" % table)
        sample_prefixes += discover_prefixes(routes, SAMPLE_PREFIXES)

    for prefix in sample_prefixes:
        cap("show route for %s" % prefix)
        cap("show route for %s all" % prefix)

    for peer in peer_names:
        cap("show route protocol %s" % quoted(peer))
        cap("show route export %s" % quoted(peer))
        # BIRD's `filtered` is a modifier, not a peer arg — it has to be
        # combined with `protocol <name>` to scope by peer.
        cap("show route protocol %s filtered" % quoted(peer))

    # `where` queries for each community kind. BIRD 1.6 has no
    # bgp_large_community attribute — those captures will record the BIRD
    # error reply, which is itself useful for the parser.
    for table in tables:
        for value in COMMUNITIES:
            cap("show route table %s where bgp_community ~ [%s]" % (table, value))
        for value in EXT_COMMUNITIES:
            cap("show route table %s where bgp_ext_community ~ [%s]" % (table, value))
        for value in LARGE_COMMUNITIES:
            cap("show route table %s where bgp_large_community ~ [%s]" % (table, value))


def main():
    ap = argparse.ArgumentParser(description="Capture raw BIRD socket output for rsbird.")
    ap.add_argument("--socket", required=True, help="BIRD control socket path")
    ap.add_argument("--out", default="fixtures", help="output directory (default: ./fixtures)")
    args = ap.parse_args()

    if not os.path.exists(args.socket):
        ap.error("socket not found: %s" % args.socket)

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    manifest = []

    started = time.time()
    run_socket(args.socket, out_dir, manifest)

    with open(os.path.join(out_dir, "_manifest.json"), "w", encoding="utf-8") as fobj:
        json.dump({"captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "entries": manifest}, fobj, indent=2)

    total = sum(e["bytes"] for e in manifest)
    print("\nDone: %d captures, %.1f KiB, %.1fs -> %s"
          % (len(manifest), total / 1024, time.time() - started, out_dir))
    print("Hand the whole '%s' directory back for curation." % args.out)


if __name__ == "__main__":
    main()
