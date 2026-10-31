// Single-VM starter deployment of UMAI on Azure (ADR-0032).
// One Linux VM (Docker Compose stack from deploy/vm), optionally a Key Vault for secrets
// (useKeyVault), a storage account for nightly backups. No secrets in parameters.
// Instances: umai-beta (main.bicepparam) and umai-prod (prod.bicepparam, ADR-0050).
// Runbooks: deploy/vm/README.md (one VM), deploy/azure/README.md (environments, monitoring).

targetScope = 'resourceGroup'

@description('Region. West Europe by ADR-0031; any EU region works for the starter VM.')
param location string = resourceGroup().location

@description('Short prefix for resource names (lowercase letters and digits).')
@minLength(3)
@maxLength(10)
param prefix string = 'umaibeta'

@description('DNS label: <label>.<region>.cloudapp.azure.com becomes the public hostname.')
param dnsLabel string

@description('VM size, 2 vCPU / 4 GiB (ADR-0032). Standard_B2s where the subscription has no Basv2 quota.')
param vmSize string = 'Standard_B2als_v2'

param adminUsername string = 'umaiops'

@description('SSH public key of the operator. Password login is disabled.')
param adminSshPublicKey string

@description('CIDR allowed to reach SSH (22) and the Key Vault data plane, e.g. 203.0.113.7/32.')
param adminSourceCidr string

@description('Entra object id of the operator who writes secrets into Key Vault (optional).')
param operatorObjectId string = ''

@description('Days a backup blob is kept.')
param backupRetentionDays int = 30

@description('Secrets in Key Vault read by the VM identity (ADR-0032 §5). Needs the Microsoft.KeyVault provider and the right to create role assignments (Owner or User Access Administrator). false: secrets stay in a root-only file on the VM and backups use a SAS token.')
param useKeyVault bool = false

@description('Days a deleted or overwritten backup blob stays recoverable (blob soft delete); 0 = off. The VM\'s SAS may write, so an overwrite is recoverable only with this on (ADR-0050).')
@minValue(0)
@maxValue(365)
param backupSoftDeleteDays int = 0

@description('Tags on every resource, e.g. {"umai-environment": "prod"}: cost per environment in Cost Management.')
param tags object = {}

var suffix = uniqueString(resourceGroup().id)
var kvName = take('${prefix}-kv-${suffix}', 24)
var saName = take('${prefix}${suffix}', 24)
var roleKeyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var roleKeyVaultSecretsOfficer = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
var roleStorageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${prefix}-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'ssh-operator'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: adminSourceCidr
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
      {
        name: 'https'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
      {
        name: 'http-acme-redirect'
        properties: {
          priority: 120
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '80'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${prefix}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.40.0.0/24'] }
    subnets: [
      {
        name: 'vm'
        properties: {
          addressPrefix: '10.40.0.0/27'
          networkSecurityGroup: { id: nsg.id }
          // Same-region traffic to Key Vault and Storage arrives from the subnet, not the public
          // IP, so their firewalls admit the subnet through service endpoints.
          serviceEndpoints: [{ service: 'Microsoft.KeyVault' }, { service: 'Microsoft.Storage' }]
        }
      }
    ]
  }
}

resource pip 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${prefix}-pip'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: { domainNameLabel: dnsLabel }
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: '${prefix}-nic'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig'
        properties: {
          subnet: { id: vnet.properties.subnets[0].id }
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: { id: pip.id }
        }
      }
    ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: '${prefix}-vm'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: vmSize }
    osProfile: {
      computerName: '${prefix}-vm'
      adminUsername: adminUsername
      customData: base64(loadTextContent('cloud-init.yaml'))
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: adminSshPublicKey
            }
          ]
        }
        patchSettings: { patchMode: 'ImageDefault' }
      }
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        diskSizeGB: 64
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
        deleteOption: 'Delete'
      }
    }
    networkProfile: {
      networkInterfaces: [{ id: nic.id, properties: { deleteOption: 'Delete' } }]
    }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: { secureBootEnabled: true, vTpmEnabled: true }
    }
  }
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = if (useKeyVault) {
  name: kvName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 30
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      virtualNetworkRules: [{ id: vnet.properties.subnets[0].id }]
      ipRules: [{ value: adminSourceCidr }]
    }
  }
}

resource sa 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: saName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Cool'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    // Without role assignments the VM writes backups with a container SAS, which needs shared keys.
    allowSharedKeyAccess: !useKeyVault
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      virtualNetworkRules: [{ id: vnet.properties.subnets[0].id, action: 'Allow' }]
      // Storage IP rules take plain addresses for /31 and /32.
      ipRules: [{ value: replace(adminSourceCidr, '/32', ''), action: 'Allow' }]
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: sa
  name: 'default'
  properties: backupSoftDeleteDays > 0
    ? { deleteRetentionPolicy: { enabled: true, days: max(backupSoftDeleteDays, 1) } }
    : {}
}

resource backups 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'backups'
  properties: { publicAccess: 'None' }
}

resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: sa
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'expire-backups'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: { blobTypes: ['blockBlob'], prefixMatch: ['backups/'] }
            actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: backupRetentionDays } } }
          }
        }
      ]
    }
  }
}

resource vmReadsSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (useKeyVault) {
  scope: kv
  name: guid(kv.id, vm.id, roleKeyVaultSecretsUser)
  properties: {
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKeyVaultSecretsUser)
  }
}

resource operatorWritesSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (useKeyVault && !empty(operatorObjectId)) {
  scope: kv
  name: guid(kv.id, operatorObjectId, roleKeyVaultSecretsOfficer)
  properties: {
    principalId: operatorObjectId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKeyVaultSecretsOfficer)
  }
}

resource vmWritesBackups 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (useKeyVault) {
  scope: sa
  name: guid(sa.id, vm.id, roleStorageBlobDataContributor)
  properties: {
    principalId: vm.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataContributor)
  }
}

output hostname string = pip.properties.dnsSettings.fqdn
output publicIp string = pip.properties.ipAddress
output keyVaultName string = useKeyVault ? kv.name : ''
output backupStorageAccount string = sa.name
output sshCommand string = 'ssh ${adminUsername}@${pip.properties.dnsSettings.fqdn}'
