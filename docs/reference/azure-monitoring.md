# Azure Monitor for the single-VM stack: agent, log collection, alerts, availability tests, prices

**Last verified:** 2026-10-31 · **Used by:** monitoring of the single-VM deployment (ADR-0032, `deploy/azure/`) ·
Labels: see [README](README.md). Facts are **vendor claims** from Microsoft Learn, azure.microsoft.com and the
Azure Retail Prices API (`prices.azure.com`, USD, consumption, list price, `westeurope`) unless marked otherwise.
Statements marked **(ours)** are read-only `az` checks against our subscription; **(inference)** is our reading.

Scope: two Ubuntu 24.04 VMs (Docker Compose: Caddy, two FastAPI services, Next.js console, Postgres) in one West
Europe resource group; the deployer is **RG Contributor only** (no role assignments, no Key Vault); Bicep only.

## 0. Subscription state (ours, 2026-10-31)

| Check | Result |
|---|---|
| `az provider show -n <ns> --query registrationState -o tsv` for `Microsoft.Insights`, `Microsoft.OperationalInsights`, `Microsoft.AlertsManagement` | `Registered`, `Registered`, `Registered` |
| `az vm extension image list-versions --publisher Microsoft.Azure.Monitor --name AzureMonitorLinuxAgent -l westeurope` | newest `1.45.1` (also 1.45.0, 1.44.0, 1.43.0 …) |
| `az role definition list --name Contributor` | `actions: *`; `notActions`: `Microsoft.Authorization/*/Write`, `/*/Delete`, `elevateAccess`, Blueprint assignments, gallery share, Purview consents, deployment-stack deny settings, subscription cancel/enable |

So Contributor covers every Azure Monitor write below; the only thing it cannot do is create role assignments.

## 1. Azure Monitor Agent (AMA)

| Topic | Fact | Source |
|---|---|---|
| Extension | Publisher `Microsoft.Azure.Monitor`, type `AzureMonitorLinuxAgent` | [ama-req] |
| Identity | A managed identity must be enabled on the VM; system- and user-assigned both supported; system-assigned is "suited for initial testing and small deployments" | [ama-req] |
| Role for the VM's identity | **None documented.** Learn lists roles only for the person deploying (Virtual Machine Contributor to install; a role with `Microsoft.Resources/deployments/*` for templates; Monitoring Contributor for DCRs and associations). No page asks for a role on the workspace or DCR for AMA (the Logs Ingestion API is a different path). Authorization comes from the DCR association. Absence of a requirement is **not stated explicitly**: confirm with Heartbeat rows after the first deploy | [ama-req], [dcr-create] |
| Install as RG Contributor | Yes: Contributor is a superset of the listed roles (§0); enabling a system-assigned identity is a VM write, not a role assignment (inference) | [ama-req], §0 |
| Version | Linux **1.45.1** (rollout from 2026-09-26; 1.45.0 paused 2026-09-16 for wrong guest-metric values). Only versions released in the last year are supported. Ubuntu 24.04 supported since Linux 1.33.1 (Sept 2024), x64 and Arm64 | [ama-ver], [ama-os] |
| Bicep | Learn sample: `Microsoft.Compute/virtualMachines/extensions@2021-11-01`, `typeHandlerVersion: '1.21'`, `autoUpgradeMinorVersion: true`, `enableAutomaticUpgrade: true`. `typeHandlerVersion` is Major.Minor; `autoUpgradeMinorVersion` picks the newest minor **at deploy time only**; `enableAutomaticUpgrade` lets the platform upgrade later. Use `'1.45'` with both flags (template API latest `2026-04-01`) | [ama-arm], [ext-ref] |
| Cost | "There's no cost to use the Azure Monitor Agent"; you pay ingestion and retention | [ama-overview] |
| Runs as | `mdsd` runs as local system user `azuremonitoragent` (not root); also `azuremetricsext`, `azureotelcollector` | [ama-manage] |
| Disk | Packages 700 MB, cache 500 MB, event cache up to 10 GB in `/var/opt/microsoft/azuremonitoragent/events`, 1 GB `/var/lib/rsyslog` | [ama-req] |
| Outbound 443 | `global.handler.control.monitor.azure.com`, `westeurope.handler.control.monitor.azure.com`, `<workspace-id>.ods.opinsights.azure.com`, `global.prod.microsoftmetrics.com`; service tags `AzureMonitor` and `AzureResourceManager` | [ama-net] |
| DCE | **Not required.** A DCE is required only for Windows Firewall logs, Prometheus (Container insights) and Private Link (page updated 2026-10-20). Learn's older text/JSON samples still show `dataCollectionEndpointId`; it is optional | [dce] |
| DCR rules | DCR in the **same region as the workspace**; up to 10 data sources per DCR; any number of DCRs per VM. Template API for `dataCollectionRules` and `dataCollectionRuleAssociations`: `2024-03-11` (the provider also lists `2025-05-11`, required only for the preview multi-stage transformations) | [dc-vm], [dcr-create], [dcr-ref], [transform] |

