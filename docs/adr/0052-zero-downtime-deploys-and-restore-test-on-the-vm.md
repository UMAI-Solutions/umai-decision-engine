# ADR-0052 — Zero-downtime deploys and a restore test on the single VM

- **Status:** Proposed
- **Date:** 2026-10-31
- **Deciders:** engineering (proposal); product owner to accept
- **Builds on:** ADR-0032 (single-VM starter), ADR-0046 (fixed Compose subnet), ADR-0042 (records
  queue), ADR-0048 (webhooks, at least once); launch plan S0-14
- **Evidence:** [`evals/reports/2026-10-31-vm-zero-downtime/summary.md`](../../evals/reports/2026-10-31-vm-zero-downtime/summary.md)

## Context

Stage 0 puts two or three design partners on `umai-beta`, one Azure VM running Docker Compose
(ADR-0032). Two operational gaps:

1. **Every deploy is an outage.** `ship.sh` loads new images and runs `umai.sh up`, which lets
   Compose recreate each changed container in place. Measured on a local copy of the stack under a
   request loop: 774 of 5 320 requests failed, engine and gateway were unreachable for 27 s. On
   2026-10-31 the fixed subnet of ADR-0046 made Compose 2.40 recreate `umai_default`; it stopped every
   container, removed the network and started the containers again **without their service-name DNS
   aliases** (reproduced locally), so the policy service lost `postgres` until a full `down` and
   `up`.
2. **No restore has ever been tried.** The nightly `umai.sh backup` uploads a `pg_dump` and the
   policy volume to Blob Storage. Trying one showed two defects: the dump was taken with
   `--no-privileges`, but the application role's rights come from the migrations' `GRANT`s, so a
   restored store refuses the policy service (it exits with code 3); and the applications directory
   (`/opt/umai/applications`, applications, providers, gateway settings) was not in the archive.

The VM is a B2s (2 vCPU, 4 GB, about 2.6 GB free). Caddy 2.10 is the only ingress.

## Decision

### Deploys

1. **Side by side, one service at a time.** `umai.sh up` asks Compose what it would change
   (`--dry-run create`; a dry `up` waits forever for services that are not running). Each of policy, engine, gateway, console that needs a new container gets one
   beside the old (`up --no-recreate --scale <svc>=2`); when Docker's health check passes, the old
   container is stopped (SIGTERM, within `stop_grace_period`: 30 s, the gateway 5 min for streams)
   and removed. The policy service goes first because it runs the migrations. A new container
   that does not become healthy is stopped and removed; the old one keeps serving and the deploy
   stops with an error. Health checks probe every 2 s while a container starts (`start_interval`,
   Docker Engine 25+); the console gets one (a public brand file).
