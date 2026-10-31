# ADR-0050 — A production environment for Stage 0 on one VM (`umai-prod`)

- **Status:** Proposed
- **Date:** 2026-10-31
- **Deciders:** product owner; engineering
- **Builds on:** ADR-0032 (single-VM template), ADR-0024 §4 (Auth0), ADR-0033 (organizations), ADR-0043 (plans)
- **Deviates from, for Stage 0:** ADR-0031 §2, §4–§6 and the Postgres row (the regional stack); launch-plan S0 items 6, 8 and 9, PRR rows S0-8, S0-9 and S0-14 as written
- **Related:** ADR-0051 (monitoring, alerting, status page); runbook [`deploy/azure/README.md`](../../deploy/azure/README.md); incident response [`security/incident-response.md`](../security/incident-response.md)

## Context

On 2026-10-31 the product owner started Stage 0 of the launch plan: a private beta for two or
three design partners under a beta agreement. The only running stack is `umai-beta` (ADR-0032),
and it is where the team experiments. Every merged change lands there the same day. Guardrail
versions, L1b enforcement, quotas and the gateway are switched by hand. Migrations run on every
deploy. On 2026-10-31 a network change took it down for several minutes. Design partners bring
real users and real content, so they should not share a box with that work.

The target platform of ADR-0031 (AKS, Application Gateway WAF_v2, PostgreSQL Flexible Server,
Key Vault Premium, Managed Redis, a DR region) costs about $2,100 a month at list price. Its
smallest variant costs about $800, half of it the WAF (ADR-0032). The deploying account also
cannot build it today. It is Contributor on resource group `UMAI` only, so it can create no new
resource group and no role assignment, and `Microsoft.KeyVault` is not registered. Workload
identity, AKS pulling from ACR, a customer-managed key on Flexible Server and Key Vault itself
each need one of those.

The software needs far less than that stack. One VM runs engine, policy service, gateway,
console and Postgres (with row-level security) under Docker Compose. Quotas are counted per
process (ADR-0043, single node), which fits one VM.

## Decision

1. **`umai-prod` is a second instance of the single-VM template** in resource group `UMAI`, West
   Europe: `deploy/azure/main.bicep` with [`deploy/azure/prod.bicepparam`](../../deploy/azure/prod.bicepparam)
   (prefix `umaiprod`). It has its own VM, OS disk, NIC, public IP, NSG, VNet and backup storage
   account. It runs the same images, Compose files and `umai.sh` as beta. Nothing is shared with
   `umai-beta` except the resource group, the subscription and the monitoring workspace of
   ADR-0051. In particular:
   - **Secrets:** generated on the prod VM by `umai.sh init` / `up`, never copied from beta. That
     covers the bundle signing key, operator tokens, tenant HMAC master key, webhook master key,
     session secret and the console's assertion key. Local-file mode (`useKeyVault=false`)
     applies until the subscription owner registers Key Vault and grants role assignments; then
     both VMs switch to Key Vault mode.
   - **Operator access:** a separate SSH key (`~/.ssh/umai_prod_ed25519`). The NSG admits SSH only
     from the operator's address.
   - **Sign-in:** its own Auth0 tenant in the EU region, tagged Production, with sign-ups off
     (`umai-control-center/identity/auth0`, workspace `prod`, `production.md`). Creating it needs
     a person, and the hosting question Q35 is the product owner's.
   - **L1b:** its own OpenAI project key, so its spend is capped and revoked apart from beta.
   - **Backups:** its own storage account. Archives are kept 35 days (beta 30). Blob soft delete
     keeps an overwritten or deleted archive recoverable for 14 days, because the VM's SAS can
     write.
2. **Hostname: a subdomain of the company domain**, a CNAME to the Azure DNS label
   `umai-prod.westeurope.cloudapp.azure.com`. The label is the hostname until the CNAME exists.
   Caddy obtains the certificate for whatever `UMAI_HOSTNAME` names.
3. **VM size is a parameter. The recommendation is `Standard_B2ms`** (2 vCPU, 8 GiB, $70.08 a
   month at list price, West Europe). The containers' memory limits add up to about 4 GiB
   (engine, policy service and gateway 900 MiB each, Postgres and console 512 MiB, Caddy 256 MiB),
   which is all of a B2s. B2ms doubles the memory and raises the burst baseline from 40 % to 60 %.
   The subscription has quota only in the B-series family: 10 vCPUs, 2 in use, no Basv2, no
   Dasv5. The S0-3 load test decides whether CPU credits last. If they do not, the next steps are
   B4ms or a quota request for a non-burstable family.
4. **Every release goes to beta first, then to prod.** Each is shipped from the same `main` commit
   in a clean detached worktree (`ship.sh` with the beta key and host, later with the prod key and
   host). Prod gets that commit only after:
   - 24 hours on beta, including one nightly backup;
   - the post-deploy checks of `deploy/vm/README.md` §5 passing on beta;
   - a backup on prod right before the deploy, inside a change window.

   Prod never runs a commit beta has not run. An emergency fix still passes beta, for at least an
   hour. Each prod release is tagged `prod-YYYYMMDD-<n>` in Git, so a rollback knows its target.