## 2. Custom text and JSON logs (AMA)

Status: both Learn pages (updated March 2026) carry no preview label, and the DCE page no longer requires a
DCE; we treat them as GA (no dated GA announcement re-checked: **unverified**).

| Topic | Fact | Source |
|---|---|---|
| Table first | The `_CL` table must exist **before** the DCR. Bicep: `Microsoft.OperationalInsights/workspaces/tables` (template API latest `2026-03-01`), `properties.schema.{name, columns}`, `plan` (`Analytics` default, `Basic`, `Auxiliary`), `retentionInDays`, `totalRetentionInDays`. Needs `Microsoft.OperationalInsights/workspaces/*` (Contributor has it) | [log-text], [table] |
| Text columns | `TimeGenerated` datetime (required; ingestion time unless a transform sets it), `RawData` string (required unless a transform parses into other columns), `Computer` string (optional, auto-filled), `FilePath` string (optional, auto-filled) | [log-text] |
| JSON columns | `TimeGenerated` plus columns **named and typed like the top-level JSON fields**; `Computer`, `FilePath` optional. Each entry must be one JSON line: "nesting and the JSON body format isn't supported". The JSON page says to create the table only with its PowerShell script ("No other method works"); that script is a plain REST `PUT …/workspaces/{ws}/tables/{name}_CL?api-version=2021-12-01-preview`, the same operation Bicep issues (inference: test) | [log-json] |
| Column names | Start with a letter; letters, digits, `_`; 2–45 characters; reserved: `id`, `BilledSize`, `IsBillable`, `TenantId`, `Title`, `Type`, `UniqueId`, `_ItemId`, `_ResourceId` … | [table] |
| File patterns | Wildcards **only in the file name and in the first folder above it**; Learn's own example is `/var/logs/*/*`, so `/var/lib/docker/containers/*/*-json.log` has the documented shape. Several patterns allowed | [log-text] |
| File rules | Local disk; ASCII or UTF-8; append only; monitor ≤ 10 directories; clean up old files; don't rename a matching file to another matching name (duplicates); ≤ 50 MB/min when copying in big files | [log-text] |
| Record split (text) | `recordStartTimestampFormat` (required in the `text` settings) marks the start of a record; if no timestamp in that format is found, end of line is used. Values: `ISO 8601` (no fractional seconds), `yyyy-MM-ddTHH:mm:ssK`, `YYYY-MM-DD HH:MM:SS`, `M/D/YYYY HH:MM:SS AM/PM`, `Mon DD, YYYY HH:MM:SS`, `yyMMdd HH:mm:ss`, `ddMMyy HH:mm:ss`, `MMM d hh:mm:ss`, `dd/MMM/yyyy:HH:mm:ss zzz` | [log-text], [dcr-ref] |
| Transform | `dataFlows[].transformKql` (KQL on each record, e.g. `parse_json(RawData)`); `logFiles[].transformKql` is deprecated. Supported for "any custom table created for the Azure Monitor Agent". Analytics/Basic: free unless it **drops > 50 %** of incoming data (excess billed at Logs Processed, §8); Auxiliary: processing billed on all incoming data | [transform], [dcr-ref] |

