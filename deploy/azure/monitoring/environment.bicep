// One watched environment (ADR-0051): the Azure Monitor Agent and the container health reporter
// on its VM, an availability test with TLS-certificate check, and the alert rules. Production rules
// are severity 1-2 and run every 5 minutes; beta rules are severity 3 and run every 15 minutes.
// Called by ../monitoring.bicep; runbook deploy/azure/README.md §5.

param location string
@description('name, vmName, hostname, production, availabilityTestFrequency, gateway (see ../monitoring.bicep).')
param env object
param workspaceId string
param dataCollectionRuleId string
param appInsightsId string
param actionGroupId string
param thresholds object

var prod = bool(env.production)
var computer = string(env.vmName)
var namePrefix = 'umai-${env.name}'
var logFrequency = prod ? 'PT5M' : 'PT15M'
var sevOutage = prod ? 1 : 3
var sevDegraded = prod ? 2 : 3
var tags = { 'umai-environment': env.name }

// Test locations: West Europe (Amsterdam), North Europe (Dublin), France Central (Paris). All are
// Azure datacenters; the checks from outside Azure are the status page's (ADR-0051).
var testLocations = [{ Id: 'emea-nl-ams-azr' }, { Id: 'emea-gb-db3-azr' }, { Id: 'emea-fr-pra-edge' }]

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' existing = {
  name: computer
}

resource agent 'Microsoft.Compute/virtualMachines/extensions@2024-07-01' = {
  parent: vm
  name: 'AzureMonitorLinuxAgent'
  location: location
  properties: {
    publisher: 'Microsoft.Azure.Monitor'
    type: 'AzureMonitorLinuxAgent'
    typeHandlerVersion: '1.45'
    autoUpgradeMinorVersion: true
    enableAutomaticUpgrade: true
  }
}

resource association 'Microsoft.Insights/dataCollectionRuleAssociations@2023-03-11' = {
  name: 'umai-host'
  scope: vm
  properties: {
    dataCollectionRuleId: dataCollectionRuleId
    description: 'Memory, disk, umai syslog lines and container logs to the UMAI workspace.'
  }
}

// Installs a systemd timer that writes container state to syslog every minute and lets the agent
// read Docker's log files (health-report.sh). Runs once per deployment of this module.
resource healthReporter 'Microsoft.Compute/virtualMachines/runCommands@2024-07-01' = {
  parent: vm
  name: 'umai-health-report'
  location: location
  dependsOn: [agent]
  properties: {
    source: { script: loadTextContent('health-report.sh') }
    asyncExecution: false
    timeoutInSeconds: 300
    treatFailureAsDeploymentFailure: true
  }
}

resource readyTest 'Microsoft.Insights/webtests@2022-06-15' = {
  name: '${namePrefix}-readyz'
  location: location
  tags: union(tags, { 'hidden-link:${appInsightsId}': 'Resource' })
  kind: 'standard'
  properties: {
    SyntheticMonitorId: '${namePrefix}-readyz'
    Name: '${namePrefix} /readyz and TLS certificate'
    Description: 'Engine readiness through Caddy; fails when the certificate has fewer than ${thresholds.certificateDays} days left.'
    Enabled: true
    Frequency: int(env.availabilityTestFrequency)
    Timeout: 30
    Kind: 'standard'
    RetryEnabled: true
    Locations: testLocations
    Request: {
      RequestUrl: 'https://${env.hostname}/readyz'
      HttpVerb: 'GET'
      ParseDependentRequests: false
      FollowRedirects: false
    }
    ValidationRules: {
      ExpectedHttpStatusCode: 200
      IgnoreHttpStatusCode: false
      SSLCheck: true
      SSLCertRemainingLifetimeCheck: int(thresholds.certificateDays)
    }
  }
}

resource availabilityAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-availability'
  location: 'global'
  tags: tags
  properties: {
    description: '${env.hostname}/readyz failed from 2 of 3 Azure locations, or the TLS certificate expires within ${thresholds.certificateDays} days.'
    severity: sevOutage
    enabled: true
    scopes: [readyTest.id, appInsightsId]
    evaluationFrequency: 'PT1M'
    windowSize: prod ? 'PT5M' : 'PT15M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.WebtestLocationAvailabilityCriteria'
      webTestId: readyTest.id
      componentId: appInsightsId
      failedLocationCount: 2
    }
    autoMitigate: true
    actions: [{ actionGroupId: actionGroupId }]
  }
}