5. **Prod rules that beta does not have:**
   - No experiments: no unmerged code and no hand-edited configuration files. Guardrails change
     only through the console or the policy service. No evals and no load tests against prod.
     The only synthetic traffic is the availability checks.
   - Change windows: Tuesday and Thursday, 07:00–09:00 Europe/Istanbul (04:00–06:00 UTC).
     Expected downtime is announced on the status page 48 hours ahead. Emergency changes may
     happen at any time with the incident commander's approval
     ([incident response](../security/incident-response.md)).
   - Backups: nightly as on beta, plus one before every deploy. A restore test before S0 exit,
     then monthly.
   - Data stays in prod. No prod backup or record goes to beta or to a workstation, except a
     restore drill whose copy is deleted afterwards.
   - Alerts: prod alerts are severity 1–2 and wake the on-call person. Beta alerts are
     severity 3 (ADR-0051).
6. **Design partners are onboarded by hand**, which matches the S0 exit "design partners
   onboarded by hand". For each partner:
   - The operator creates the partner's first person in the prod Auth0 tenant and sends a
     password reset.
   - That person signs in and creates the organization, which gets the starter guardrail
     (ADR-0033). They invite colleagues; the operator creates each colleague in Auth0 first.
   - The operator sets the organization's plan with `PUT /v1/tenants/{id}/plan` (ADR-0043), so
     Community's 10 000 requests a month do not stop a pilot.
7. **What prod does not have, disclosed in the beta agreement (S0-13):** no WAF, no high
   availability, no zone redundancy, no DR region, keys and secrets on the VM disk (root only,
   encrypted at rest by Azure), one Postgres container with nightly logical dumps (recovery
   point 24 hours, recovery time a few hours), no SLA. These are beta service levels.

**Exit to the ADR-0031 stack.** Any one of these triggers the move:
- the first paying customer (Stage 1);
- a fourth design partner;
- any customer asking for an SLA, a recovery point under 24 hours or a recovery time under four
  hours;
- the S0-3 load test or production metrics showing one VM cannot carry the partners' volume
  within budget (CPU credits exhausted, or p95 above the evaluation budget at their rate);
- a security review requiring WAF, private endpoints or HSM-held keys.

The subscription owner granting role assignments and registering Key Vault is a precondition of
that move, not a trigger. When it happens, both VMs move to Key Vault mode first. Images, Compose
files, the Postgres schema and backups carry over: the database moves by dump and restore, and
the IaC grows from this template's parameters.

## Alternatives considered

| Option | Why not |
|---|---|
| Design partners on `umai-beta` | Partner users and content on the box that takes experiments and same-day deploys. Real people in the development Auth0 tenant (Q42). One bad deploy reaches customers |
| The ADR-0031 stack now | About $800–2,100 a month. It needs role assignments and Key Vault, which the account does not have. Weeks of IaC before the first partner |
| A resource group per environment | A cleaner boundary for rights and cost, but the account cannot create one. Name prefixes and the `umai-environment` tag separate them instead |
| A standby VM in North Europe | Doubles the cost for a recovery time no partner has asked for yet. Recovery is "new VM from Bicep, restore the last backup" |
| Promote images instead of rebuilding (beta's exact image to prod) | Better, but `ship.sh` always builds. Builds from the same clean commit hit the layer cache and give the same image id; the runbook checks that id. A `ship.sh` option to skip the build is a follow-up for the deploy scripts |

## Consequences

- Cost at list price: the VM ($70.08), a 64 GB Standard SSD (≈ $4.80), a static IP (≈ $3.65)
  and backup storage (≈ $1) come to about $80 a month. Monitoring for both VMs comes on top
  (ADR-0051).
- Two VMs to patch and ship. Operators pick the environment explicitly every time: an SSH key
  per environment, and the host named in every `ship.sh` call.
- Rows S0-8 (Key Vault, P-256 signing), S0-9 (WAF, private endpoints) and S0-14 (Flexible
  Server PITR and geo-backup) cannot close on this stack as written. Stage 0 exits only if the
  product owner records them as accepted deviations for the design-partner beta, with the
  triggers above. Otherwise the ADR-0031 move becomes part of Stage 0. Engineering's
  recommendation is to accept the deviations and make the ADR-0031 stack the entry condition of
  Stage 1.
- The prod Auth0 tenant and the hostname need the product owner: Q35 (hosting), the plan (MFA,
  e-mail provider) and the company subdomain.

## Validation

- `az deployment group what-if` of `prod.bicepparam` (2026-10-31): nine resources to create
  (VM, NIC, NSG, public IP, VNet, storage account with blob service, `backups` container and
  lifecycle policy). It changes nothing on `umaibeta-*` or `umai-single`. Summary in
  [`deploy/azure/README.md`](../../deploy/azure/README.md) §1.
- After creation: the checks of `deploy/vm/README.md` §5 on the prod hostname, a restore of the
  first prod backup into a scratch database, and one alert drill (S0-11).
- Revisit when the S0-3 load test reports. Also revisit when an exit trigger above fires, or
  when the account gets Key Vault and role-assignment rights.