Bicep shape (from Learn's samples, trimmed; `format` is `'text'` or `'json'`, `settings` only for text):

```bicep
properties: {
  streamDeclarations: {
    'Custom-DockerText': {
      columns: [
        { name: 'TimeGenerated', type: 'datetime' }
        { name: 'RawData', type: 'string' }
        { name: 'FilePath', type: 'string' }
        { name: 'Computer', type: 'string' }
      ]
    }
  }
  dataSources: {
    logFiles: [
      {
        name: 'dockerJsonFiles'
        streams: [ 'Custom-DockerText' ]
        filePatterns: [ '/var/lib/docker/containers/*/*-json.log' ]
        format: 'text'
        settings: { text: { recordStartTimestampFormat: 'ISO 8601' } }
      }
    ]
  }
  destinations: { logAnalytics: [ { name: 'ws', workspaceResourceId: workspace.id } ] }
  dataFlows: [
    {
      streams: [ 'Custom-DockerText' ]
      destinations: [ 'ws' ]
      outputStream: 'Custom-DockerLogs_CL' // table must have the projected columns
      transformKql: 'source | extend d = parse_json(RawData) | project TimeGenerated = todatetime(d["time"]), Stream = tostring(d["stream"]), Message = tostring(d["log"]), FilePath, Computer'
    }
  ]
}
```

Docker specifics: json-file lines are `{"log":…,"stream":…,"time":…}` objects, one per line, one container per
file; `max-size` defaults to unlimited and `max-file` to 1 ([docker-json]). Using `format: 'text'` plus
`parse_json` avoids the strict field-to-column mapping of `format: 'json'` (inference). Caddy's JSON access log
arrives as a string inside `log` and needs a second `parse_json` (inference).

**Open risk (unverified):** AMA runs as `azuremonitoragent`, while Docker keeps `/var/lib/docker/containers`
root-only; other agents hit "permission denied" on exactly these files (secondary source: [dd-issue]). Whether
AMA's file reader can open them is not documented. Test on the VM before relying on it; the fallback is Docker's
`syslog` log driver (configurable `syslog-facility`, `tag`; default tag = first 12 characters of the container ID,
[docker-syslog]) into the Syslog path of §3.

## 3. Syslog and systemd units

| Topic | Fact | Source |
|---|---|---|
| Collection path | AMA reads syslog **only through rsyslog or syslog-ng**: it drops `/etc/rsyslog.d/10-azuremonitoragent-omfwd.conf` forwarding `*.*` to TCP `127.0.0.1:28330` and restarts rsyslog. No direct journald input is documented | [syslog] |
| DCR | `dataSources.syslog: [{ name, streams: ['Microsoft-Syslog'], facilityNames: ['daemon'], logLevels: ['Info','Notice','Warning','Error','Critical','Alert','Emergency'] }]`, output to the `Syslog` table (no DCE, no custom table) | [dcr-samples], [dcr-ref] |
| Syslog table | Columns `Computer`, `EventTime`, `Facility`, `SeverityLevel`, `ProcessName`, `ProcessID`, `SyslogMessage`, `HostName`; **Basic plan not supported** (Auxiliary yes); semicolons are removed at ingestion | [syslog-table], [syslog] |
| systemd defaults | stdout goes to the journal (`DefaultStandardOutput=journal`); `SyslogFacility=` defaults to **`daemon`**, `SyslogLevel=` to **`info`**, `SyslogIdentifier=` to the process name | [systemd-exec] |
| journald → rsyslog | Upstream default forwards only to wall. Ubuntu reportedly ships a journald drop-in (named as `/usr/lib/systemd/journald.conf.d/syslog.conf` in a Stack Exchange thread) enabling `ForwardToSyslog`, read by rsyslog's `imuxsock` (**secondary source**). Check on the VM with `systemd-analyze cat-config systemd/journald.conf` and that rsyslog is installed | [journald], [ubuntu-fwd] |

So `backup: uploaded <name>` from a systemd service should land in `Syslog` with `Facility == "daemon"`,
`SeverityLevel == "info"`, `ProcessName` = the identifier (inference; set `SyslogIdentifier=` in the unit to fix it).

## 4. Memory and disk

**Guest counters (AMA, legacy Linux model, stream `Microsoft-Perf` → `Perf` table).** Linux specifiers as listed
by Learn: `Memory(*)\% Available Memory`, `Memory(*)\Available MBytes Memory`, `Memory(*)\% Used Memory`,
`Logical Disk(*)\% Used Space`, `Logical Disk(*)\% Free Space`, `Logical Disk(*)\Free Megabytes`,
`Processor(*)\% Processor Time`, `System(*)\Load1` (Bicep string: `'Logical Disk(*)\\% Used Space'`). Default
sampling 60 s (`samplingFrequencyInSeconds`). Don't put Windows-style counters in the same DCR (duplicates).
`Microsoft-InsightsMetrics` → Azure Monitor Metrics is labelled **(preview)**. `Perf` column values (e.g.
`InstanceName` = mount point) are **unverified**. Sources: [perf-ref], [perf], [dcr-samples].

**Platform metrics (no agent)** for `Microsoft.Compute/virtualMachines`: `Available Memory Bytes` and
`Available Memory Percentage` (PT1M, Average), `Percentage CPU`, `VmAvailabilityMetric`, `CPU Credits Remaining`
(B-series), OS-disk IOPS/latency. None carries a preview label (the page marks others "(Preview)"), so they read
as GA. **There is no platform metric for file-system used space**: disk % needs the Perf counter. Source: [vm-metrics].

## 5. Availability tests (Application Insights standard tests)

| Topic | Fact | Source |
|---|---|---|
| Prerequisite | Workspace-based Application Insights component (`Microsoft.Insights/components@2020-02-02`, linked to the workspace) | [avail] |
| Bicep | `Microsoft.Insights/webtests@2022-06-15`, `kind: 'standard'`, tag `'hidden-link:${component.id}': 'Resource'`; `properties`: `SyntheticMonitorId`, `Name`, `Kind: 'standard'`, `Enabled`, `Frequency` (s, default 300), `Timeout` (s, default 30), `RetryEnabled`, `Locations: [{ Id }]`, `Request: { RequestUrl, HttpVerb, Headers: [{ key, value }], FollowRedirects, ParseDependentRequests, RequestBody (base64) }`, `ValidationRules: { ExpectedHttpStatusCode, IgnoreHttpStatusCode, ContentValidation: { ContentMatch, IgnoreCase, PassIfTextFound }, SSLCheck, SSLCertRemainingLifetimeCheck }` | [webtest-ref] |
| TLS expiry | `SSLCheck: true` plus `SSLCertRemainingLifetimeCheck: <days>` (positive, needs `SSLCheck`) fails the test that many days before expiry; only the **final redirected URL** is checked | [webtest-ref], [avail] |
| EU locations | West Europe `emea-nl-ams-azr`, North Europe `emea-gb-db3-azr`, France Central `emea-fr-pra-edge`, France South `emea-ch-zrh-edge`, UK South `emea-ru-msa-edge`, UK West `emea-se-sto-edge` (display names as Learn prints them; physical sites of the `-edge` ids **unverified**) | [avail] |
| Recommendations | **At least five locations** (up to 16); alert threshold = locations − 2 (so 3 of 5); enable retries (a failure counts only after 3 successive attempts; "about 80% of failures disappear on retry"); ≤ 100 tests per component; edits take up to 20 min to reach all agents | [avail] |
| Network | All locations are listed under provider "Azure"; requests come from the service tag `ApplicationInsightsAvailability` (shared IPs; Learn suggests a custom header `X-Customer-InstanceId`). The URL must be public. The test therefore shows reachability from Azure's address space, not from consumer networks (inference) | [avail] |
| Ping tests | Classic URL ping tests: retirement **extended from 2026-09-30 to 2028-09-30**, when existing ones are removed | [avail] |
| Alert | `Microsoft.Insights/metricAlerts`, `location: 'global'`, `scopes: [webtest.id, component.id]`, `criteria: { 'odata.type': 'Microsoft.Azure.Monitor.WebtestLocationAvailabilityCriteria', webTestId, componentId, failedLocationCount }`, Learn sample `evaluationFrequency: 'PT1M'`, `windowSize: 'PT5M'`, `failedLocationCount: 2`. Alerting on an `availabilityResults/…` metric name instead is **unverified** here | [alert-metric-arm], [metricalert-ref] |

## 6. Alert rules and action groups

Permissions for any alert rule: read on the target, write on the resource group of the rule, read on the action
group, so **RG Contributor suffices** for all three types below [alert-arm].

| Resource | API version for Bicep | Location | Notes |
|---|---|---|---|
| `Microsoft.Insights/scheduledQueryRules` | **`2026-03-01`** (newest GA; `2023-12-01` before it) | region (`westeurope`) | `scopes: [workspace.id]` is supported (alerts then fire on the workspace). `kind`: `LogAlert` (default), `SimpleLogAlert`, `LogToMetric`. `evaluationFrequency` 1 min–24 h; 1-min rules reject queries with `search`, `union`, `take`, `ingestion_time()` or tables without data; query time range capped at 2 days. **Leave `identity` unset**: then the rule runs with the permissions of whoever last edited it; a managed identity would need a Reader role assignment on the workspace, which we can't create. Auto-resolve: 1-min rules after 10 min healthy, 5–15-min rules after three periods [log-alert], [sqr-ref] |
| `Microsoft.Insights/metricAlerts` | **`2026-01-01`** (new GA; `2018-03-01` before) | `'global'` | `criteria.'odata.type'`: `SingleResourceMultipleMetricCriteria`, `MultipleResourceMultipleMetricCriteria`, `WebtestLocationAvailabilityCriteria`, `PromQLCriteria`. Learn recommends the rule in the target's resource group [metricalert-ref], [alert-arm] |
| `Microsoft.Insights/actionGroups` | **`2023-01-01`** (newest GA; `2023-09-01-preview`, `2024-10-01-preview` in the reference; provider also lists `2025-09-01-preview`, `2026-03-01-preview`) | `'global'` (provider locations: Global plus a few regions, **not** West Europe) | `groupShortName` ≤ 12 chars (required), `enabled`, `emailReceivers: [{ name, emailAddress, useCommonAlertSchema }]` [ag-ref] |

Table plans and alerts: Analytics tables support all alerts, Basic tables only Simple Log Alerts, Auxiliary
tables none [plans].

## 7. Heartbeat

AMA writes a `Heartbeat` record **every minute** while it reaches Azure Monitor [dc-vm], [heartbeat]; Learn's
verify query filters `Category == "Azure Monitor Agent"` [ama-manage] (the table reference still lists older
`Category` values). `Heartbeat` is **free of ingestion charges** [cost-logs]. A "no heartbeat for 10 min" log alert
therefore works; query a longer window than the gap so a silent VM still appears, for example
`Heartbeat | where TimeGenerated > ago(6h) | summarize last = max(TimeGenerated) by Computer | where last < ago(10m)`
at a 5-min frequency (inference). `VmAvailabilityMetric` is the agentless complement (VM down vs agent down).

## 8. Prices (West Europe, USD, pay-as-you-go, Retail Prices API, read 2026-10-31)

Filters (`https://prices.azure.com/api/retail/prices?currencyCode=USD&$filter=…`):
`serviceName eq 'Log Analytics' and armRegionName eq 'westeurope'`,
`serviceName eq 'Azure Monitor' and armRegionName eq 'westeurope'`,
`serviceName eq 'Application Insights' and armRegionName eq 'westeurope'`,
`contains(meterName, 'Web Test') and armRegionName eq 'westeurope'`.

| Item (meter) | Price | Notes |
|---|---|---|
| Analytics Logs ingestion (`Analytics Logs Data Ingestion`) | **$0 for 0–5 GB, then $2.99/GB** | "first 5 GB/month per billing account" free [pricing]; 31 days retention included [cost-logs] |
| Analytics retention beyond 31 days (`Analytics Logs Data Retention`) | $0.13/GB/month | long-term retention (`Data Archive`) $0.026/GB/month |
| Basic Logs ingestion | $0.65/GB | 30 days included; queries $0.0065/GB scanned; Simple Log Alerts only |
| Auxiliary Logs ingestion | $0.07/GB | plus `Logs Processed` $0.13/GB on all incoming data; no alerts |
| Logs Processed (transformations) | $0.13/GB | Analytics/Basic only when a transform drops > 50 % |
| Free tables | $0 | `Heartbeat`, `AzureActivity`, `Usage`, `Operation` [cost-logs] |
| Standard availability test (`Standard Web Test Execution`) | **$0.000645 per execution** | ping tests free until removal; multi-step $10/test/month |
| Metric alert (`Alerts Metric Monitored`) | first 10 time series free, then **$0.10 per time series/month** | dynamic threshold +$0.10 |
| Log search alert, per rule (`Alerts System Log Monitored at …`) | **1 min $3.00 · 5 min $1.50 · 10 min $1.00 · 15 min $0.50** per month | first time series included [pricing] |
| Log search alert, extra time series (`Alerts Resource Monitored at …`) | 1 min $0.30 · 5 min $0.15 · 10 min $0.10 · 15 min $0.05 | mapping of these two meter families to "rule" vs "extra time series" is **inference** from [pricing]; simple log alerts are billed with the 1-min meter (exact rate **unverified**) |
| E-mail notifications (`Emails`) | first 1,000/month free, then $0.00002 each ($2 per 100,000) | |
| Azure Monitor Agent | $0 | [ama-overview] |

Worked example: one standard test from 5 locations every 5 min = 5 × 12 × 730 = 43,800 runs/month ≈ **$28.25**;
from 3 locations ≈ $16.95; 5 locations every 15 min ≈ $9.42.

## What this means for us

1. **Everything fits RG Contributor**: AMA extension, system-assigned identity, DCRs and associations, `_CL`
   tables, App Insights, web tests, alerts and action groups. No role assignment is needed if log alerts run
   without a managed identity and AMA's identity needs none (the latter: confirm by Heartbeat).
2. **No DCE, no Key Vault.** Workspace, DCR, log alerts in `westeurope`; metric alerts, action groups `global`.
3. **Docker logs are the risky part**: the file pattern is supported, but read access for `azuremonitoragent` to
   root-only Docker directories must be tested on the VM first; the syslog-driver route via rsyslog is the fallback.
4. **Backup success and app markers** (`L1B_TIMEOUT`, `quota_exceeded`) become log alerts over `Syslog` or the
   custom table; keep those tables on Analytics (Basic allows only Simple Log Alerts, Auxiliary none).
5. **Disk needs Perf counters, memory doesn't**: `Available Memory Percentage` is an agentless platform metric;
   `Logical Disk(*)\% Used Space` needs AMA.
6. **Cost is driven by ingestion and web tests**, not alert rules: keep ingestion under 5 GB/month with
   transforms (watch the 50 % rule), and size test locations × frequency (≈ $28 per URL at 5 × 5 min).

## Sources (accessed 2026-10-31)

[ama-req]: https://learn.microsoft.com/en-us/azure/azure-monitor/agents/azure-monitor-agent-requirements
[ama-ver]: https://learn.microsoft.com/en-us/azure/azure-monitor/agents/azure-monitor-agent-extension-versions
[ama-os]: https://learn.microsoft.com/en-us/azure/azure-monitor/agents/azure-monitor-agent-supported-operating-systems
[ama-arm]: https://learn.microsoft.com/en-us/azure/azure-monitor/agents/resource-manager-agent
[ext-ref]: https://learn.microsoft.com/en-us/azure/templates/microsoft.compute/virtualmachines/extensions
[ama-overview]: https://learn.microsoft.com/en-us/azure/azure-monitor/agents/azure-monitor-agent-overview
[ama-manage]: https://learn.microsoft.com/en-us/azure/azure-monitor/agents/azure-monitor-agent-manage
[ama-net]: https://learn.microsoft.com/en-us/azure/azure-monitor/agents/azure-monitor-agent-network-configuration
[dce]: https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-endpoint-overview
[dc-vm]: https://learn.microsoft.com/en-us/azure/azure-monitor/vm/data-collection
[dcr-create]: https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-rule-create-edit
[dcr-ref]: https://learn.microsoft.com/en-us/azure/templates/microsoft.insights/datacollectionrules
[dcr-samples]: https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-rule-samples
[log-text]: https://learn.microsoft.com/en-us/azure/azure-monitor/vm/data-collection-log-text
[log-json]: https://learn.microsoft.com/en-us/azure/azure-monitor/vm/data-collection-log-json
[table]: https://learn.microsoft.com/en-us/azure/azure-monitor/logs/create-custom-table
[transform]: https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-transformations
[docker-json]: https://docs.docker.com/engine/logging/drivers/json-file/
[docker-syslog]: https://docs.docker.com/engine/logging/drivers/syslog/
[dd-issue]: https://github.com/DataDog/datadog-agent/issues/11473
[syslog]: https://learn.microsoft.com/en-us/azure/azure-monitor/vm/data-collection-syslog
[syslog-table]: https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/syslog
[systemd-exec]: https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html
[journald]: https://www.freedesktop.org/software/systemd/man/latest/journald.conf.html
[ubuntu-fwd]: https://oneuptime.com/blog/post/2026-03-02-forward-journald-logs-syslog-ubuntu/view
[perf]: https://learn.microsoft.com/en-us/azure/azure-monitor/vm/data-collection-performance
[perf-ref]: https://learn.microsoft.com/en-us/azure/azure-monitor/vm/metrics-guest-reference
[vm-metrics]: https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-metrics/microsoft-compute-virtualmachines-metrics
[avail]: https://learn.microsoft.com/en-us/azure/azure-monitor/app/availability
[webtest-ref]: https://learn.microsoft.com/en-us/azure/templates/microsoft.insights/webtests
[alert-metric-arm]: https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/resource-manager-alerts-metric
[metricalert-ref]: https://learn.microsoft.com/en-us/azure/templates/microsoft.insights/metricalerts
[alert-arm]: https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-create-rule-cli-powershell-arm
[log-alert]: https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-create-log-alert-rule
[sqr-ref]: https://learn.microsoft.com/en-us/azure/templates/microsoft.insights/scheduledqueryrules
[ag-ref]: https://learn.microsoft.com/en-us/azure/templates/microsoft.insights/actiongroups
[plans]: https://learn.microsoft.com/en-us/azure/azure-monitor/logs/data-platform-logs
[heartbeat]: https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/heartbeat
[cost-logs]: https://learn.microsoft.com/en-us/azure/azure-monitor/logs/cost-logs
[pricing]: https://azure.microsoft.com/en-us/pricing/details/monitor/
[meters]: https://learn.microsoft.com/en-us/azure/azure-monitor/fundamentals/cost-meters

Prices: `prices.azure.com/api/retail/prices` with the §8 filters (meters per [meters]); API versions also via `az provider show` (ours).
