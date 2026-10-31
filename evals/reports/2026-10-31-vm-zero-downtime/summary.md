# Zero-downtime deploys and restore test on the single VM — local proof

- **Date:** 2026-10-31
- **For:** ADR-0052, launch plan S0-14, `deploy/vm/README.md` §4a–§4c
- **Where:** a local copy of the `umai-beta` stack, never beta itself. One `docker:29.1.3-dind`
  container played the VM (its own Docker engine, so nothing else on the laptop was touched), with
  Docker Compose 2.40.3 (the VM's version), the images running on beta (engine `5a21514`, console
  `7be65ff`), `deploy/vm` of this change, `UMAI_GATEWAY=true`, synthetic configuration and secrets
  (`harness/zdt.sh setup`). Laptop: 22 cores; the B2s has 2.
- **Load:** `harness/loadloop.py`, run from the engine image. Through Caddy (`https://localhost`):
  `POST /v1/evaluate` (synthetic TCKN `10000000146`), `GET /readyz`, `GET /v1/models` (gateway),
  `GET /` and a brand file (console), `POST /hooks/iyzico` (policy service). On the Compose
  network: `caddy:9100/readyz` and `caddy:9200/healthz`, the console's own path to the backends.
  Each target has a worker with a new connection per request and one with a kept connection;
  about 100 requests/s in all (authenticated workers paced under the 20/s per-credential limit).
  A failure is a transport error or any status other than the target's usual one.
- **Deploy:** `harness/zdt.sh newimages` gives both images a new id under the same tag (what
  `ship.sh` does), then `umai.sh up` with the loops running.

## Results

| Run | Script | Requests | Failed | Longest outage | p99 / max latency |
|---|---|---|---|---|---|
| `before` | `main` (Compose recreates in place) | 5 320 | **774** | 27.4 s | 110 / 1 303 ms |
| `zdt1` + `zdt1i` | rejected: Caddy reload switches to container names | 13 764 | 3 | at the reload | 138 / 559 ms |
| `zdt3` + `zdt3i` | rejected: DNS routing on Caddy 2.10 | 13 045 | 91 | 0.8 s | 155 / 1 230 ms |
| **`zdt4` + `zdt4i`** | **ADR-0052 (DNS routing, Caddy 2.11.7)** | 12 608 | **0** | — | 189 / 750 ms |
| **`zdt5` + `zdt5i`** | **ADR-0052, again** | 12 942 | **0** | — | 177 / 671 ms |
| **`zdt6` + `zdt6i`** | **ADR-0052, final script (`--dry-run create`)** | 11 599 | **0** | — | max 1 266 ms |
| `rec2` | `umai.sh up --recreate` (planned) | 7 049 | 3 593 | 31.5 s | — |

Each `up` replaced policy, engine, gateway and console in 47–62 s (`runs/up-zdt4.log`); an `up`
with nothing to change took 3 s and touched nothing. A shipped Caddyfile that does not parse is
refused before anything changes; a valid one is validated, then applied by one reload.
Per-run summaries with every failure's time, target and error: `runs/summary-*.json`.

Why the rejected variants failed, from Caddy's error log:

- **Reload switching** (`zdt1`): a reload swaps every server at once and drops the requests that
  arrive at that instant (`RemoteProtocolError`). A separate test counted about five per reload
  under 16 continuous clients; `net.ipv4.tcp_migrate_req=1` made no difference. A request already
  in flight (4 s) survived a reload.
