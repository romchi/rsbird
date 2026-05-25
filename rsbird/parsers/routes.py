"""
Parser for ``show route ...`` — every common variant: summary or detail,
single-path or multipath, BIRD 1.6 / 2.x / 3.x.

Output line shapes the parser handles
-------------------------------------

* **BIRD 1.6** single-line route::

    PREFIX             via 10.0.0.1 on eth0 [rs1_ipv4 12:52:10] ! (100) [AS65010i]
                       via 10.0.0.2 on eth0 [rs2_ipv4 12:52:10]   (100) [AS65010i]

* **BIRD 2.x / 3.x** two-line route — header + next-hop on its own line::

    PREFIX             unicast [rs1_ipv4 12:52:11.702] * (100) [AS65010i]
        via 10.0.0.1 on eth0
                       unicast [direct4 12:52:07.480] * (240)
        dev eth0

  ``unicast`` may also be ``blackhole`` / ``prohibit`` / ``unreachable`` /
  ``recursive``.

Detail attribute names differ between BIRD generations:

* BIRD 1.6/2.x: ``BGP.origin``, ``BGP.as_path``, ``BGP.next_hop``,
  ``BGP.local_pref``, ``BGP.community``, ``BGP.ext_community``,
  ``BGP.large_community``.
* BIRD 3.x: lowercase ``bgp_origin``, ``bgp_path``, ``bgp_next_hop``,
  ``bgp_local_pref``, ``bgp_community``, ``bgp_ext_community``,
  ``bgp_large_community`` — same data, different keys.
"""
from __future__ import annotations

import re

from rsbird.models import BgpAttrs, Community, Route
from rsbird.parsers._common import parse_datetime

# -------- regex toolbox -----------------------------------------------------

# Tail of every route header: `[PROTO TIME] [*|!]? (PREF) [AS<asn><origin>]?`
_HEADER_TAIL = re.compile(
    r"\[(?P<proto>\S+)\s+(?P<time>[^\]]+)\]"
    r"\s*(?P<best>[*!])?"
    r"\s*\((?P<pref>\d+(?:/[^)\s]+)?)\)"
    r"\s*(?:\[AS(?P<asn>\d+)(?P<origin>[ie?])\])?"
    r"\s*$"
)

# v1.6: PREFIX (optional) + `via X on Y` | `dev Y` + tail.
_V1_HEADER = re.compile(
    r"^\s*(?P<prefix>[0-9a-fA-F.:/]+)?"
    r"\s+(?:via\s+(?P<via>\S+)\s+on\s+(?P<iface_via>\S+)|dev\s+(?P<iface_dev>\S+))"
    r"\s+"
)

# v2/v3: PREFIX (optional) + kind word (unicast/blackhole/...) + tail.
_V2_HEADER = re.compile(
    r"^\s*(?P<prefix>[0-9a-fA-F.:/]+)?"
    r"\s+(?P<kind>unicast|blackhole|prohibit|unreachable|recursive)"
    r"\s+"
)

_INT = re.compile(r"^-?\d+$")
_PARENS = re.compile(r"\(([^)]+)\)")

