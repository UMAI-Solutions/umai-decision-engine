# Azure environments: umai-beta, umai-prod, monitoring (runbook)

Two instances of the single-VM template (`main.bicep`, ADR-0032) run in resource group `UMAI`,
West Europe, plus shared monitoring (`monitoring.bicep`, ADR-0051). Use this page for anything
about the environments; everything inside one VM (first-time setup, secrets, backup, restore, the
gateway, L1b) is in [`deploy/vm/README.md`](../vm/README.md). Incidents:
[`docs/security/incident-response.md`](../../docs/security/incident-response.md).

| | **umai-beta** | **umai-prod** (ADR-0050) |
|---|---|---|
| Purpose | Demos, pilots with synthetic data, experiments, every merge the same day | Stage 0 design partners under a beta agreement; nothing else |
| Parameters | `main.bicepparam` | `prod.bicepparam` |
| Resources | `umaibeta-*`, storage `umaibetae46trud7sdv2e` | `umaiprod-*`, storage `umaiprode46trud7sdv2e` |
| Hostname | `umai-beta.westeurope.cloudapp.azure.com` | the company subdomain (CNAME, §2); until then `umai-prod.westeurope.cloudapp.azure.com` |
| VM | `Standard_B2s` (2 vCPU, 4 GiB) | `Standard_B2ms` (2 vCPU, 8 GiB), a parameter (ADR-0050 §3) |
| SSH key | `~/.ssh/umai_beta_ed25519` | `~/.ssh/umai_prod_ed25519`, operators only |
| Auth0 | development tenant (`dev-zqxuqnql6vid3wcf.us.auth0.com`) | its own EU tenant, sign-ups off (`umai-control-center/identity/auth0/production.md`) |
| L1b key | beta OpenAI project | its own OpenAI project, own spending cap |
| Releases | any merged `main` commit | only commits that ran ≥ 24 h on beta, in a change window (§3) |
| Backups | nightly, 30 days | nightly + before every deploy, 35 days, 14-day soft delete |
| Alerts | severity 3, every 15 min | severity 1–2, every 5 min (§5) |
| Status page | not shown | `status.<company domain>` (§6) |

The resource group also holds `umai-single` and other resources that belong to neither
environment: never touch them. The account is Contributor on the resource group only (no role
assignments, no Key Vault), which is why both VMs keep secrets in `/opt/umai/secrets.env`.

## 1. Create umai-prod

Every Azure change needs the product owner's OK first; run `what-if` and read it before `create`.

```bash
az login
ssh-keygen -t ed25519 -f ~/.ssh/umai_prod_ed25519 -N "" -C "umai-prod operator"
ip="$(az network nsg rule show -g UMAI --nsg-name umaibeta-nsg -n ssh-operator --query sourceAddressPrefix -o tsv)"
az deployment group what-if -g UMAI -f deploy/azure/main.bicep -p deploy/azure/prod.bicepparam \
  -p adminSshPublicKey="$(cat ~/.ssh/umai_prod_ed25519.pub)" adminSourceCidr="$ip"
az deployment group create -g UMAI -n umai-prod -f deploy/azure/main.bicep -p deploy/azure/prod.bicepparam \
  -p adminSshPublicKey="$(cat ~/.ssh/umai_prod_ed25519.pub)" adminSourceCidr="$ip"
```

The what-if of 2026-10-31 is in §8: nine resources to create, nothing on beta or `umai-single`
changes. Quota: the B-series family has 10 vCPUs in West Europe, 2 in use by beta; B2ms takes 2
more. Never pass `main.bicepparam` with `-n umai-prod` or the reverse: the prefix decides which VM
a deployment touches.

## 2. First-time setup and hostname

Follow [`deploy/vm/README.md`](../vm/README.md) §2–§3 with the prod key
(`SSH_KEY=~/.ssh/umai_prod_ed25519`) and these values in `/opt/umai/config.env`:

| Key | umai-prod |
|---|---|
| `UMAI_HOSTNAME` | `umai-prod.westeurope.cloudapp.azure.com`, later the company subdomain |
| `UMAI_BACKUP_ACCOUNT` | `umaiprode46trud7sdv2e` (Bicep output `backupStorageAccount`) |
| `UMAI_REGION` | `eu` (stamped into the API keys minted here, ADR-0023) |
| `UMAI_OIDC_ISSUER`, `UMAI_OIDC_CLIENT_ID` | from the prod Auth0 tenant (`umai-control-center/identity/auth0/production.md` §4) |
| `UMAI_CONSOLE_BASIC_AUTH` | `false`: sign-in through Auth0, whose sign-ups are off |
| `UMAI_PLANS` | `true` (quotas; partners get their plan by hand, §4) |
| `UMAI_GATEWAY` | `true` if a partner uses the gateway (`UMAI_GATEWAY_PROVIDER_KEYS` `{}` for pass-through) |
| `UMAI_BILLING` | unset (no paid plans in Stage 0) |