2. **Caddy finds containers in Docker's DNS and never reloads for a deploy.** Every route uses one
   snippet: `dynamic a <service> <port>` refreshed every second, `lb_try_duration 5s`, no
   keep-alive to the containers (`keepalive off`, `net.ipv4.tcp_tw_reuse=1` in Caddy's namespace).
   A starting container and a stopping one refuse connections (uvicorn binds after its startup and
   closes its listener on SIGTERM, Next.js 14 calls `server.close()`); Caddy retries a refused
   connection on another container, for every method, because nothing was sent. **Caddy 2.11.4 or
   later is required:** 2.10 sent a retried request with an empty body. The stack moves to
   `caddy:2.11.7-alpine`.
3. **The console calls the engine and the policy service through Caddy** (`http://caddy:9100`,
   `http://caddy:9200`, listeners on the Compose network only), so its server-side calls get the
   same retries and never reuse a connection to a stopping container.
4. **Changes that cannot run twice are refused.** A changed network or volume, or a new Postgres or
   Caddy container, makes `up` stop before touching anything, name the change and exit 3.
   `umai.sh up --recreate` takes the whole stack down (every profile; volumes stay) and up again:
   about 32 s without service locally. That is also the fix for 2026-10-31: everything attached to
   a recreated network is recreated with it.
5. **Caddy's own files take effect by reload, only when they change.** Caddy reads one directory
   (`/run/umai/caddy`: the Caddyfile and the console gate); a shipped Caddyfile waits as
   `Caddyfile.new` until the running Caddy validates it, then one reload applies it. A reload can fail the few requests that arrive at that instant (measured: about
   five per reload under a continuous load of 16 clients), so it never happens for an image deploy.
6. **Migrations expand, then contract.** For one deploy the old engine, gateway and console run
   against the schema the new policy service migrated. A migration may add tables, nullable
   columns, columns with defaults and indexes, and add grants; it may not drop, rename, retype or
   tighten anything the running release still uses. Removing something takes two releases: stop
   using it, ship; then drop it. A migration that cannot follow the rule ships with
   `umai.sh up --recreate` and says so in its pull request.

### Backups and the restore test (S0-14 on the VM)

7. **The dump keeps its GRANTs** (`pg_dump --no-owner`, without `--no-privileges`) and the archive
   holds `a/`, the applications directory. Backups taken before this change are repaired when
   restored: the grants of a store migrated from scratch are copied onto it.
8. **`umai.sh restore-test [ARCHIVE | BLOB]`** restores a backup (a file on the VM, a blob fetched to
   the VM with a read-only SAS from stdin or the managed identity, or a backup taken on the spot)
   into a scratch Compose project (`restore-test.yml`): its own internal network (nothing in it can
   call out, so restored webhook endpoints stay silent), its own volumes, no published ports, random
   secrets that are never written down, no retention deletes, no webhook, billing or signing key.
   It compares every table's row count with the dump, reports the migration of the backup and of
   the image, starts the policy service and the engine, has the policy service read the tenants and
   one tenant's keys as the application role (grants and row-level security), checks that the
   engine loads the restored bundles without errors, removes the project and prints each phase's
   duration. It prints names, counts and durations only; the archive never leaves the VM.
9. **Recovery objectives** (derivation in [`docs/security/backup-and-recovery.md`](../security/backup-and-recovery.md)):
   RPO 24 h (the nightly backup); RTO 1 h when the VM survives, 4 h when it is lost (working
   hours, no on-call before S0-11). The restore test's durations on `umai-beta` confirm or correct
   the figures.

## Alternatives considered

| Option | Why not |
|---|---|
| Switch Caddy's upstreams by container name with a graceful `caddy reload` | Built and measured: 3 failed requests per deploy. A reload swaps every server at once; requests that arrive at that instant are dropped (about five per reload under load; `net.ipv4.tcp_migrate_req` does not help) |
| Blue/green Compose projects behind Caddy | The switch is a Caddy reload again, the volumes must become external and shared, and every service runs twice for the whole deploy |
| DNS routing on Caddy 2.10, with or without `request_buffers` | Built and measured: 39 and 91 failed requests per deploy. 2.10 does not rewind the body for a retry (fixed in 2.11.1–2.11.4, caddyserver/caddy #7360, #7547, #7715, #7719) |
| Health-check-based draining (Traefik, docker-rollout's pre-stop hook) | Needs a drain signal in every application (the console is another repository) or a new proxy replacing Caddy's TLS, headers and log setup |
| HAProxy in front, servers drained through its runtime API | A second proxy and its configuration for a few design partners |
| Keep in-place recreation and announce deploy windows | 25–45 s of errors per deploy, and the 2026-10-31 failure mode remains |

## Consequences

- An image deploy no longer interrupts service; the local proof had 0 failed requests in 37 149
  (public routes and the console's internal path, three runs). A deploy takes about a minute.
- For a few seconds per service both versions answer requests (a new container takes requests once
  it listens, before Docker's health check has passed). Console and policy service of different
  releases can meet in that window; their contract must tolerate one release of skew, as the
  schema does.
- While two policy services overlap, both run the webhook worker: a delivery may be sent twice.
  The webhook contract is at least once and receivers deduplicate on `event_id` (ADR-0048).
- Per-process state starts empty in the new container: the failed-credential throttle (ADR-0046)
  and the quota counters (re-read from the ledger, ADR-0043), as after any restart.
- Caddy opens a connection per proxied request (about 0.1 ms on the bridge network). Ports in
  TIME_WAIT are reused; at hundreds of requests per second per container this needs measuring.
- Container names change with every deploy (`umai-engine-3`); use `docker compose -p umai exec
  <service>` and the `com.docker.compose.service` label, not names.
- Memory: one service is doubled at a time; the peak was +150 MiB over the stack's 470–490 MiB,
  far inside the B2s's 2.6 GB free.
- The first deploy of this change refuses (Caddy's container changes) and needs one
  `umai.sh up --recreate`: about half a minute without service, planned.
- Losing the VM in local-file secret mode loses `secrets.env`, which no backup holds: the bundle
  signing key, the tenant HMAC master key and the webhook master key (stored webhook URLs cannot be
  decrypted). The 4 h RTO assumes Key Vault (S0-8) or a copy the operator keeps; without either,
  recovery is partial (see the recovery document).

## Validation

- Local Compose run of the whole stack in an isolated Docker engine (Docker 29.1.3, Compose 2.40.3,
  the images on `umai-beta`): request loops through Caddy (new and kept connections; engine,
  gateway, console, policy-service routes) and on the console's internal path during `umai.sh up`
  with new images of both repositories. Before: 774/5 320 failed. After, three runs: 0/12 608,
  0/12 942 and 0/11 599. Report and harness: `evals/reports/2026-10-31-vm-zero-downtime/`.
- `umai.sh restore-test` locally on a 328 MB store (300 672 decision events, 300 672 usage
  records): PASSED in 83 s; an old-format backup passed with the grant repair and failed without it.
- On `umai-beta` (2026-10-31, with the operator's go-ahead, from a scratch directory): the latest
  nightly blob PASSED in 40 s (grants repaired, migrated 0009 → 0013); a backup taken then PASSED
  in 34 s after the scratch Postgres got a TCP health check. Beta's `/healthz` stayed up.
- Unit tests: `tests/unit/test_vm_script.py` (the deploy decisions, the order, the refusal, the
  restore helpers), `tests/unit/test_vm_files.py` (Compose, Caddyfile, scratch project).
- Revisit when the stack moves to the regional design (ADR-0031: AKS rolling updates and Flexible
  Server PITR replace both), or if a deploy on `umai-beta` shows failed requests in Caddy's log.
