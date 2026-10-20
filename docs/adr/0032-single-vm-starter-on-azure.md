# ADR-0032 — Single-VM starter deployment on Azure before the regional stack

- **Status:** Accepted (product owner, 2026-10-17: "start on one VM in Azure")
- **Date:** 2026-10-17
- **Deciders:** product owner; engineering
- **Builds on:** ADR-0016 (console calls the engine server-side), ADR-0022 (policy service), ADR-0031 (UMAI Cloud on Azure)
- **Deviates from, temporarily:** ADR-0031 §2 (signing in Key Vault Premium), §4 (Application Gateway WAF), §6 (AKS), Postgres/Redis rows; launch-plan S0 items 7–9
- **Facts:** prices from the Azure Retail Prices API, West Europe, 2026-10-17; [`reference/azure-platform.md`](../reference/azure-platform.md)

## Context

The regional stack of ADR-0031 (AKS, Application Gateway WAF_v2, zone-redundant Postgres, Managed
Redis, DR region) costs about $2,100 a month at list price; even its minimal variant is about
$800, half of it the WAF's fixed fee. The product owner needs the same software reachable in Azure
for about $30 a month now, for demos and first pilots. The software as it stands needs far less
than that stack: the engine runs Presidio with the `no_op` NLP engine (no model in memory), the
policy service stores drafts in SQLite and versions as files, and nothing reads Postgres, Redis or
an identity service yet (ADR-0024, 0025 and 0028 are not implemented). Measured locally, the four
containers use about 240 MiB together.

## Decision

1. **One Azure VM runs the whole product with Docker Compose** (`deploy/vm/`): Caddy, engine,
   policy service, console. Size 2 vCPU / 4 GiB with a 64 GB Standard SSD in an EU region (West
   Europe by default): `Standard_B2als_v2` ($31.5/month) where the subscription has Basv2 quota,
   otherwise `Standard_B2s` ($35/month; `umai-beta` runs this one). Infrastructure as code in
   `deploy/azure/main.bicep`. List price ≈ $41–45/month pay-as-you-go (VM, disk $4.8, IP $3.7,
   backups ≈ $1); less with a one-year reservation (discount not verified).
2. **Same images as the regional stack.** One engine image serves the API and the policy service
   (`Dockerfile`); the console has its own (`umai-control-center/Dockerfile`, Next.js standalone).
   Moving to AKS later changes the orchestration, not the artifacts. Images are built on the
   operator workstation and streamed to the VM over SSH (`deploy/vm/ship.sh`): the VM holds no
   repository credentials (the GitHub organisation also disables deploy keys).
3. **Only what the code uses is deployed.** No Postgres or Redis until the code that needs them
   lands (AGENTS rule 6). The VM has headroom for them (≈ 3.7 GiB free). Console login will use
   Auth0 (ADR-0024 §4), so no identity service runs on the VM.
