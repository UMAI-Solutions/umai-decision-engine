# ADR-0051 — Monitoring, alerting and a public status page for the single-VM environments

- **Status:** Proposed
- **Date:** 2026-10-31
- **Deciders:** product owner (status-page vendor, alert address); engineering
- **Builds on:** ADR-0032 (single VM), ADR-0050 (`umai-prod`), ADR-0005 (no raw content), ADR-0031 §5 and §8 (Front Door only for content-free surfaces; Log Analytics in the region)
- **Answers, in part:** launch-plan S0 item 9 and PRR row S0-11 (monitoring, alerting, status page)
- **Facts:** [`reference/azure-monitoring.md`](../reference/azure-monitoring.md), [`reference/status-pages-and-uptime.md`](../reference/status-pages-and-uptime.md) (both verified 2026-10-31)

## Context

`umai-beta` has no monitoring beyond Docker's health checks and `docker logs` (ADR-0032
"Consequences"). Before design partners use `umai-prod`, the team must learn about an outage before
the partner does. Stage 0 asks for:

- container health, the rate of 5xx responses, latency, L1b timeouts and quota refusals;
- disk, memory, backup success and certificate expiry;
- availability checks from outside Azure's network, e-mail alerts and a public status page.

Constraints:

- **Account.** Contributor on resource group `UMAI` only: no role assignments, no Key Vault.
- **Product.** Logs and telemetry carry no content (ADR-0005). The engine writes plain-text logs:
  no metrics endpoint and no OpenTelemetry yet.
- **Deploy files.** `deploy/vm/` (Compose, Caddyfile, `umai.sh`) is being changed for zero-downtime
  deploys elsewhere, so monitoring must not need changes there.

Azure Monitor fits the account. The Azure Monitor Agent needs a managed identity and a link to a
data collection rule, but no role assignment. Application Insights availability tests run from
Azure datacenters, so they cannot see an outage of Azure or of the path into it. Checks from
outside need a third party.

## Decision

1. **Azure Monitor, in Bicep.** [`deploy/azure/monitoring.bicep`](../../deploy/azure/monitoring.bicep)
   watches both VMs, one module per environment. It needs no change to `deploy/vm/`. It deploys:
   - one Log Analytics workspace `umaiops-law` in West Europe, kept 30 days, with a 1 GB daily cap;
   - the Azure Monitor Agent on each VM and one data collection rule with three sources:
     - memory and root-disk counters;
     - syslog (facility daemon), filtered to the lines that mention UMAI;
     - the containers' Docker log files, into the custom table `UmaiContainerLog_CL`;
   - a small root timer on each VM (`monitoring/health-report.sh`, installed by a managed run
     command). Every minute it writes each container's state, health and restart count to
     syslog. It also gives the agent read access to Docker's root-only log files (ACLs on
     `/var/lib/docker/containers` only);
   - per environment, one standard availability test of `https://<host>/readyz` from three Azure
     locations (Amsterdam, Dublin, Paris). It fails when the TLS certificate has fewer than 14
     days left;
   - per environment, alert rules. Each signal it watches has a rule:

     | Signal | Rule |
     |---|---|
     | Container health | `containers` (restart, not running or unhealthy in 3 of 15 reports, no container running, reporter silent; the gateway is ignored where it is switched off) |
     | 5xx rate | `http-5xx` (Caddy access lines: > 5 % of ≥ 20 requests, or ≥ 10, in 15 min) |
     | Latency | `latency` (p95 of the decision `total_ms` above 1 500 ms with ≥ 20 decisions) |
     | L1b timeouts | `l1b-timeouts` (≥ 5 enforcing or ≥ 30 in total in 15 min) |
     | Quota 429s | `quota-429` (any `quota_exceeded`, hourly) |
     | Disk and memory | `disk-memory` (root disk > 85 %, available memory < 10 %) and `cpu-credits` (B-series credits < 30) |
     | Backup success | `backup` (a failed `umai-backup.service`, or no "backup: uploaded" in 26 h) |
     | Certificate expiry | `availability` (the test above fails from 2 of 3 locations) |
     | VM and agent | `vm-unavailable` (platform metric) and `vm-silent` (no heartbeat for 10 min) |

   - **Severities and frequencies.** Production rules are Azure severity 1 (outage) or 2
     (degraded) and run every 5 minutes. Beta rules are severity 3 and run every 15 minutes.
     Quota refusals are severity 3 everywhere. They map to SEV1–SEV3 of the
     [incident runbook](../security/incident-response.md).
   - **Thresholds** are parameters of `monitoring.bicep`. A unit test pins the log lines the
     queries match (`tests/unit/test_monitoring_alerts.py`), so renaming one fails CI instead of
     muting an alert.
2. **What reaches the workspace.** Counters, syslog lines about UMAI units and the containers'
   log lines, which carry no content by ADR-0005. Two exceptions are handled at ingestion by the
   rule's transformation:
   - Caddy's access lines are cut to status, duration, method and path, so no client address
     is stored;
   - Postgres `DETAIL` and `STATEMENT` lines, which can quote a row's values, are dropped.

   The engine's `admission.refused` and `admission.throttled` lines keep client addresses: they
   are security records. Logs stay in West Europe for 30 days.