# Attribute keys we read from `1012-` blocks (and tab-continuation lines).
# Maps the on-the-wire name to a ``(target, slot, kind)`` triple, where
# ``target`` is "bgp" or "route" and ``kind`` tells the value parser what
# shape to expect.
_ATTR_MAP: dict[str, tuple[str, str, str]] = {
    # Legacy (BIRD 1.6 / 2.x).
    "BGP.origin":           ("bgp", "origin", "string"),
    "BGP.as_path":          ("bgp", "as_path", "as_path"),
    "BGP.next_hop":         ("bgp", "next_hop", "hops"),
    "BGP.local_pref":       ("bgp", "local_pref", "int"),
    "BGP.med":              ("bgp", "med", "int"),
    "BGP.community":        ("bgp", "communities", "comm_std"),
    "BGP.ext_community":    ("bgp", "ext_communities", "comm_ext"),
    "BGP.large_community":  ("bgp", "large_communities", "comm_large"),
    "BGP.aggregator":       ("bgp", "aggregator", "string"),
    "BGP.originator_id":    ("bgp", "originator_id", "string"),
    "BGP.cluster_list":     ("bgp", "cluster_list", "hops"),
    "BGP.atomic_aggregate": ("bgp", "atomic_aggregate", "bool"),

    # BIRD 3.x (lowercase + underscore).
    "bgp_origin":           ("bgp", "origin", "string"),
    "bgp_path":             ("bgp", "as_path", "as_path"),
    "bgp_next_hop":         ("bgp", "next_hop", "hops"),
    "bgp_local_pref":       ("bgp", "local_pref", "int"),
    "bgp_med":              ("bgp", "med", "int"),
    "bgp_community":        ("bgp", "communities", "comm_std"),
    "bgp_ext_community":    ("bgp", "ext_communities", "comm_ext"),
    "bgp_large_community":  ("bgp", "large_communities", "comm_large"),
}


# -------- value helpers -----------------------------------------------------

def _parse_as_path(value: str) -> list[int | frozenset[int]]:
    """``"65010 65020 {64500 64501}"`` -> ``[65010, 65020, frozenset({64500, 64501})]``."""
    out: list[int | frozenset[int]] = []
    tokens = re.findall(r"\{[^}]*\}|\(\[[^]]*\]\)|\S+", value)
    for token in tokens:
        if token.startswith("{") and token.endswith("}"):
            members = re.findall(r"\d+", token)
            out.append(frozenset(int(m) for m in members))
        elif _INT.match(token):
            out.append(int(token))
    return out


def _parse_int(value: str) -> int | None:
    value = value.strip()
    return int(value) if _INT.match(value) else None


def _parse_communities(value: str, kind: str) -> list[Community]:
    """Parse `(a,b) (c,d) ...` / `(rt, a, b) ...` / `(a, b, c) ...`."""
    out: list[Community] = []
    for match in _PARENS.finditer(value):
        parts = [p.strip() for p in match.group(1).split(",")]
        try:
            if kind == "standard" and len(parts) == 2:
                out.append(Community.standard(int(parts[0]), int(parts[1])))
            elif kind == "large" and len(parts) == 3:
                out.append(Community.large(int(parts[0]), int(parts[1]), int(parts[2])))
            elif kind == "extended" and len(parts) == 3:
                out.append(Community.extended(parts[0], int(parts[1]), int(parts[2])))
        except (ValueError, TypeError):
            continue
    return out


# -------- header / attribute appliers ---------------------------------------

def _apply_attr(route: Route, raw_key: str, value: str) -> None:
    spec = _ATTR_MAP.get(raw_key)
    if spec is None:
        return
    target, slot, kind = spec
    if route.bgp is None:
        route.bgp = BgpAttrs()
    bgp = route.bgp

    if kind == "string":
        setattr(bgp, slot, value.strip())
    elif kind == "int":
        v = _parse_int(value)
        if v is not None:
            setattr(bgp, slot, v)
    elif kind == "bool":
        setattr(bgp, slot, bool(value.strip()))
    elif kind == "as_path":
        setattr(bgp, slot, _parse_as_path(value))
    elif kind == "hops":
        setattr(bgp, slot, value.split())
    elif kind in {"comm_std", "comm_ext", "comm_large"}:
        sub = {"comm_std": "standard", "comm_ext": "extended", "comm_large": "large"}[kind]
        setattr(bgp, slot, _parse_communities(value, sub))


def _set_via(route: Route, via: str | None, iface: str | None) -> None:
    if via and not route.via:
        route.via = via
    if iface and not route.interface:
        route.interface = iface


