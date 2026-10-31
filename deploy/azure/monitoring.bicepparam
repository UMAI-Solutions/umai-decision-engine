using './monitoring.bicep'

// Monitoring for umai-beta and umai-prod (ADR-0051). Deploy after both VMs exist:
//
//   az deployment group what-if -g UMAI -n umai-monitoring -f deploy/azure/monitoring.bicep -p deploy/azure/monitoring.bicepparam
//
// Runbook: deploy/azure/README.md §5. Thresholds: monitoring.bicep `thresholds`.

param alertEmail = '<alert e-mail address>'

param environments = [
  {
    name: 'beta'
    vmName: 'umaibeta-vm'
    hostname: 'umai-beta.westeurope.cloudapp.azure.com'
    production: false
    availabilityTestFrequency: 900
    gateway: true
  }
  {
    name: 'prod'
    vmName: 'umaiprod-vm'
    hostname: 'umai-prod.westeurope.cloudapp.azure.com' // the company-domain hostname once its CNAME exists
    production: true
    availabilityTestFrequency: 300
    gateway: true
  }
]
