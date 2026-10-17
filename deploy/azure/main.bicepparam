using './main.bicep'

// Fill before deploying. Nothing here is secret; secrets go into Key Vault afterwards.
param dnsLabel = 'umai-beta'
param adminSshPublicKey = '<ssh-ed25519 AAAA... operator>'
param adminSourceCidr = '<your public IP>/32'
param useKeyVault = false           // true needs Microsoft.KeyVault and role-assignment rights
param operatorObjectId = ''         // Key Vault mode: az ad signed-in-user show --query id -o tsv
