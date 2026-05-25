"""
Parser for ``show symbols`` (and ``show symbols <type>``).

Sample reply::

    0001 BIRD 2.0.8 ready.
    1010-ips_site	constant
     as      	undefined
     master4 	routing table
     rs1_ipv4	protocol
     ...
    0000

Every symbol row is ``NAME[padding]\\tKIND``: the first row carries the
``1010-`` tag, every subsequent row is space-prefixed continuation. ``KIND``
may include a space (``routing table``, ``custom attribute``,
``unknown type`` in BIRD 3) — splitting on the TAB separator preserves it.
"""
from __future__ import annotations

from rsbird.models import Symbol


def parse_symbols(raw: str, *, version: object | None = None) -> list[Symbol]:  # noqa: ARG001 — API parity
    """Parse a ``show symbols`` reply into a list of :class:`Symbol`."""
    out: list[Symbol] = []
    for line in raw.splitlines():
        if line.startswith("1010-"):
            body = line[5:]
        elif line.startswith(" "):
            body = line[1:]  # strip the single-space continuation marker
        else:
            continue  # greeting, terminators, blanks — ignore
        if "\t" not in body:
            continue
        name, kind = body.split("\t", 1)
        out.append(Symbol(name=name.strip(), kind=kind.strip()))
    return out