4. **Ingress:** Caddy terminates TLS (Let's Encrypt, TLS 1.2+, HSTS), caps request bodies at
   512 KB (ADR-0019 §4, closes readiness gap T2 at the ingress), returns 404 for `/docs`, `/redoc`,
   `/openapi.json`, and logs without query strings or headers. One hostname: `/v1/*`, `/healthz`,
   `/readyz` go to the engine (bearer token, ADR-0016); everything else is the console behind
   **HTTP basic auth** until console login (ADR-0024) exists. The policy service is not exposed.
5. **Secrets:** Key Vault Standard, read by the VM's managed identity at start into `/run/umai`
   (tmpfs, mode 0400); nothing secret in Bicep parameters or compose files. Key Vault and the
   backup storage account accept only the VM subnet (service endpoints) and the operator's IP.
   **Fallback, in use for `umai-beta`:** where the deployer cannot register `Microsoft.KeyVault`
   or create role assignments (resource-group Contributor only), `useKeyVault=false` keeps the
   secrets in `/opt/umai/secrets.env` (root, 0600), generated on the VM by `umai.sh init`, and
   backups use a write-only container SAS (`cw`, one year) stored in the same file. Containers see
   the same `/run/umai` files in both modes. Switch to Key Vault once the subscription owner
   registers the provider and grants role-assignment rights on the resource group.
6. **Signing stays Ed25519 with a file key** fetched from Key Vault, because the Key Vault signer
   (`kms_azure`, ECDSA P-256, ADR-0031 §2) is not implemented. On first start the seed bundles in
   the image are copied to the policy volume and signed with this deployment's key; the engine
   requires signed bundles as in any non-development environment.
7. **Backups:** nightly tar of the policy volume and a consistent SQLite copy to Blob Storage
   (managed identity without shared keys, or the SAS of §5), kept 30 days by a lifecycle rule.
8. **Scope limit.** The starter VM is for demos and **at most a few pilot tenants under a pilot
   agreement**. It is not the S0 private beta of the launch plan: it has no WAF, no HA, no DR, a
   single shared API token (readiness T1 open: the body `tenant_id` is not bound to the
   credential), software-protected keys and manual operations.

## Alternatives considered

| Option | Why not |
|---|---|
| ADR-0031 stack at minimum size (≈ $800/month) | 25× the budget; WAF alone ≈ $360 |
| Azure Container Apps, scale to zero | Cheaper only if every service sleeps; cold starts on the evaluation path, and Postgres (when it comes) must stay on |
| App Service (Linux) | One plan per app or a shared B-tier plan costs more than the VM for three apps and gives no compose-style shared volume for the policy directory |
| Azure Front Door for TLS instead of Caddy | Outside the EU Data Boundary (ADR-0031 §4) and a fixed monthly fee |
| Deploy Postgres and Redis now | Nothing uses them; idle RAM and patching for no function |

## Consequences

- One VM is a single point of failure; recovery is "new VM from Bicep, restore the last backup",
  measured in hours. Acceptable for demos and pilots, not for paid SaaS.
- The operator patches the OS (unattended upgrades with a 03:30 reboot window) and watches the
  stack; there is no monitoring beyond container health checks and `docker logs`.
- Pilot tenants each need their own HMAC key (`umai-tenant-hmac-keys` / `UMAI_TENANT_HMAC_KEYS`).
  Since organizations are created at runtime (ADR-0033), see the amendment below.
- The operator's SSH access and the storage firewall are pinned to one IP; a changing home IP
  means updating the NSG rule (runbook §4).
- Exit to ADR-0031: when the first paying customer, the second pilot, or any S0 readiness row
  requires WAF, HA or tenant-bound keys. Images, Key Vault secrets and backups carry over.

## Validation

- Local run of the full compose stack with synthetic secrets (2026-10-17): engine and policy
  service healthy; `/v1/evaluate` 401 without token and BLOCK with token on a synthetic TCKN; console
  401 without basic auth, 200 with it; console → engine and console → policy relays 200;
  `/docs` and `/openapi.json` 404; 700 kB body 413; HSTS, `nosniff`, `Cache-Control: no-store`
  on API responses; publishing v5 through the policy service signed it and the engine loaded it
  within one poll interval. Memory: engine 101 MiB, policy 97 MiB, console 26 MiB, Caddy 17 MiB.
- On Azure (`umai-beta`, resource group `UMAI`, 2026-10-17): Let's Encrypt certificate issued;
  `/healthz` 200, `/v1/bundles` 401 without token, `/docs` and `/openapi.json` 404, console 401
  without and 200 with basic auth, console → engine and console → policy relays 200; `/v1/evaluate`
  with the token BLOCKs a synthetic TCKN (L0); bundles signed with the deployment key
  `vm-2026-10`; `umai.sh backup` uploaded an archive to Blob Storage.

## Amendment 2026-10-19: HMAC keys for organizations created at runtime

**Context.** Self-service organizations (ADR-0033) are tenants that appear at runtime. Outside
development the engine refuses a tenant without an HMAC key (`TENANT_KEY_MISSING`), and this
starter keeps keys in one environment map, so every new organization would need an operator
edit and a restart before its first evaluation. ADR-0027 §5 (per-tenant keys generated at
onboarding, wrapped by Key Vault, delivered in the key snapshot) needs Key Vault, which this
account cannot register yet.

**Decision.** On single-VM and self-hosted installs, a tenant without an explicit key gets one
derived from a master secret: HKDF-SHA256 over `UMAI_DE_TENANT_HMAC_MASTER_KEY` (≥ 32 bytes),
`info = "umai-tenant-hmac/v1:" + tenant id`, 32 bytes. Explicit keys in
`UMAI_DE_TENANT_HMAC_KEYS` win, so existing tenants keep their hashes. `umai.sh` generates the
master secret once (`umai-tenant-hmac-master-key`).

**Alternatives.** Generate and store a key per tenant in the store now (an unwrapped key in
Postgres and in the snapshot files, and a contract change, for an interim); operator edits per
organization (breaks self-service onboarding).

**Consequences.** One secret now protects every derived tenant key: no worse than the single
map it complements, but per-tenant rotation needs an explicit key for that tenant. The exit is
unchanged: UMAI Cloud moves to ADR-0027 §5, and derived tenants get generated keys there (hash
continuity is not kept across that move, by design of §5).