// Platform metrics, no agent needed: the VM itself, and the burst credits of a B-series size.
resource vmAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-vm-unavailable'
  location: 'global'
  tags: tags
  properties: {
    description: 'Azure reports ${computer} unavailable.'
    severity: sevOutage
    enabled: true
    scopes: [vm.id]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'available'
          metricNamespace: 'Microsoft.Compute/virtualMachines'
          metricName: 'VmAvailabilityMetric'
          operator: 'LessThan'
          threshold: 1
          timeAggregation: 'Average'
        }
      ]
    }
    autoMitigate: true
    actions: [{ actionGroupId: actionGroupId }]
  }
}

resource cpuCreditsAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-cpu-credits'
  location: 'global'
  tags: tags
  properties: {
    description: '${computer} is about to run out of CPU credits and fall back to its baseline (ADR-0050 §3).'
    severity: sevDegraded
    enabled: true
    scopes: [vm.id]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT30M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'credits'
          metricNamespace: 'Microsoft.Compute/virtualMachines'
          metricName: 'CPU Credits Remaining'
          operator: 'LessThan'
          threshold: thresholds.cpuCreditsMin
          timeAggregation: 'Average'
        }
      ]
    }
    autoMitigate: true
    actions: [{ actionGroupId: actionGroupId }]
  }
}