Then:

- **Backup SAS:** from the workstation, as in `deploy/vm/README.md` §3, with `SA=umaiprode46trud7sdv2e`.
- **L1b:** the prod OpenAI project key through `umai.sh set-secret UMAI_OPENAI_API_KEY`
  (`deploy/vm/README.md` §7a).
- **Secrets copy:** after `umai.sh init` and the first `umai.sh up`, put a copy of
  `/opt/umai/secrets.env` into the team's password manager (entry "umai-prod secrets.env"). A
  restore onto a new VM needs the same signing key, HMAC master key and webhook master key
  (incident runbook §5.4). Never copy it to beta or to a repository.
- **Checks:** `deploy/vm/README.md` §5 against the prod hostname, then one backup by hand
  (`umai.sh backup`).

**Company hostname.** Create a CNAME at the domain's DNS provider:
`<subdomain>` → `umai-prod.westeurope.cloudapp.azure.com`. Then, in this order:

1. Set `UMAI_HOSTNAME=<subdomain>` and run `umai.sh up`; Caddy fetches the certificate on the
   first request.
2. Change `console_url` and `brand_base_url` in the prod tfvars and apply
   (`production.md` §6).
3. Set `hostname` of the prod entry in `monitoring.bicepparam` and redeploy monitoring (§5).
4. Update the status-page monitors (§6).

The Azure DNS label stays as the CNAME target.

## 3. Releases: beta first, then prod

1. Merge to `main`. Build from a clean detached worktree of `origin/main` in both repositories
   (`ship.sh` labels images with `git describe --dirty`):

   ```bash
   git fetch origin && git worktree add --detach ../rel-engine origin/main
   git -C ../umai-control-center fetch origin && git -C ../umai-control-center worktree add --detach ../rel-console origin/main
   SSH_KEY=~/.ssh/umai_beta_ed25519 ../rel-engine/deploy/vm/ship.sh umaiops@umai-beta.westeurope.cloudapp.azure.com ../rel-console
   ```

2. On beta: `deploy/vm/README.md` §5, a look at the beta alerts, at least 24 hours including one
   nightly backup. A fix found here restarts the clock with a new commit.
3. Prod, in a change window (Tuesday or Thursday, 04:00–06:00 UTC; ADR-0050 §5):
   1. Downtime announced on the status page 48 h before, if expected (migrations, network
      changes).
   2. Alerts of `umai-prod` muted for the window (§5, "change windows").
   3. On the prod VM: `sudo /opt/umai/src/umai-decision-engine/deploy/vm/umai.sh backup`.
   4. From the **same** two worktrees:
      `SSH_KEY=~/.ssh/umai_prod_ed25519 ../rel-engine/deploy/vm/ship.sh umaiops@<prod hostname> ../rel-console`.
      The build hits the layer cache, so the image ids printed by
      `docker image inspect -f '{{.Id}}' umai-decision-engine:current umai-control-center:current`
      on the workstation match beta's (`sudo docker image inspect …` on the VM). A different
      id means the commit or a base image changed: stop.
   5. `deploy/vm/README.md` §5 against the prod hostname; status page green.
   6. Tag the release: `git -C ../rel-engine tag prod-$(date -u +%Y%m%d)-1 && git -C ../rel-engine push origin --tags`;
      note the console commit in the tag message.
4. Release notes to the partners only when behaviour they see changes.

**Emergency fix:** the same path with at least one hour on beta, outside a window with the
incident commander's approval. **Rollback:** incident runbook §5.

## 4. Design-partner organizations

Partners are onboarded by hand (launch plan S0 exit):

1. **Beta agreement signed** (S0-13), with the partner's named contacts for incidents and breach
   notices (incident runbook §7).
2. **The first person:** in the prod Auth0 tenant, User Management → Users → Create user
   (`Username-Password-Authentication`, their business address), then **Send reset password**
   (`production.md` §5).
3. They sign in at `https://<prod hostname>/` and create their organization; it gets the
   starter guardrail signed with prod's key (ADR-0033). They create an application and a key on
   Get started.
4. **Plan:** Community allows 10 000 requests a month. For a pilot, give the organization
   Enterprise (metered, not refused) as operator, inside the policy container:

   ```bash
   sudo docker exec -i umai-policy-1 python - <<'EOF'
   import os, httpx
   headers = {"Authorization": f"Bearer {os.environ['UMAI_DE_CONTROL_API_TOKEN']}"}
   print(httpx.put("http://127.0.0.1:9200/v1/tenants/<tenant id>/plan", headers=headers,
                   json={"edition": "enterprise"}).json())
   EOF
   ```