- **Caddy 2.10** (`zdt2`, `zdt3`): a refused connection is retried, but the retry carries a closed
  or empty body: `http: invalid Read on closed Body`, then with `request_buffers`
  `ContentLength=322 with Body length 0`. Fixed in Caddy 2.11.1–2.11.4 (#7360, #7547, #7715,
  #7719). With 2.11.7, 12 rolls of a uvicorn backend under 16 POSTing clients: 6 145 requests, 0
  failed, every body intact.

## Memory (B2s headroom)

`docker stats` every ~2 s during `zdt4` and `zdt5` (`runs/stats-zdt4.txt`):

| | Stack before | Peak during the deploy | Extra |
|---|---|---|---|
| `zdt4` | 464 MiB | 614 MiB | +149 MiB |
| `zdt5` | 485 MiB | 625 MiB | +141 MiB |

One service runs twice at a time; the largest is the policy service (130–158 MiB; 158 MiB after
load in the Stage 0 measurements). Beta has about 2.6 GB free: the deploy uses about 6 % of it.

## Restore test (S0-14)

Store filled with synthetic records: 300 672 decision events, 300 672 usage records, 6 tenants,
15 keys, 20 audit events; 328 MB.

| Run | Result | Phases |
|---|---|---|
| `umai.sh restore-test` (backup taken now, 36 MB archive) | PASSED, 83 s | backup 8 s · unpack and count 21 s · Postgres and load 21 s · counts and migrations 7 s · policy service and engine 19 s · checks 3 s · removal 4 s |
| old-format archive (`--no-privileges`, no `a/`) | PASSED, 87 s | the dump has no GRANTs: repaired from a store migrated from scratch |
| the same, repair switched off | FAILED | the policy service exits with code 3: a backup taken today restores a store the services cannot read |

Every table's row count equalled the dump's. A real restore following README §4c (rehearsed with
`harness/rehearse-restore.sh`, a local archive instead of the download) is in the next section.

## Restore rehearsal (README §4c)

`harness/rehearse-restore.sh` follows README §4c on the same store, with a local archive instead
of the blob download, under the request loop (`runs/summary-restore.json`):

| Step | Duration |
|---|---|
| unpack, `down`, policy volume and applications, a fresh Postgres | 14 s |
| loading the dump (328 MB store) | 12 s |
| `umai.sh up` to a healthy stack | 21 s |
| **outage seen by the loop** | **46.7 s** (4 207 of 6 498 requests failed, all inside it) |

Afterwards 300 818 decision events (the 300 672 plus the loop's own) and every service healthy.

## Restore drill on `umai-beta` (with the operator's go-ahead)

The PR's `umai.sh` and `restore-test.yml` ran from a root-only scratch directory on the VM, not
over the live deploy files; the live containers kept their ids and `/opt/umai/src` was unchanged.

| Backup | Result | Duration |
|---|---|---|
| nightly blob `umai-backup-20261031T023348Z.tar.gz` (old format: no GRANTs, no `a/`, migration 0009), read-only SAS for that blob on stdin | PASSED twice: rows equal, grants repaired, migrated 0009 → 0013, 4 tenants, 7 bundles, 0 load errors | 40 s, 42 s |
| taken on the spot (new format, 20 tables, 2 application files) | first FAILED loading the dump; PASSED twice after the fix | 34 s, 34 s |

The failure: with `start_interval: 1s` the scratch Postgres was reported healthy over its socket
while the image's init scripts still ran on a temporary server, and the load hit that server's
restart (the download had hidden it in the blob run; locally the timing never showed it). The
health check now uses TCP, which the temporary server does not listen on; the error filter is
case-insensitive. Beta's public `/healthz` during the drills: 334 of 335 probes answered 200
(p99 0.26 s); the one miss never reached the VM (11 ms client-side; Caddy logged 334 requests, no
5xx).

## Reproduce

```bash
docker run -d --privileged --name umai-zdt-vm -e DOCKER_TLS_CERTDIR= docker:29.1.3-dind
docker exec umai-zdt-vm apk add bash jq curl openssl coreutils gzip tar
# Compose 2.40.3 into /usr/local/libexec/docker/cli-plugins/docker-compose; docker save | docker load
# the two images, postgres:17-alpine, caddy:2.11.7-alpine, alpine; copy deploy/ to
# /opt/umai/src/umai-decision-engine and harness/ to /zdt; then inside:
/zdt/zdt.sh setup && /opt/umai/src/umai-decision-engine/deploy/vm/umai.sh up --recreate
/zdt/zdt.sh loop-start run host engine.evaluate engine.readyz gateway.models console.root console.brand policy.hook
/zdt/zdt.sh loop-start runi umai_default internal.engine internal.policy
/zdt/zdt.sh newimages && /opt/umai/src/umai-decision-engine/deploy/vm/umai.sh up
/zdt/zdt.sh loop-stop run && /zdt/zdt.sh loop-stop runi
```
