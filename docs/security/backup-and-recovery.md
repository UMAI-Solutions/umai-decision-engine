# Backup and recovery on the single VM (S0-14, adapted)

**Status:** 2026-10-31, proposal with ADR-0052. Applies to `umai-beta` (ADR-0032) until the regional
stack's Flexible Server PITR and geo-redundant backup (ADR-0031, launch plan S0-14) replace it.
Runbook: [`deploy/vm/README.md`](../../deploy/vm/README.md) §4b and §4c.

## What is backed up

`umai.sh backup` runs nightly at 02:30 UTC (systemd timer, up to 15 min random delay, `Persistent`)
and uploads one archive to the storage account's `backups` container (30-day lifecycle rule):

| Part | Content | Since |
|---|---|---|
| `db/umai.sql.gz` | `pg_dump` of the tenant store (owner role, all tenants, with `GRANT`s) | GRANTs: ADR-0052 |
| `p/` | the policy volume: bundles, signed versions, key-registry snapshots | ADR-0032 |
| `a/` | `/opt/umai/applications`: applications, providers, gateway settings | ADR-0052 |

Not in any backup: `secrets.env` (local-file mode) and `config.env`. Key Vault mode keeps the
secrets in the vault; local-file mode keeps them only on the VM disk (below, "VM lost").

## Restore test

`umai.sh restore-test [ARCHIVE | BLOB]` restores an archive into a scratch Compose project on the VM
(internal network, own volumes, no ports, random secrets), checks it and removes it. Checks: rows
per table equal to the dump, migration of the backup and of the image, policy service and engine
healthy, the policy service reads tenants and one tenant's keys as the application role (grants
and row-level security), the engine loads the restored bundles with no load errors. Output: names,
counts, durations. The archive stays on the VM. Run it monthly and after every change to the
backup or the schema; keep its output as the drill log below.

## Measurements

Local copy of the stack (Docker 29.1.3, Compose 2.40.3, images of `umai-beta`, an isolated Docker
engine on a 22-core laptop), store filled with synthetic records:

| Step | Store 328 MB (300 672 decision events, 300 672 usage records, 6 tenants, 15 keys) |
|---|---|
| Backup: dump and archive (`make_archive`) | 8 s, archive 36 MB |
| Restore test, total | 83 s (PASSED) |
| of which: unpack and count the dump's rows (test only) | 21 s |
| of which: fresh Postgres and loading the dump | 21 s |
| of which: policy service and engine to healthy | 19 s |
| Old-format backup (no GRANTs): restore with repair | 87 s (PASSED); without the repair the policy service exits |
| Real restore, README §4c (fresh Postgres, load, volumes, start) | 47 s without service |
| `umai.sh up --recreate` (stop everything, start everything) | 30–32 s without service |

Report: [`evals/reports/2026-10-31-vm-zero-downtime`](../../evals/reports/2026-10-31-vm-zero-downtime/summary.md).
A B2s has two burstable vCPUs; the Stage 0 load estimate (2026-10-31) assumes a laptop core is
1.5–2.5× a B2s vCPU, and fewer cores serialise what ran in parallel here. The objectives below allow a factor of 3 until the drill on `umai-beta` gives the real one.

## Objectives

| | Target | Derivation |
|---|---|---|
| **RPO** | **24 h** | One backup a day. Worst case: the VM is lost just before the next run, 24 h + up to 15 min after the last one. The dump costs seconds, so a shorter RPO is a timer change, not an engineering task: the product owner's call. |
| **RTO, VM intact** (operator error, a bad migration, a damaged store) | **1 h** | Technical time 47 s locally (README §4c rehearsed), ≤ 3 min on a B2s, plus the restore test before it (83 s). The rest is the operator: notice, decide, SSH, a read SAS, verify (README §5). Working hours: no on-call before S0-11. |
| **RTO, VM lost** | **4 h** | New VM from Bicep and cloud-init, `ship.sh` (two images, about 0.8 GB, over the operator's uplink), first-time setup (README §3), then the restore above. Not measured end to end; the restore part is. Assumes the secrets survive (Key Vault, S0-8, or a copy the operator keeps); otherwise the recovery is partial (next section). |
| **Planned downtime** | ≈ 30 s locally per `up --recreate` | Only for a network, volume, Postgres or Caddy change (ADR-0052); image deploys have none. |

## VM lost in local-file mode: what cannot come back

`secrets.env` is generated on the VM and is in no backup. If the disk is lost:

- **Bundle signing key and its public key** (`config.env`): restored bundles carry signatures the
  new deployment does not know; they verify only with the old public key in
  `UMAI_BUNDLE_PUBLIC_KEYS`, else they must be re-signed. Key snapshots are re-signed by the
  policy service from the store.
- **Tenant HMAC master key**: hashes in decision events after the restore differ from those before.
- **Webhook master key**: stored endpoint URLs cannot be decrypted and signing secrets change;
  organizations add their endpoints again (ADR-0048).
- **Session secret, console assertion key, database passwords**: regenerated; everyone signs in again.
- **OIDC client secret, backup SAS, provider keys**: fetched again from Terraform, Azure, OpenAI.

API keys survive (stored as SHA-256 in the restored store). Closing this needs Key Vault (S0-8) or
a decision to keep an encrypted copy of `secrets.env` off the VM; neither is in place.

## Drill log

| Date | Where | Backup | Store | Result | Duration |
|---|---|---|---|---|---|
| 2026-10-31 | local copy of the stack (ADR-0052 validation) | taken on the spot | 328 MB, synthetic | PASSED | 83 s |
| 2026-10-31 | local copy | old format (no GRANTs, no `a/`) | 328 MB, synthetic | PASSED with grant repair | 87 s |
| — | `umai-beta` | latest nightly blob | beta store | pending: needs the operator's go-ahead | — |
