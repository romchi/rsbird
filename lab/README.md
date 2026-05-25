# rsbird lab — BIRD configs for fixture capture

A self-contained set of BIRD configs that, when run on top of the existing
`pybird/docker/` compose stack, produce a routing table rich enough to
exercise every parser case rsbird needs: every community flavour, multiple
sources for the same prefix, long AS-paths, import-filtered routes, and the
non-BGP next-hop types (blackhole / unreachable / device / prohibit).

## Topology

```
  AS65010                AS65020
  ┌──────┐               ┌──────┐
  │ rs1  │               │ rs2  │
  │ 2.0.8│               │ 2.0.8│
  └──┬───┘               └──┬───┘
     │                      │
     │   eBGP (v4 + v6)     │
     └─────────┬────────────┘
               │
        ┌──────┼──────┐──────┐
        │      │      │      │
        ▼      ▼      ▼      ▼
     1.6.8v4 1.6.8v6 2.0.8  3.1.2
     AS65068 AS65068 AS65208 AS65231
```

`rs1` originates the bulk of the curated test prefixes; `rs2` announces a
small set that overlaps with one of `rs1`'s prefixes plus one unique
prefix, so the test BIRDs see best-path selection across two sources.

The test BIRDs are passive receivers — they import everything (except one
prefix that's deliberately rejected by an import filter so
`show route filtered` has content), re-export back to rs1/rs2 so
`show route export` is non-empty, and keep the filtered routes around
(`import keep filtered`) so they're queryable.

## What's covered

Prefixes announced by rs1 (each as a v4 `/24` and a v6 `/48`):

| Prefix          | Coverage                                                |
|-----------------|---------------------------------------------------------|
| `10.0.0.0/24`   | blackhole route                                         |
| `10.1.1.0/24`   | unreachable                                             |
| `10.2.2.0/24`   | device route (`via "eth0"`)                             |
| `10.3.3.0/24`   | gateway route (`via <IP>`)                              |
| `10.4.4.0/24`   | prohibit                                                |
| `10.5.5.0/24`   | standard communities + MED                              |
| `10.6.0.0/24`   | many standard communities including NO_EXPORT           |
| `10.7.0.0/24`   | large communities only                                  |
| `10.8.0.0/24`   | extended communities (`rt`, `ro`)                       |
| `10.9.0.0/24`   | all three community kinds + MED                         |
| `10.10.0.0/24`  | long AS-path via `bgp_to_test` filter prepends          |
| `10.50.0.0/24`  | best-path test — both rs1 and rs2 announce this         |
| `10.52.0.0/24`  | rejected by test BIRDs (lands in `show route filtered`) |

rs2 announces `10.50.0.0/24` (overlap) and `10.51.0.0/24` (rs2-unique).
IPv6 mirrors the same scheme under `2001:db8:N::/48`.

Things deliberately **not** covered here (better captured from production
data once available): AS_SET in AS-path, ECMP/multipath, very long real-world
paths, RPKI/ROA tags, RR-client `from` field.

## Usage

The files in this directory are intended to **replace** the matching files
in `pybird/docker/bird/`. The docker-compose at `pybird/docker/docker-compose.yaml`
already wires the volume mounts and the network — no compose changes needed.

```bash
# 1. Swap configs in (back up the originals first if you want).
cp rsbird/lab/bird/rs1-bird.conf      pybird/docker/bird/rs1-bird.conf
cp rsbird/lab/bird/rs2-bird.conf      pybird/docker/bird/rs2-bird.conf
cp rsbird/lab/bird/bird-1.6.8v4.conf  pybird/docker/bird/bird-1.6.8v4.conf
cp rsbird/lab/bird/bird-1.6.8v6.conf  pybird/docker/bird/bird-1.6.8v6.conf
cp rsbird/lab/bird/bird-2.0.8.conf    pybird/docker/bird/bird-2.0.8.conf
cp rsbird/lab/bird/bird-3.1.2.conf    pybird/docker/bird/bird-3.1.2.conf

# 2. Restart so BIRD reloads (recreate is simplest).
cd pybird/docker
docker compose up -d --force-recreate rs1 rs2 bird-1.6.8v4 bird-1.6.8v6 bird-2.0.8 bird-3.1.2

# 3. Give BGP ~30s to converge, then sanity-check.
docker compose exec bird-2.0.8 birdc -s /usr/local/var/run/b208.ctl 'show route count'
docker compose exec bird-2.0.8 birdc -s /usr/local/var/run/b208.ctl 'show route table master4'
```

If `show route count` is non-zero and `show route table master4` lists
the `10.0.0.0/24 … 10.52.0.0/24` family, the lab is healthy.

## Capturing fixtures

The control sockets the docker-compose creates live in
`pybird/docker/run/` (mounted into each container). Run the rsbird capture
tool from the host pointing at them:

```bash
cd /home/rb/lo.work/rsbird

# BIRD 1.6.8 — two daemons, two sockets
python3 tools/capture_fixtures.py \
    --socket-v4 ../pybird/docker/run/b168v4.ctl \
    --socket-v6 ../pybird/docker/run/b168v6.ctl \
    --out fixtures/bird_1.6.8

# BIRD 2.0.8 — single socket
python3 tools/capture_fixtures.py \
    --socket ../pybird/docker/run/b208.ctl \
    --out fixtures/bird_2.0.8

# BIRD 3.1.2 — single socket
python3 tools/capture_fixtures.py \
    --socket ../pybird/docker/run/b312.ctl \
    --out fixtures/bird_3.1.2
```

With the discovery fix in `tools/capture_fixtures.py` the script now picks
up the BGP peers (`rs1_ipv4`, `rs2_ipv4`, `rs1_ipv6`, `rs2_ipv6`) and the
tables (`master4`, `master6`), so each run also captures:

- `show protocols all <peer>` (per peer, varied states)
- `show route count`, `show route table <T> count`
- `show route table <T>` (full)
- `show route for <IP>` and `show route for <IP> all` (sampled prefixes)
- `show route protocol <peer>`, `show route export <peer>`, `show route filtered <peer>`

## Sanity check before sending

After capture, a quick correctness probe:

```bash
grep -l "10.5.5.0/24"  fixtures/bird_*/show_route_table/*.input
grep -l "10.10.0.0/24" fixtures/bird_*/show_route_for/*.input
grep -l "10.52"         fixtures/bird_*/show_route_filtered/*.input
```

The first should find the route in every BIRD version's master4 dump; the
second should show the long-AS-path prefix in the per-prefix detail; the
third confirms the import filter rejected and stored `10.52.0.0/24`.

Then tar the whole `fixtures/` tree and hand it back.

## Notes / caveats

- `import keep filtered` syntax used here is BIRD 2/3 channel-level. If BIRD
  3.1.2 ever complains, the wording is sometimes `keep filtered;` instead;
  adjust per the running version's error message.
- `bgp_path.prepend()` is the BIRD-2/3 filter idiom; BIRD adds the local AS
  on eBGP egress automatically, so the receiver sees one more hop than the
  filter explicitly added.
- The configs do not currently inject AS_SET into the path — BIRD's filter
  syntax for that is awkward and AS_SET is well-represented in real-world
  route-server tables. Capture it from production when available.