3. **Alerts go to e-mail** through one action group with the common alert schema: one team
   address, never a personal one alone. Production alerts also go to the status-page vendor's
   on-call notifications (point 4) once that account exists. Planned work in a change window
   (ADR-0050 §5) is muted with an alert processing rule for the window, not by turning rules
   off.
4. **External checks and the status page: a hosted service, outside Azure. Recommended: Better
   Stack** (one Responder seat, about $29–34 a month):
   - its probes run on Hetzner, Akamai and its own addresses, not on Azure;
   - one status page on `status.<company domain>` switches between English and Turkish;
   - the page's state follows the monitors, incidents are posted by hand, and subscribers get
     e-mail;
   - data is stored in the EU by default, with a DPA and SOC 2 Type 2.

   It checks prod's `/readyz` (with a certificate-expiry warning), the console's sign-in page and
   the gateway's `/v1/models` every minute. The page shows three components: API, AI gateway and
   console. It shows prod only, never beta. Before buying, confirm in writing which features the
   seat unlocks and in which language subscriber e-mails go out. The runner-up is UptimeRobot
   Team (an EU company whose terms allow commercial use; English and Turkish need two pages).
   The vendor becomes a sub-processor for subscriber e-mail addresses (S0-13).

## Alternatives considered

| Option | Why not |
|---|---|
| Status page as an Azure static site fed by the availability tests | Shares Azure's failure domain: when West Europe or the path into it fails, the page and its checks fail with it. Needs our own code (test results → JSON → page), subscriber e-mails, incident posts and two languages, all built by us. Static Web Apps Standard ($9) plus tests at about $28 per URL per month cost more than a hosted page. A storage static site has no HTTPS on a custom domain without Front Door (about $35 a month base). The free tier of Static Web Apps is for personal projects |
| Atlassian Statuspage | A page only ($29 Hobby), no checks of its own; Turkish only through a paid add-on |
| Instatus, OpenStatus, Hyperping | Instatus keeps data in the US. OpenStatus has no DPA we could find. Hyperping needs Pro ($74–89) for languages |
| Self-hosted Uptime Kuma or similar | Another server to run. On Azure it shares the failure domain; elsewhere it means another provider account and sub-processor anyway |
| Prometheus and Grafana on the VM | Same failure domain as what it watches, more to operate, and no outside view |
| A hosted log and metrics service (Grafana Cloud, Datadog, Better Stack Logs) | Another sub-processor for operational data; cost grows with volume; Log Analytics is in-region and already where ADR-0031 §8 puts logs |
| Instrument the code (OpenTelemetry, Application Insights SDK) | The right path for AKS (ADR-0030 telemetry allowlist), but a code change across three services. Log lines already carry the numbers |
| Docker's syslog or journald log driver instead of reading the json files | Needs a change to `docker-compose.yml` (deploy scripts, owned elsewhere) and loses `docker logs` on the VM |
| Availability tests from five locations, alerting at three (Microsoft's recommendation) | $28 a month per URL at 5 minutes. Three locations, alerting at two, cost $17. The external checks cover the outside view |

## Consequences

- **Cost** at list price per month: availability tests about $22.60 (prod every 5 minutes, beta
  every 15, three locations each); log alerts $13 (prod $9, beta $4); metric alerts $0 (six
  series, the first ten are free); Log Analytics about $0–2 (estimated 0.6 GB a month, inside the
  5 GB monthly free allowance if nothing else in the billing account uses it); the agent and
  e-mails $0. Monitoring adds about $36–38; the status page about $29–34. Itemised in
  [`deploy/azure/README.md`](../../deploy/azure/README.md) §7.
- **Unverified until the first deploy:** whether the agent reads Docker's log files with the ACLs
  the reporter sets, and whether the JSON-format log source fills the table as declared. If
  `UmaiContainerLog_CL` stays empty on beta, the 5xx, latency, L1b and quota rules see nothing:
  the runbook's first check after deploying finds it.
- `deploy/vm/` stays untouched. Changing a log message the rules read needs a matching change in
  `monitoring/environment.bicep` (the unit test says which).
- S0-11 still needs, besides this decision: the deployment (with the user's OK), the alert
  address, the status-page account, an on-call schedule and one alert drill.

## Validation

- `az deployment group what-if` (2026-10-31): 35 resources to create, nothing changed or deleted
  ([`deploy/azure/README.md`](../../deploy/azure/README.md) §8).
- All eight log-alert queries and both ingestion transformations were run against synthetic rows
  in the engine's real log formats on a Log Analytics query endpoint, 2026-10-31. That was 27
  alert cases, each firing when it should and staying quiet when it should, plus the
  transformation output, which keeps no client address.
- After deploying: rows in `Heartbeat`, `Perf`, `Syslog` and `UmaiContainerLog_CL` within 15
  minutes; an alert drill on beta (stop the engine container, the e-mail arrives, the external
  check turns red, restore). These are the S0-11 evidence.
- Revisit when the move to ADR-0031 starts (Container insights, OpenTelemetry), when a
  threshold fires too often or too late (incident reviews), or when a cost item exceeds its
  estimate by more than half.