// Log search alerts. Each query returns rows only when something is wrong; one row or more fires.
// {computer} and {threshold.*} are filled in below.
var queries = [
  {
    name: 'vm-silent'
    severity: sevOutage
    frequency: logFrequency
    window: 'PT30M'
    description: 'No agent heartbeat from ${computer} for 10 minutes: VM, network or monitoring agent down.'
    query: '''
let lastBeat = toscalar(Heartbeat | where Computer == '{computer}' | summarize max(TimeGenerated));
print LastHeartbeat = lastBeat
| where isnull(LastHeartbeat) or LastHeartbeat < ago(10m)
'''
  }
  {
    name: 'containers'
    severity: sevDegraded
    frequency: logFrequency
    window: 'PT15M'
    description: 'A container of ${computer} restarted, is not running or is unhealthy, or the health reporter is silent.'
    query: '''
let reports = Syslog | where Computer == '{computer}' and ProcessName == 'umai-health';
let silent = reports | summarize n = count() | where n == 0
  | project Container = '-', Problem = 'no health report in 15 minutes';
let bad = reports
  | where SyslogMessage startswith 'container='
  | parse SyslogMessage with 'container=' Container ' state=' State ' health=' Health ' restarts=' Restarts:int
  | where Container !contains 'seed'
  | summarize NotOk = countif(State != 'running' or Health == 'unhealthy'), Restarted = max(Restarts) - min(Restarts) by Container
  | where NotOk >= 3 or Restarted > 0
  | project Container, Problem = strcat('restarted ', Restarted, 'x, not running or unhealthy in ', NotOk, ' of 15 reports');
union silent, bad
'''
  }
  {
    name: 'http-5xx'
    severity: sevDegraded
    frequency: logFrequency
    window: 'PT15M'
    description: 'Caddy answered too many requests with a 5xx status on ${env.hostname}.'
    query: '''
UmaiContainerLog_CL
| where Computer == '{computer}' and Message startswith 'access status='
| extend Status = toint(extract(@'status=(\d+)', 1, Message)), Path = extract(@'path=(\S*)', 1, Message)
| summarize Requests = count(), Errors = countif(Status >= 500), ErrorPaths = make_set_if(Path, Status >= 500, 10)
| extend ErrorPercent = round(100.0 * Errors / Requests, 1)
| where (Requests >= {http5xxMinRequests} and ErrorPercent > {http5xxPercent}) or Errors >= {http5xxCount}
'''
  }
  {
    name: 'latency'
    severity: sevDegraded
    frequency: logFrequency
    window: 'PT15M'
    description: 'Guardrail decisions on ${computer} are slower than {latencyP95Ms} ms at p95.'
    query: '''
UmaiContainerLog_CL
| where Computer == '{computer}' and Message contains 'umai.de.pipeline decision '
| extend TotalMs = todouble(extract(@'total_ms=([0-9.]+)', 1, Message))
| summarize Decisions = count(), P95Ms = percentile(TotalMs, 95), P50Ms = percentile(TotalMs, 50)
| where Decisions >= {latencyMinDecisions} and P95Ms > {latencyP95Ms}
'''
  }
  {
    name: 'l1b-timeouts'
    severity: sevDegraded
    frequency: logFrequency
    window: 'PT15M'
    description: 'L1b decision-model calls on ${computer} time out; enforcing ones fall back to the guardrail\'s failure policy.'
    query: '''
UmaiContainerLog_CL
| where Computer == '{computer}' and Message contains 'kind=timeout' and Message contains 'l1b.'
| summarize Enforcing = countif(Message contains 'l1b.failed '), Shadow = countif(Message contains 'l1b.shadow_failed ')
| where Enforcing >= {l1bEnforcingTimeouts} or Enforcing + Shadow >= {l1bAllTimeouts}
'''
  }
  {
    name: 'quota-429'
    severity: 3
    frequency: 'PT1H'
    window: 'PT1H'
    description: 'An organization on ${computer} hit its monthly quota (429 quota_exceeded): talk to them, raise their plan if agreed.'
    query: '''
UmaiContainerLog_CL
| where Computer == '{computer}' and Message contains 'admission.refused reason=quota_exceeded'
| extend Tenant = extract(@'tenant=(\S+)', 1, Message), Environment = extract(@'environment=(\S+)', 1, Message)
| summarize Refusals = count() by Tenant, Environment
'''
  }
  {
    name: 'disk-memory'
    severity: sevDegraded
    frequency: 'PT15M'
    window: 'PT15M'
    description: 'The root disk of ${computer} is above {diskUsedPercent} % or available memory below {memoryAvailablePercent} %.'
    query: '''
Perf
| where Computer == '{computer}'
| where (ObjectName == 'Logical Disk' and CounterName == '% Used Space' and InstanceName == '/')
     or (ObjectName == 'Memory' and CounterName == '% Available Memory')
| summarize Value = round(avg(CounterValue), 1) by CounterName
| where (CounterName == '% Used Space' and Value > {diskUsedPercent})
     or (CounterName == '% Available Memory' and Value < {memoryAvailablePercent})
'''
  }
  {
    name: 'backup'
    severity: sevDegraded
    frequency: 'PT1H'
    window: 'P2D'
    description: 'The nightly backup of ${computer} failed, or nothing was uploaded in 26 hours.'
    query: '''
let lines = Syslog | where Computer == '{computer}' and TimeGenerated > ago(26h);
let uploaded = toscalar(lines | where ProcessName == 'umai.sh' and SyslogMessage contains 'backup: uploaded' | count);
let failed = toscalar(lines | where SyslogMessage contains 'umai-backup.service' and (SyslogMessage contains 'Failed' or SyslogMessage contains 'FAILURE') | count);
print Uploaded = uploaded, Failures = failed
| where Uploaded == 0 or Failures > 0
'''
  }
]

var thresholdNames = [
  'http5xxPercent'
  'http5xxMinRequests'
  'http5xxCount'
  'latencyP95Ms'
  'latencyMinDecisions'
  'l1bEnforcingTimeouts'
  'l1bAllTimeouts'
  'diskUsedPercent'
  'memoryAvailablePercent'
]

resource logAlerts 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = [
  for q in queries: {
    name: '${namePrefix}-${q.name}'
    location: location
    tags: tags
    kind: 'LogAlert'
    properties: {
      displayName: '${namePrefix} ${q.name}'
      description: reduce(thresholdNames, q.description, (text, t) => replace(text, '{${t}}', string(thresholds[t])))
      severity: q.severity
      enabled: true
      scopes: [workspaceId]
      evaluationFrequency: q.frequency
      windowSize: q.window
      criteria: {
        allOf: [
          {
            query: reduce(
              thresholdNames,
              replace(q.query, '{computer}', computer),
              (text, t) => replace(text, '{${t}}', string(thresholds[t]))
            )
            timeAggregation: 'Count'
            operator: 'GreaterThan'
            threshold: 0
            failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
          }
        ]
      }
      autoMitigate: true
      actions: { actionGroups: [actionGroupId] }
    }
  }
]

output availabilityTestId string = readyTest.id
