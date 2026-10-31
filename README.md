# as-stats-python

Per-AS traffic statistics from NetFlow, IPFIX and sFlow. A Python rewrite of
[AS-Stats](https://github.com/manuelkasper/AS-Stats) by Manuel Kasper, with a
modern web UI in the spirit of [as-stats-gui](https://github.com/nidebr/as-stats-gui).

It answers the question every peering coordinator has: which networks send and
receive how much traffic over which of my links?

![Top AS](docs/top-as.png)

## Features

- **Collector** for NetFlow v5, NetFlow v9, IPFIX and sFlow v5. It uses the AS numbers your routers export, and looks
  them up by IP address for routers that send none (VyOS, MikroTik, Linux, most switches).
  Sampling rates announced by the router (v9/IPFIX options, sFlow) are applied automatically, or set per exporter or interface.
- **Configured entirely in the web UI.** No `knownlinks` file: the collector lists every interface it sees, with name and current rate,
  and you turn one into a link with a click. Changes are picked up within seconds, without a restart.
- **Top AS** with mirrored per-link sparklines (inbound above the line, outbound below), filters for time range, IPv4/IPv6 and links.
- **AS detail** pages with interactive charts per link or per IP version, per-link totals and shared IX presence.
  Optionally, the remote prefixes of the AS your network talked to (prefix statistics, off by default).
- **Links** overview: all links stacked, and which ASes use each link.
- **Internet exchanges** from PeeringDB: the IXs your AS is present at, with traffic per member. Useful for finding peering candidates you currently reach over transit.
- **AS names** looked up automatically through the RIPEstat API (all RIRs), with country, PeeringDB names and your own overrides.
- **Users and roles**: administrators and read-only users. Nothing is visible without logging in.
- **One Docker image** running everything, with SQLite by default and MariaDB optional.
- Multi-resolution storage (5 minutes, hourly, daily) with configurable retention, so long ranges stay fast.

| AS detail | Links and discovered interfaces |
| --- | --- |
| ![AS detail](docs/as-detail.png) | ![Links settings](docs/links-settings.png) |

## Quick start

```sh
git clone https://github.com/schadom/as-stats-python
cd as-stats-python
docker compose up -d
docker compose logs asstats | grep -A1 "initial admin"
```

The compose file uses host networking (Linux), see [Keep the router's source address](#keep-the-routers-source-address).
Open <http://your-host:8080>, log in as `admin` with the password from the log, and choose a new one.
To set the first password yourself, set `ASSTATS_ADMIN_PASSWORD` before the first start.

Prebuilt images for amd64 and arm64 are published to `ghcr.io/schadom/as-stats-python`.

Then:

1. Point your routers at the host (UDP 9000 for NetFlow/IPFIX, UDP 6343 for sFlow), see [router configuration](#router-configuration).
2. In **Exporters**, add each router. Routers that already send flows are listed and can be added with one click.
3. In **Links & interfaces**, pick the interfaces facing your transit providers, peers and IXs and turn them into links.
4. Optionally enter your own AS number in **General** to load your internet exchanges from PeeringDB.

Traffic appears on the Top AS page within a few minutes.

### With MariaDB

```sh
# edit the passwords in docker-compose.mariadb.yml first
docker compose -f docker-compose.mariadb.yml up -d
```

Any SQLAlchemy URL works in `ASSTATS_DATABASE_URL`; MariaDB/MySQL (`mysql+pymysql://...`) and SQLite are tested,
PostgreSQL works as well. SQLite is fine for most networks. Pick MariaDB for many links, long 5 minute retention, or when
other tools should query the data.

### Keep the router's source address

Exporters are identified by the source address of their flow packets. With published ports (`-p 9000:9000/udp`),
Docker often rewrites that address to its own gateway (`172.17.0.1`). All routers then look like one exporter, their
NetFlow templates overwrite each other, and the flows are decoded wrongly. The UI warns when it sees flows from a Docker
gateway address.

Use host networking instead, which `docker-compose.yml` does by default. With `docker run`:

```sh
docker run -d --name asstats --network host \
  -e ASSTATS_WEB_PORT=8080 -e ASSTATS_NETFLOW_PORT=9000 -e ASSTATS_SFLOW_PORT=6343 \
  -v asstats-data:/data --restart unless-stopped as-stats-python
```

## Router configuration

Routers that know BGP can put the source and destination AS into the flows. Routers that can't are fine too: the
collector then looks up the AS for each address, see [How traffic is counted](#how-traffic-is-counted).

**Juniper (inline IPFIX)**

```
services {
    flow-monitoring {
        version-ipfix {
            template ipv4 { flow-active-timeout 60; ipv4-template; }
            template ipv6 { flow-active-timeout 60; ipv6-template; }
        }
    }
}
forwarding-options {
    sampling {
        instance {
            asstats {
                input { rate 1000; }
                family inet {
                    output {
                        flow-server 192.0.2.10 { port 9000; autonomous-system-type origin; version-ipfix { template { ipv4; } } }
                        inline-jflow { source-address 192.0.2.1; }
                    }
                }
                family inet6 {
                    output {
                        flow-server 192.0.2.10 { port 9000; autonomous-system-type origin; version-ipfix { template { ipv6; } } }
                        inline-jflow { source-address 192.0.2.1; }
                    }
                }
            }
        }
    }
}
```

Apply `sampling input` (family inet and inet6) on the interfaces facing your links.

**Cisco IOS XE (Flexible NetFlow)**

```
flow exporter ASSTATS
 destination 192.0.2.10
 source Loopback0
 transport udp 9000
 option interface-table
 option sampler-table
!
flow monitor ASSTATS-V4
 exporter ASSTATS
 cache timeout active 60
 record netflow ipv4 as
!
flow monitor ASSTATS-V6
 exporter ASSTATS
 cache timeout active 60
 record netflow ipv6 as
!
sampler ASSTATS
 mode random 1 out-of 1000
!
interface TenGigabitEthernet0/0/0
 ip flow monitor ASSTATS-V4 sampler ASSTATS input
 ip flow monitor ASSTATS-V4 sampler ASSTATS output
 ipv6 flow monitor ASSTATS-V6 sampler ASSTATS input
 ipv6 flow monitor ASSTATS-V6 sampler ASSTATS output
```

`option interface-table` gives the UI interface names, `option sampler-table` the sampling rate.

**VyOS** (no AS numbers in the flows, the collector looks them up):

```
set system flow-accounting interface eth0
set system flow-accounting netflow version 9
set system flow-accounting netflow server 192.0.2.10 port 9000
set system flow-accounting netflow source-ip 192.0.2.1
set system flow-accounting netflow sampling-rate 100
```

VyOS does not announce its sampling rate, so enter the same rate on the exporter in the UI. Give each router its own
`source-ip` so they can be told apart.

**sFlow**: export to UDP 6343, with the extended gateway (BGP) data where available. The sampling rate is taken from the
samples. Without gateway data, AS numbers are looked up from the sampled packet headers.

### Which interfaces to monitor

Each flow is counted on the link it entered (inbound, source AS) and on the link it left (outbound, destination AS).
Monitor ingress on all interfaces, or ingress and egress on the link interfaces only. Avoid exporting both ingress and egress
for traffic that passes between two links, otherwise it is counted twice.

## How traffic is counted

For every flow:

- if the input interface belongs to a link, the bytes count as **inbound** on that link for the **source AS**;
- if the output interface belongs to a link, they count as **outbound** on that link for the **destination AS**.

Flows whose **remote** address (the source for inbound, the destination for outbound) is not on the Internet are
ignored by default: private ranges (RFC 1918, CGNAT 100.64.0.0/10, IPv6 ULA), link-local, multicast, documentation
and other reserved ranges. That is router housekeeping such as IPv6 neighbor discovery, router advertisements or
link-local BGP sessions. Local addresses may be private, and IX peering LANs are public, so both are still counted.
Flows without addresses are never filtered. The switch is under **General**, and the Collector status page shows
how many flows are ignored.

Bytes are multiplied by the sampling rate. The rate comes from, in order: the interface override on the link, the exporter
setting, the rate the router announces, or 1.

Where the AS numbers come from is set per exporter:

- **From the router, else IP lookup** (default): the AS numbers in the flow are used. When a flow carries none (AS 0,
  or no AS fields at all), the AS announcing the address is looked up.
- **From the router only**: never look up. Flows without AS numbers count as "Unknown / local".
- **IP lookup only**: always look up, ignoring what the router sends.

The lookup uses the [iptoasn.com](https://iptoasn.com) dataset (public domain), downloaded once a day into the data
directory. The source can be changed under **General**; the Collector status page shows how many ranges are loaded.
Addresses nobody announces (private ranges, for example) count as "Unknown / local".

With **neighbor AS** mode the adjacent AS fields are used where the exporter sends them (IPFIX elements 128/129,
the sFlow extended gateway path). The IP lookup always yields the origin AS.

Traffic is stored in 5 minute buckets by arrival time. Completed hours and days are rolled up into hourly and daily
tables, and queries combine them automatically. Default retention: 3 days at 5 minutes, 90 days hourly, 5 years daily,
changeable under **General**.

### Prefix statistics (optional)

Turned on under **General → Prefix statistics** (off by default). The collector then also records, per hour and link,
the remote prefix of every counted flow, and each AS page lists the prefixes your network talked to, with volume,
share, the links used and a link to bgp.tools.

- Prefixes come from the IP-to-AS dataset: the announced range holding the address, as a CIDR block. Addresses
  outside the dataset are grouped by /24 (IPv4) or /48 (IPv6). Flows need IP addresses, which NetFlow v9, IPFIX and
  sFlow carry; NetFlow v5 does too.
- After each hour, only the biggest prefixes of every AS are kept (50 by default) and only if they moved at least
  1 MB; the rest is summed up as "Other prefixes". Prefix data has its own retention (30 days by default).
- Expect roughly 2 to 3 times the disk space of the hourly AS data. The settings page shows the current size and has
  a button to delete all prefix data.
- Switching it on or off takes effect within seconds, without a restart. Prefix data starts when it is switched on.

## Migrating from AS-Stats

Import your existing `knownlinks` file to create exporters and links in one step:

```sh
docker compose cp knownlinks asstats:/data/knownlinks
docker compose exec asstats asstats import-knownlinks /data/knownlinks
```

Exporters are created with their IP as name, rename them in the UI. RRD history is not imported.

## Configuration

Everything an operator changes at runtime lives in the database and is edited in the UI. Environment variables cover
what is needed before the database is reachable:

| Variable | Default | |
| --- | --- | --- |
| `ASSTATS_DATABASE_URL` | `sqlite:////data/asstats.db` | SQLAlchemy URL, e.g. `mysql+pymysql://user:pass@host/asstats?charset=utf8mb4` |
| `ASSTATS_DATA_DIR` | `/data` | SQLite database and template cache |
| `ASSTATS_ADMIN_USER` | `admin` | First admin account, created when there are no users |
| `ASSTATS_ADMIN_PASSWORD` | random, logged | Password for that account |
| `ASSTATS_NETFLOW_PORT` | `9000` | NetFlow/IPFIX port, `0` disables |
| `ASSTATS_SFLOW_PORT` | `6343` | sFlow port, `0` disables |
| `ASSTATS_LISTEN_ADDRESS` | `::` | Collector bind address (dual stack) |
| `ASSTATS_WEB_PORT` | `8080` | Web UI port |
| `ASSTATS_COOKIE_SECURE` | `false` | Set `true` when served over HTTPS |
| `ASSTATS_SESSION_HOURS` | `168` | Login session lifetime |
| `ASSTATS_TRUSTED_PROXIES` | | Comma separated reverse proxy IPs whose `X-Forwarded-For` is trusted |
| `ASSTATS_FLUSH_INTERVAL` | `60` | Seconds between collector writes |
| `ASSTATS_LOG_LEVEL` | `INFO` | |

### Users

Administrators manage links, exporters, users and settings. Read-only users see all statistics. Passwords set by an
administrator must be changed at the next login. If you lock yourself out:

```sh
docker compose exec asstats asstats user reset-password admin
```

## Architecture

```
routers ──UDP──▶ collector ──▶ database ◀── web app (FastAPI + React UI)
                (asyncio)     SQLite or         │  scheduler: rollups, retention,
                    ▲          MariaDB          │  AS names (RIPEstat), IXs (PeeringDB),
                    └──── IP-to-AS table ◀──────┘  IP-to-AS dataset (iptoasn.com)
```

The container runs two processes, started and restarted by `asstats run`: `asstats collector` decodes flows and writes aggregated counters
every minute, `asstats web` serves the UI and API (`/api`, OpenAPI docs at `/api/docs`) and runs the background jobs.
The collector is a single process. Decoding and aggregation run at about one million NetFlow/IPFIX flows or 300,000 sFlow samples per second on one core in benchmarks, so packet reception is usually the limit.

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                                   # ASSTATS_TEST_DATABASE_URL=... to test another database

ASSTATS_DATA_DIR=./data asstats web &    # API on :8080
ASSTATS_DATA_DIR=./data asstats collector &
asstats flowgen 127.0.0.1 --protocol ipfix   # synthetic flows on ifIndex 1-3 (links) and 100-101
asstats flowgen 127.0.0.1 --protocol v9 --no-as   # like VyOS: addresses only, AS looked up

cd frontend && npm install && npm run dev  # UI on :5173, proxies /api to :8080
npm run build                              # builds into asstats/static
```

## License

MIT. Based on the ideas of AS-Stats by Manuel Kasper (BSD-2-Clause); no code was copied.
