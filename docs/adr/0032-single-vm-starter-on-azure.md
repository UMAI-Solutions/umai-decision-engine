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
   policy service, console. Size `Standard_B2als_v2` (2 vCPU, 4 GiB) with a 64 GB Standard SSD in
   an EU region (West Europe by default). Infrastructure as code in `deploy/azure/main.bicep`.
   List price ≈ $41/month pay-as-you-go (VM $31.5, disk $4.8, IP $3.7, Key Vault and backups ≈ $1);
   about $30 with a one-year reservation (discount not verified).
2. **Same images as the regional stack.** One engine image serves the API and the policy service
   (`Dockerfile`); the console has its own (`umai-control-center/Dockerfile`, Next.js standalone).
   Moving to AKS later changes the orchestration, not the artifacts.
3. **Only what the code uses is deployed.** No Postgres, Redis or Keycloak until the code that
   needs them lands (AGENTS rule 6). The VM has headroom for them (≈ 3.7 GiB free).
4. **Ingress:** Caddy terminates TLS (Let's Encrypt, TLS 1.2+, HSTS), caps request bodies at
   512 KB (ADR-0019 §4, closes readiness gap T2 at the ingress), returns 404 for `/docs`, `/redoc`,
   `/openapi.json`, and logs without query strings or headers. One hostname: `/v1/*`, `/healthz`,
   `/readyz` go to the engine (bearer token, ADR-0016); everything else is the console behind
   **HTTP basic auth** until console login (ADR-0024) exists. The policy service is not exposed.
5. **Secrets:** Key Vault Standard, read by the VM's managed identity at start into `/run/umai`
   (tmpfs, mode 0400); nothing secret in Bicep parameters, compose files or on disk. Key Vault and
   the backup storage account accept only the VM subnet (service endpoints) and the operator's IP.
6. **Signing stays Ed25519 with a file key** fetched from Key Vault, because the Key Vault signer
   (`kms_azure`, ECDSA P-256, ADR-0031 §2) is not implemented. On first start the seed bundles in
   the image are copied to the policy volume and signed with this deployment's key; the engine
   requires signed bundles as in any non-development environment.
7. **Backups:** nightly tar of the policy volume and a consistent SQLite copy to Blob Storage
   (managed identity, no shared keys), kept 30 days by a lifecycle rule.
8. **Scope limit.** The starter VM is for demos and **at most a few pilot tenants under a pilot
   agreement**. It is not the S0 private beta of the launch plan: it has no WAF, no HA, no DR, a
   single shared API token (readiness T1 open: the body `tenant_id` is not bound to the
   credential), software-protected keys and manual operations.

## Alternatives considered

| Option | Why not |
|---|---|
| ADR-0031 stack at minimum size (≈ $800/month) | 25× the budget; WAF alone ≈ $360 |
| Azure Container Apps, scale to zero | Cheaper only if every service sleeps; cold starts on the evaluation path, and Postgres/Keycloak (when they come) must stay on |
| App Service (Linux) | One plan per app or a shared B-tier plan costs more than the VM for three apps and gives no compose-style shared volume for the policy directory |
| Azure Front Door for TLS instead of Caddy | Outside the EU Data Boundary (ADR-0031 §4) and a fixed monthly fee |
| Deploy Postgres, Redis and Keycloak now | Nothing uses them; idle RAM and patching for no function |

## Consequences

- One VM is a single point of failure; recovery is "new VM from Bicep, restore the last backup",
  measured in hours. Acceptable for demos and pilots, not for paid SaaS.
- The operator patches the OS (unattended upgrades with a 03:30 reboot window) and watches the
  stack; there is no monitoring beyond container health checks and `docker logs`.
- Pilot tenants each need their own HMAC key in Key Vault (`umai-tenant-hmac-keys`).
- Exit to ADR-0031: when the first paying customer, the second pilot, or any S0 readiness row
  requires WAF, HA or tenant-bound keys. Images, Key Vault secrets and backups carry over.

## Validation

- Local run of the full compose stack with synthetic secrets (2026-10-17): engine and policy
  service healthy; `/v1/evaluate` 401 without token and BLOCK with token on a synthetic TCKN; console
  401 without basic auth, 200 with it; console → engine and console → policy relays 200;
  `/docs` and `/openapi.json` 404; 700 kB body 413; HSTS, `nosniff`, `Cache-Control: no-store`
  on API responses; publishing v5 through the policy service signed it and the engine loaded it
  within one poll interval. Memory: engine 101 MiB, policy 97 MiB, console 26 MiB, Caddy 17 MiB.
- On Azure: the runbook's checks (`deploy/vm/README.md` §5) after the first deployment.
