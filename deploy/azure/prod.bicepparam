using './main.bicep'

// umai-prod: the Stage 0 production stack for design partners (ADR-0050), a second instance of the
// single-VM template next to umai-beta, in resource group UMAI (West Europe). Its own VM, disk,
// IP, NSG, VNet and backup storage account; its own secrets (generated on the VM by umai.sh) and
// its own Auth0 tenant. Nothing here is secret. Runbook: deploy/azure/README.md.
//
//   az deployment group what-if -g UMAI -f deploy/azure/main.bicep -p deploy/azure/prod.bicepparam \
//     -p adminSshPublicKey="$(cat ~/.ssh/umai_prod_ed25519.pub)" adminSourceCidr=<your IP>/32

param prefix = 'umaiprod'

// Azure DNS label until the company domain points here: umai-prod.westeurope.cloudapp.azure.com.
// The custom hostname is a CNAME to this name (deploy/azure/README.md §2); the label stays.
param dnsLabel = 'umai-prod'

// 2 vCPU / 8 GiB burstable. The containers' memory limits add up to about 4 GiB, all of a B2s;
// B2ms doubles the memory and raises the CPU baseline from 40 % to 60 % (ADR-0050 §3). The
// subscription has B-series quota only (no Basv2, no Dv5). Revisit after the S0-3 load test.
param vmSize = 'Standard_B2ms'

param adminSshPublicKey = '<ssh-ed25519 AAAA... umai-prod operator>'  // its own key, not beta's
param adminSourceCidr = '<your public IP>/32'

// Contributor on the resource group only and Microsoft.KeyVault unregistered: local-file secrets
// and a write-only backup SAS, as on beta (ADR-0032 §5). Switch to true with the subscription owner.
param useKeyVault = false
param operatorObjectId = ''

// Backups: 35 days (beta 30), and an overwritten or deleted archive stays recoverable 14 days.
param backupRetentionDays = 35
param backupSoftDeleteDays = 14

param tags = {
  'umai-environment': 'prod'
}
