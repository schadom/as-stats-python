# as-stats-python

Per-AS traffic statistics from NetFlow, IPFIX and sFlow. A Python rewrite of
[AS-Stats](https://github.com/manuelkasper/AS-Stats) by Manuel Kasper, with a
modern web UI in the spirit of [as-stats-gui](https://github.com/nidebr/as-stats-gui).

It answers the question every peering coordinator has: which networks send and
receive how much traffic over which of my links?

![Top AS](docs/top-as.png)

## Features

- **Collector** for NetFlow v5, NetFlow v9, IPFIX and sFlow v5, using the AS numbers your routers export.
  Sampling rates announced by the router (v9/IPFIX options, sFlow) are applied automatically, or set per exporter or interface.
- **Configured entirely in the web UI.** No `knownlinks` file: the collector lists every interface it sees, with name and current rate,
  and you turn one into a link with a click. Changes are picked up within seconds, without a restart.
- **Top AS** with mirrored per-link sparklines (inbound above the line, outbound below), filters for time range, IPv4/IPv6 and links.
- **AS detail** pages with interactive charts per link or per IP version, per-link totals and shared IX presence.
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

The exporter is identified by the source IP of the flow packets. Docker's default port publishing usually keeps it.
If your setup rewrites it (check the source addresses on the Collector status page, e.g. with some overlay networks), run the container with `network_mode: host`.

## Router configuration

The routers must put AS numbers into the flows (BGP source and destination AS). Choose origin AS (default) or neighbor AS,
see [How traffic is counted](#how-traffic-is-counted).

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

**sFlow**: export to UDP 6343 with the extended gateway (BGP) data enabled. The sampling rate is taken from the samples.

### Which interfaces to monitor

Each flow is counted on the link it entered (inbound, source AS) and on the link it left (outbound, destination AS).
Monitor ingress on all interfaces, or ingress and egress on the link interfaces only. Avoid exporting both ingress and egress
for traffic that passes between two links, otherwise it is counted twice.

## How traffic is counted

For every flow:

- if the input interface belongs to a link, the bytes count as **inbound** on that link for the **source AS**;
- if the output interface belongs to a link, they count as **outbound** on that link for the **destination AS**.

Bytes are multiplied by the sampling rate. The rate comes from, in order: the interface override on the link, the exporter
setting, the rate the router announces, or 1.

With **neighbor AS** mode the adjacent AS fields are used where the exporter sends them (IPFIX elements 128/129,
the sFlow extended gateway path). Flows that carry AS 0 (often traffic from or to your own prefixes) are shown as
"Unknown / local".

Traffic is stored in 5 minute buckets by arrival time. Completed hours and days are rolled up into hourly and daily
tables, and queries combine them automatically. Default retention: 3 days at 5 minutes, 90 days hourly, 5 years daily,
changeable under **General**.

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
                               MariaDB          └─ AS names (RIPEstat), IXs (PeeringDB)
```

The container runs two processes under supervisord: `asstats collector` decodes flows and writes aggregated counters
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

cd frontend && npm install && npm run dev  # UI on :5173, proxies /api to :8080
npm run build                              # builds into asstats/static
```

## License

MIT. Based on the ideas of AS-Stats by Manuel Kasper (BSD-2-Clause); no code was copied.