5. **Colleagues:** the owner invites them in the console; the operator creates each invited
   address in Auth0 first (sign-ups are off), then they accept.
6. **Record the onboarding:** partner, tenant id, plan, contacts. Keep it in the company's
   customer records, not in this repository.

Organizations on beta stay there. Nothing is migrated from beta to prod: partners start fresh.

## 5. Monitoring (ADR-0051)

`monitoring.bicep` deploys one Log Analytics workspace (`umaiops-law`), an Application Insights
component for the availability tests, an e-mail action group and one data collection rule.
Per environment (`monitoring/environment.bicep`) it adds:

- the Azure Monitor Agent;
- the health reporter (`monitoring/health-report.sh`, a systemd timer installed by a managed run
  command);
- a `/readyz` availability test with a TLS-certificate check;
- these rules, each with the incident severity it maps to (prod / beta):

| Rule | Fires when | prod / beta |
|---|---|---|
| `availability` | `/readyz` fails from 2 of 3 Azure locations, or the certificate has < 14 days left | SEV1 / 3 |
| `vm-unavailable` | Azure's VM availability metric drops | SEV1 / 3 |
| `vm-silent` | no agent heartbeat for 10 min | SEV1 / 3 |
| `containers` | a container restarted, was not running or unhealthy in 3 of 15 reports, or no report came | SEV2 / 3 |
| `http-5xx` | > 5 % of ≥ 20 requests, or ≥ 10 requests, answered 5xx by Caddy in 15 min | SEV2 / 3 |
| `latency` | p95 of decision `total_ms` > 1 500 ms (≥ 20 decisions in 15 min) | SEV2 / 3 |
| `l1b-timeouts` | ≥ 5 enforcing or ≥ 30 L1b timeouts in 15 min | SEV2 / 3 |
| `disk-memory` | root disk > 85 % used, or available memory < 10 % | SEV2 / 3 |
| `cpu-credits` | B-series CPU credits < 30 | SEV2 / 3 |
| `backup` | `umai-backup.service` failed, or no "backup: uploaded" in 26 h | SEV2 / 3 |
| `quota-429` | any organization refused with `quota_exceeded` in the last hour | SEV3 / 3 |

Thresholds are the `thresholds` parameter of `monitoring.bicep`. The queries match log lines by
text; `tests/unit/test_monitoring_alerts.py` fails when one of those lines changes.

**Deploy** (after both VMs exist; with only beta, deploy with the prod entry removed from the
parameter file and add it later):

```bash
az deployment group what-if -g UMAI -n umai-monitoring -f deploy/azure/monitoring.bicep \
  -p deploy/azure/monitoring.bicepparam -p alertEmail=<team address>
az deployment group create  -g UMAI -n umai-monitoring -f deploy/azure/monitoring.bicep \
  -p deploy/azure/monitoring.bicepparam -p alertEmail=<team address>
```

Azure sends a confirmation e-mail to the action group's address. The run command installs the
reporter as root once per deployment; the VMs need no restart.

**Check within 15 minutes** (Log Analytics workspace `umaiops-law` → Logs):

```kusto
union withsource=T Heartbeat, Perf, Syslog, UmaiContainerLog_CL
| where TimeGenerated > ago(15m) | summarize rows = count() by T, Computer
```

- **No `Heartbeat`:** the agent did not start. Look at the extension's status on the VM and at
  `/var/opt/microsoft/azuremonitoragent/log/` on it.
- **No `Syslog` rows with `ProcessName == "umai-health"`:** run
  `systemctl status umai-health-report.timer` and `journalctl -t umai-health -n 5`.
- **No `UmaiContainerLog_CL`:** the agent cannot read Docker's files. Check with
  `sudo -u azuremonitoragent ls /var/lib/docker/containers/*/` and
  `getfacl /var/lib/docker/containers`. The reporter sets these ACLs every minute (ADR-0051,
  unverified until this check). Without this table the 5xx, latency, L1b and quota rules see
  nothing.

**Alert drill** (S0-11 evidence; on beta, announced to the team):
`sudo docker compose -p umai stop engine`. Expect the `containers` and `availability` e-mails
within 15–20 minutes and the external check red. Then `umai.sh up`, the resolved e-mails, and an
entry in the incident runbook §9.

**Change windows:** mute instead of disabling rules. Azure portal → Monitor → Alerts → Alert
processing rules → Create:

- scope: resource group `UMAI`;
- filter: alert rule name contains `umai-prod`;
- rule settings: suppress notifications;
- scheduling: the window in UTC.

