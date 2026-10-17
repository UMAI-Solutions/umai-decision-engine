# Single-VM deployment on Azure (runbook)

Decision and limits: [ADR-0032](../../docs/adr/0032-single-vm-starter-on-azure.md). For demos and a
few pilot tenants, not paid SaaS. About $41/month at list price.

```text
Internet ──443──▶ Caddy ──/v1, /healthz, /readyz──▶ engine :9100 ──reads──▶ policies volume
                   │                                                         ▲ signs + writes
                   └──everything else (basic auth)──▶ console :3100 ──▶ policy service :9200
Key Vault ──(managed identity, at start)──▶ /run/umai (tmpfs)      nightly backup ──▶ Blob
```

## 1. Create the Azure resources (operator workstation, PowerShell or bash)

```powershell
az login
az group create -n rg-umai-beta -l westeurope
# Edit deploy/azure/main.bicepparam: dnsLabel, SSH public key, your IP (/32), your object id
#   az ad signed-in-user show --query id -o tsv
az deployment group create -g rg-umai-beta -f deploy/azure/main.bicep -p deploy/azure/main.bicepparam
```

Note the outputs: `hostname`, `keyVaultName`, `backupStorageAccount`, `sshCommand`.

## 2. Put the secrets into Key Vault

Generate them locally. The values never go into a file in the repository or into chat.

```bash
KV=<keyVaultName>
az keyvault secret set --vault-name $KV -n umai-api-token     --value "$(openssl rand -hex 32)" -o none
az keyvault secret set --vault-name $KV -n umai-control-token --value "$(openssl rand -hex 32)" -o none
# Tenant HMAC keys: one per tenant that will call the API, plus the console's own tenant.
az keyvault secret set --vault-name $KV -n umai-tenant-hmac-keys -o none --value \
  "{\"console\":\"$(openssl rand -base64 32)\",\"pilot-1\":\"$(openssl rand -base64 32)\"}"
# Console basic-auth password hash (bcrypt):
az keyvault secret set --vault-name $KV -n umai-console-password-hash -o none --value \
  "$(docker run --rm caddy:2.10-alpine caddy hash-password --plaintext '<console password>')"
```

Bundle signing key (Ed25519, ADR-0032 §6), straight from `keygen` into Key Vault without a file:

```bash
docker build -t umai-decision-engine:current .        # in the engine repository
out="$(docker run --rm umai-decision-engine:current python -m umai_decision_engine.policy.cli keygen)"
az keyvault secret set --vault-name $KV -n umai-signing-key -o none   --value "$(printf '%s
' "$out" | awk '/^private/ {print $NF}')"
printf '%s
' "$out" | awk '/^public/ {print $NF}'    # public half -> UMAI_BUNDLE_PUBLIC_KEYS (§3)
unset out
```

## 3. Install the stack on the VM

```bash
ssh umaiops@<hostname>
sudo -i
cloud-init status --wait                       # docker, compose, swap, unattended upgrades
cd /opt/umai/src
# Read-only access: GitHub needs a separate deploy key per repository.
for r in umai-decision-engine umai-control-center; do
  ssh-keygen -t ed25519 -f ~/.ssh/$r -N "" -C "umai-vm $r"   # add ~/.ssh/$r.pub to repo $r (read-only)
  printf 'Host gh-%s
  HostName github.com
  User git
  IdentityFile ~/.ssh/%s
' $r $r >> ~/.ssh/config
done
git clone gh-umai-decision-engine:UMAI-Solutions/umai-decision-engine.git
git clone gh-umai-control-center:UMAI-Solutions/umai-control-center.git

cp /opt/umai/src/umai-decision-engine/deploy/vm/config.env.example /opt/umai/config.env
chmod 600 /opt/umai/config.env && vi /opt/umai/config.env   # hostname, vault, account, key id, {"<key id>": "<public>"}
chmod +x /opt/umai/src/umai-decision-engine/deploy/vm/umai.sh
cp /opt/umai/src/umai-decision-engine/deploy/vm/umai*.service \
   /opt/umai/src/umai-decision-engine/deploy/vm/umai-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now umai.service umai-backup.timer
```

The first start builds both images (a few minutes), seeds and signs the demo bundles, then starts
the stack. Caddy obtains the certificate on the first HTTPS request.

## 4. Day-to-day

| Task | Command (as root on the VM) |
|---|---|
| Status | `umai.sh status` |
| Logs | `docker compose -p umai logs -f engine` (no raw content is logged, ADR-0005) |
| Deploy new code | `umai.sh update` (git pull both repos, rebuild, restart) |
| Rotate a secret | change it in Key Vault, then `umai.sh up` |
| Backup now | `umai.sh backup` |
| Restore | `systemctl stop umai`; `az storage blob download` the archive (workstation) and copy it over; `docker run --rm -v umai_policies:/p -v umai_control:/c -v $PWD:/in alpine sh -c 'cd / && tar -xzf /in/<archive> && mv /c/backup.sqlite /c/control.sqlite'`; `systemctl start umai` |
| Give a pilot tenant access | add its HMAC key to `umai-tenant-hmac-keys`, `umai.sh up`, hand over the API token over a secure channel |

## 5. Checks after the first deployment

```bash
H=https://<hostname>
curl -s -o /dev/null -w '%{http_code}\n' $H/healthz              # 200
curl -s -o /dev/null -w '%{http_code}\n' $H/v1/bundles           # 401 (no token)
curl -s -o /dev/null -w '%{http_code}\n' $H/openapi.json         # 404
curl -s -o /dev/null -w '%{http_code}\n' $H/                     # 401 (basic auth)
curl -sI $H/healthz | grep -i strict-transport-security
```

Then sign in to the console at `$H/`, open the playground with synthetic data, and publish a
guardrail change: the engine's `/v1/bundles` shows the new version, signed with your key id.

## 6. What this deployment does not have

No WAF, no HA, no DR region, one shared API token (body `tenant_id` not bound to the credential,
readiness T1), console basic auth instead of login, software-protected keys, no central log store.
Moving on to the ADR-0031 stack is triggered by the conditions in ADR-0032 "Consequences".
