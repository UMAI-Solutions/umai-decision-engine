// Monitoring and alerting for the single-VM environments (umai-beta, umai-prod; ADR-0051).
// One Log Analytics workspace, one data collection rule and one e-mail action group shared by both
// VMs; per environment (monitoring/environment.bicep) the Azure Monitor Agent, a container health
// reporter, availability tests and the alert rules. Deployed after the VMs exist, into the same
// resource group. Runbook and cost: deploy/azure/README.md §5. Facts: docs/reference/azure-monitoring.md.
//
// What reaches the workspace (no content, ADR-0005): performance counters (memory, disk), syslog
// lines of the health reporter, the backup service and systemd about umai units, and the
// containers' log lines with Caddy's access lines shortened to status, duration, method and path
// (no client address) and Postgres DETAIL/STATEMENT lines dropped.

targetScope = 'resourceGroup'

param location string = resourceGroup().location

@description('Short prefix for the shared monitoring resources.')
param prefix string = 'umaiops'

@description('Where alerts go (one address; a team list is better than a person).')
param alertEmail string

@description('Environments to watch: name, vmName (also the syslog Computer name), hostname, production (alert severities, evaluation frequency), availabilityTestFrequency (seconds: 300, 600 or 900), gateway (the gateway profile is on).')
param environments array

@description('Days logs are kept; 31 are included in the ingestion price (docs/reference/azure-monitoring.md).')
@minValue(30)
@maxValue(730)
param retentionDays int = 30

@description('Ingestion cap per day in GB; past it the workspace stops collecting until the next day (and alerts go blind). -1 = no cap.')
param dailyCapGb int = 1

@description('Thresholds for the log alerts (deploy/azure/README.md §5).')
param thresholds object = {
  http5xxPercent: 5 // share of Caddy responses with status >= 500 in 15 min...
  http5xxMinRequests: 20 // ...once at least this many requests came in
  http5xxCount: 10 // or this many 5xx in 15 min, whatever the share
  latencyP95Ms: 1500 // p95 of the decision total_ms in 15 min (evaluation budget default)
  latencyMinDecisions: 20
  l1bEnforcingTimeouts: 5 // l1b.failed kind=timeout in 15 min (decisions fall back to the failure policy)
  l1bAllTimeouts: 30 // incl. shadow calls
  diskUsedPercent: 85 // root filesystem
  memoryAvailablePercent: 10
  cpuCreditsMin: 30 // B-series: credits left before the VM is throttled to its baseline
  certificateDays: 14 // TLS certificate remaining lifetime checked by the availability test
}

param tags object = {
  'umai-environment': 'shared'
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-law'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: retentionDays
    workspaceCapping: { dailyQuotaGb: dailyCapGb }
    features: { enableLogAccessUsingOnlyResourcePermissions: true }
  }
}

// Container log lines (Docker json-file), one row per line. Filled by the data collection rule.
resource containerLogTable 'Microsoft.OperationalInsights/workspaces/tables@2023-09-01' = {
  parent: workspace
  name: 'UmaiContainerLog_CL'
  properties: {
    plan: 'Analytics'
    retentionInDays: retentionDays
    schema: {
      name: 'UmaiContainerLog_CL'
      columns: [
        { name: 'TimeGenerated', type: 'datetime' }
        { name: 'Computer', type: 'string' }
        { name: 'FilePath', type: 'string' }
        { name: 'Message', type: 'string' }
        { name: 'Stream', type: 'string' }
      ]
    }
  }
}

// Workspace-based Application Insights: only the home of the availability tests.
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-appi'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    IngestionMode: 'LogAnalytics'
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: '${prefix}-ag'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'umai-ops'
    enabled: true
    emailReceivers: [
      { name: 'ops-email', emailAddress: alertEmail, useCommonAlertSchema: true }
    ]
  }
}

// Docker keeps a JSON object per line: {"log": "<line>\n", "stream": "stdout", "time": "..."}.
// "time" is not declared (a reserved word in KQL); TimeGenerated is the agent's collection time.
var containerLogTransform = '''
source
| where not(log contains '/readyz HTTP/1.1" 200' or log contains '/healthz HTTP/1.1" 200')
| where not(log contains 'DETAIL:' or log contains 'STATEMENT:')
| extend a = parse_json(log)
| extend Message = iff(log contains '"logger":"http.log.access', strcat('access status=', tostring(a.status), ' duration_s=', tostring(a.duration), ' method=', tostring(a.request.method), ' path=', tostring(a.request.uri), ' size=', tostring(a.size)), tostring(log))
| project TimeGenerated, Computer, FilePath, Message, Stream = tostring(stream)
'''

var syslogTransform = '''
source
| where ProcessName == 'umai-health' or ProcessName == 'umai.sh' or SyslogMessage contains 'umai'
'''

resource dcr 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: '${prefix}-dcr-vm'
  location: location
  tags: tags
  kind: 'Linux'
  dependsOn: [containerLogTable]
  properties: {
    description: 'UMAI single-VM hosts: memory, disk, umai syslog lines, container logs (ADR-0051).'
    streamDeclarations: {
      'Custom-UmaiContainerLog': {
        columns: [
          { name: 'TimeGenerated', type: 'datetime' }
          { name: 'Computer', type: 'string' }
          { name: 'FilePath', type: 'string' }
          { name: 'log', type: 'string' }
          { name: 'stream', type: 'string' }
        ]
      }
    }
    dataSources: {
      performanceCounters: [
        {
          name: 'host'
          streams: ['Microsoft-Perf']
          samplingFrequencyInSeconds: 60
          counterSpecifiers: [
            '\\Memory(*)\\% Available Memory'
            '\\Logical Disk(*)\\% Used Space'
          ]
        }
      ]
      syslog: [
        {
          name: 'daemon'
          streams: ['Microsoft-Syslog']
          facilityNames: ['daemon']
          logLevels: ['Info', 'Notice', 'Warning', 'Error', 'Critical', 'Alert', 'Emergency']
        }
      ]
      logFiles: [
        {
          name: 'containers'
          streams: ['Custom-UmaiContainerLog']
          filePatterns: ['/var/lib/docker/containers/*/*-json.log']
          format: 'json'
        }
      ]
    }
    destinations: {
      logAnalytics: [{ name: 'workspace', workspaceResourceId: workspace.id }]
    }
    dataFlows: [
      { streams: ['Microsoft-Perf'], destinations: ['workspace'] }
      { streams: ['Microsoft-Syslog'], destinations: ['workspace'], transformKql: syslogTransform }
      {
        streams: ['Custom-UmaiContainerLog']
        destinations: ['workspace']
        transformKql: containerLogTransform
        outputStream: 'Custom-UmaiContainerLog_CL'
      }
    ]
  }
}

module environment 'monitoring/environment.bicep' = [
  for env in environments: {
    name: 'monitoring-${env.name}'
    params: {
      location: location
      env: env
      workspaceId: workspace.id
      dataCollectionRuleId: dcr.id
      appInsightsId: appInsights.id
      actionGroupId: actionGroup.id
      thresholds: thresholds
    }
  }
]

output workspaceName string = workspace.name
output workspaceCustomerId string = workspace.properties.customerId
output actionGroupId string = actionGroup.id