Delete the rule afterwards (or `az monitor alert-processing-rule create` / `delete`). Alerts
still fire and show in the portal.

**Privacy:** what reaches the workspace is listed in ADR-0051 §2. Caddy lines arrive as
`access status=… duration_s=… method=… path=…`, without client addresses. Postgres
`DETAIL`/`STATEMENT` lines are dropped. Logs are kept 30 days.

## 6. Status page and external checks (ADR-0051 §4)

Recommended: Better Stack, one Responder seat. The account owner creates the account and
accepts the vendor's terms and DPA, and the vendor goes on the sub-processor list (S0-13).
Configure:

| Monitor (outside Azure) | URL | Expect | Interval | Notes |
|---|---|---|---|---|
| prod API | `https://<prod hostname>/readyz` | 200 | 1 min | SSL expiry warning 14 days; regions EU + US |
| prod console | `https://<prod hostname>/auth/signed-out` | 200 | 1 min | the console renders without a session |
| prod gateway | `https://<prod hostname>/v1/models` | 401 without a key | 3 min | only if the gateway is on; a 502 means the gateway is down |
| beta API (optional) | `https://umai-beta.westeurope.cloudapp.azure.com/readyz` | 200 | 5 min | e-mail only, not on the page |

Set up the page:

- **Hostname:** `status.<company domain>`, a CNAME to the vendor.
- **Languages:** English and Turkish.
- **Components:** "API" (prod API monitor), "AI gateway", "Console".
- **Subscribers:** e-mail subscriptions on.
- **Alerts:** the team address, and the on-call person's phone app for the prod monitors.

Incident posts follow the templates in the incident runbook §6.1. Never put beta on the page.

## 7. Cost per month (list price, West Europe, USD, 2026-10-31)

| Item | Amount | Source |
|---|---|---|
| umai-prod VM `Standard_B2ms` | 70.08 | Retail Prices API, $0.096/h |
| OS disk 64 GB Standard SSD, static IP, backup storage | ≈ 9.50 | ADR-0032 ($4.80 + $3.65 + ≈ $1) |
| **umai-prod stack** | **≈ 80** | |
| Availability tests: prod every 5 min, beta every 15 min, 3 locations | 22.60 | 35 040 runs × $0.000645 |
| Log search alerts: prod 5 × $1.50 + 3 × $0.50, beta 8 × $0.50 | 13.00 | per rule by frequency |
| Metric alerts (6 series), e-mails, agent, heartbeats | 0 | first 10 series and 1 000 e-mails free |
| Log Analytics ingestion, ≈ 0.6 GB (estimate) | 0–2 | 5 GB/month free per billing account, then $2.99/GB |
| **Monitoring** | **≈ 36–38** | |
| Status page and external checks (Better Stack, 1 seat) | 29–34 | annual / monthly billing |
| **New total, without beta** | **≈ 145–152** | beta itself stays ≈ $45 |

Not included: an Auth0 paid plan if MFA or e-mail templates need one (Essentials from $150, the
product owner's call, ADR-0053). Check the real ingestion after a week
(`Usage | where TimeGenerated > ago(7d) | summarize sum(Quantity) by DataType`).

**Cheaper variant:** prod tests every 10 min saves about $8.50. Moving beta's rules to hourly
saves nothing (15 min is already the lowest price).

## 8. What-if summaries (2026-10-31, no change made)

**`main.bicep` + `prod.bicepparam`:** Create 9 resources:

- `umaiprod-vm` (`Standard_B2ms`), `umaiprod-nic`, `umaiprod-nsg`, `umaiprod-pip`, `umaiprod-vnet`;
- storage account `umaiprode46trud7sdv2e` with blob service, container `backups` and the
  lifecycle policy.

Ignore 28 existing resources (`umaibeta-*`, `umai-single*`, Container Apps, Cognitive Services,
other workspaces). Modify 0, Delete 0. Run with the beta operator key as a placeholder for the
SSH key and beta's operator CIDR.

**`monitoring.bicep` + `monitoring.bicepparam`:** Create 35 resources:

- shared: workspace `umaiops-law` and table `UmaiContainerLog_CL`, `umaiops-appi`, action group
  `umaiops-ag`, data collection rule `umaiops-dcr-vm`;
- per VM: `AzureMonitorLinuxAgent`, the DCR association `umai-host`, run command
  `umai-health-report`;
- per environment: web test `umai-<env>-readyz`, 3 metric alerts and 8 log search alerts.

Ignore 28, Modify 0, Delete 0. Run with a placeholder alert address. The prod VM did not exist
yet: its three VM resources fail at `create` until it does.
