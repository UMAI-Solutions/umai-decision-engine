using './main.bicep'

// Fill before deploying. Nothing here is secret; secrets go into Key Vault afterwards.
param dnsLabel = 'umai-beta'
param adminSshPublicKey = '<ssh-ed25519 AAAA... operator>'
param adminSourceCidr = '<your public IP>/32'
param operatorObjectId = '<az ad signed-in-user show --query id -o tsv>'