def _apply_header_tail(route: Route, tail_match: re.Match) -> None:
    route.protocol = tail_match.group("proto")
    route.learned = parse_datetime(tail_match.group("time"))
    route.best = tail_match.group("best") in {"*", "!"}
    # `(100)` or `(100/-)` — keep just the leading int.
    pref = tail_match.group("pref")
    if pref:
        head = pref.split("/", 1)[0]
        if _INT.match(head):
            route.preference = int(head)
    asn = tail_match.group("asn")
    if asn:
        route.origin_as = int(asn)


def _new_route_from_header(line: str, current_prefix: str | None) -> tuple[Route, str | None] | None:
    """Try to parse ``line`` as a route header; return (route, new_prefix).

    Returns ``None`` if the line is not a route header.
    """
    # Try v2/v3 (kind word) first — it's narrower.
    m2 = _V2_HEADER.match(line)
    if m2:
        tail = _HEADER_TAIL.search(line[m2.end():])
        if not tail:
            return None
        prefix = m2.group("prefix") or current_prefix
        if prefix is None:
            return None
        route = Route(prefix=prefix, type=m2.group("kind"))
        _apply_header_tail(route, tail)
        return route, prefix

    # Fallback: BIRD 1.6 single-line shape.
    m1 = _V1_HEADER.match(line)
    if m1:
        tail = _HEADER_TAIL.search(line[m1.end():])
        if not tail:
            return None
        prefix = m1.group("prefix") or current_prefix
        if prefix is None:
            return None
        route = Route(prefix=prefix)
        _apply_header_tail(route, tail)
        _set_via(route, m1.group("via"), m1.group("iface_via") or m1.group("iface_dev"))
        return route, prefix
    return None


def _apply_tab_continuation(route: Route, body: str) -> None:
    """Handle TAB-prefixed continuation lines (``\\tvia ...`` / ``\\tBGP.foo: ...``)."""
    text = body.lstrip("\t ").rstrip()
    if not text:
        return
    # `via X on Y` / `dev Y` — v2/v3 next-hop line
    via_m = re.match(r"via\s+(\S+)\s+on\s+(\S+)", text)
    if via_m:
        _set_via(route, via_m.group(1), via_m.group(2))
        return
    dev_m = re.match(r"dev\s+(\S+)", text)
    if dev_m:
        _set_via(route, None, dev_m.group(1))
        return
    if text.startswith("Type:"):
        route.type = text.split(":", 1)[1].strip()
        return
    if ":" in text:
        key, value = text.split(":", 1)
        key = key.strip()
        if key in _ATTR_MAP:
            _apply_attr(route, key, value.strip())


# -------- main entry --------------------------------------------------------

def parse_routes(raw: str, *, version: object | None = None) -> list[Route]:  # noqa: ARG001 — API parity
    """Parse any ``show route ...`` reply into a list of :class:`Route`."""
    routes: list[Route] = []
    current_prefix: str | None = None
    current_route: Route | None = None

    for raw_line in raw.splitlines():
        # `1007-Table master4:` header — ignored, but skip the leading code.
        if raw_line.startswith("1007-Table "):
            continue

        if raw_line.startswith("1007-"):
            body = raw_line[5:]
        elif raw_line.startswith("1008-") or raw_line.startswith("1012-"):
            # Attribute / type block — TAB-indented continuation of last route.
            if current_route is not None:
                _apply_tab_continuation(current_route, raw_line[5:])
            continue
        elif raw_line.startswith(" "):
            body = raw_line[1:]
        else:
            # Greeting (0001), terminator (0000), unknown — skip.
            continue

        if not body.strip():
            continue

        # TAB-prefixed continuation of the previous route (BIRD 2.x/3.x via/dev,
        # plus untagged BGP.* / bgp_* lines that follow a 1012-).
        if body.startswith("\t"):
            if current_route is not None:
                _apply_tab_continuation(current_route, body)
            continue

        # Otherwise this is a route header — start a new Route.
        parsed = _new_route_from_header(body, current_prefix)
        if parsed is None:
            continue
        route, prefix = parsed
        if prefix is not None:
            current_prefix = prefix
        routes.append(route)
        current_route = route

    return routes
