# Single-VM deployment on Azure (runbook)

Decision and limits: [ADR-0032](../../docs/adr/0032-single-vm-starter-on-azure.md). For demos and a
few pilot tenants, not paid SaaS. About $45/month at list price.

```text
Internet ──443──▶ Caddy ──/v1, /healthz, /readyz──▶ engine :9100 ──reads──▶ policies volume (+ .keys/)
                   │                                                         ▲ signs + writes
                   └──everything else (basic auth)──▶ console :3100 ──▶ policy service :9200 ──▶ postgres :5432
secrets: Key Vault or /opt/umai/secrets.env ──(umai.sh, at start)──▶ /run/umai (tmpfs)        (tenants, keys, drafts; RLS)
nightly backup (policies + pg_dump) ──▶ Blob Storage          images: built on the workstation, ship.sh
```

The VM never holds repository credentials: images are built on the operator workstation and
streamed over SSH (`ship.sh`). Two secret modes, chosen by `UMAI_KEY_VAULT` in `config.env`:

| Mode | When | Secrets | Backups |
|---|---|---|---|
| **Key Vault** (`useKeyVault=true`) | `Microsoft.KeyVault` is registered and the deployer may create role assignments | Key Vault, read by the VM's managed identity | managed identity with a blob data role |
| **Local file** (`useKeyVault=false`; `umai-beta` today) | resource-group Contributor only | `/opt/umai/secrets.env`, root 0600, generated on the VM by `umai.sh init` | write-only container SAS |

## 1. Create the Azure resources (workstation)

```bash
az login
ssh-keygen -t ed25519 -f ~/.ssh/umai_beta_ed25519 -N "" -C "umai-beta operator"
P=(dnsLabel=umai-beta adminSshPublicKey="$(cat ~/.ssh/umai_beta_ed25519.pub)"
   adminSourceCidr=<your IP>/32 useKeyVault=false vmSize=Standard_B2s)
az deployment group what-if -g <rg> -f deploy/azure/main.bicep -p "${P[@]}"
az deployment group create -g <rg> -n umai-beta -f deploy/azure/main.bicep -p "${P[@]}"
```

`vmSize`: pick a family with quota (`az vm list-usage -l westeurope`); `umai-beta` runs
`Standard_B2s` because the subscription has no Basv2 quota. Outputs: `hostname`,
`backupStorageAccount`, `keyVaultName` (empty in local-file mode), `publicIp`, `sshCommand`.

## 2. Ship the images and files (workstation)

```bash
SSH_KEY=~/.ssh/umai_beta_ed25519 deploy/vm/ship.sh umaiops@<hostname> ../umai-control-center
```

The first run stops after delivering images and files, because no secrets exist yet.

## 3. First-time setup on the VM

```bash
ssh -i ~/.ssh/umai_beta_ed25519 umaiops@<hostname>
sudo -i
D=/opt/umai/src/umai-decision-engine/deploy/vm
docker pull caddy:2.10-alpine && docker pull alpine
install -m 600 $D/config.env.example /opt/umai/config.env
vi /opt/umai/config.env        # UMAI_HOSTNAME, UMAI_BACKUP_ACCOUNT, UMAI_CONSOLE_USER; vault empty
$D/umai.sh init                # tokens, tenant HMAC keys, DB passwords, signing key + public key in config.env
cat /root/console-password.txt # store it in your password manager, then:
shred -u /root/console-password.txt
cp $D/umai.service $D/umai-backup.service $D/umai-backup.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now umai.service umai-backup.timer
```

Backup SAS (local-file mode), from the workstation. The token goes straight into the VM file;
it contains `&`, so it is appended rather than substituted with `sed`:

```bash
SA=<backupStorageAccount>
key="$(az storage account keys list -g <rg> -n $SA --query [0].value -o tsv)"
sas="$(az storage container generate-sas --account-name $SA --account-key "$key" -n backups \
  --permissions cw --expiry "$(date -u -d '+1 year' +%Y-%m-%dT%H:%MZ)" --https-only -o tsv)"
printf '%s' "$sas" | ssh -i ~/.ssh/umai_beta_ed25519 umaiops@<hostname> "sudo sh -c '
  v=\$(cat); sed -i /^UMAI_BACKUP_SAS=/d /opt/umai/secrets.env
  printf \"UMAI_BACKUP_SAS=%s\n\" \"'\''\$v'\''\" >> /opt/umai/secrets.env'"
unset key sas
```

The SAS expires after a year; renew it the same way.

In Key Vault mode put the secrets into the vault instead (names: `umai-api-token`,
`umai-control-token`, `umai-tenant-hmac-keys`, `umai-signing-key`, `umai-console-password-hash`),
set `UMAI_KEY_VAULT` and `UMAI_SIGNING_KEY_ID` / `UMAI_BUNDLE_PUBLIC_KEYS` by hand; `umai.sh init`
is not used.

Caddy obtains the TLS certificate on the first HTTPS request.

## 4. Day-to-day

| Task | Where | Command |
|---|---|---|
| Deploy new code | workstation | `SSH_KEY=~/.ssh/umai_beta_ed25519 deploy/vm/ship.sh umaiops@<hostname>` |
| Status | VM, root | `$D/umai.sh status` |
| Logs | VM, root | `docker compose -p umai logs -f engine` (no raw content is logged, ADR-0005) |
| API token for a pilot client | VM, root | `grep UMAI_API_TOKEN /opt/umai/secrets.env`; hand over on a secure channel |
| Add a pilot tenant | VM, root | `curl -X POST -H "Authorization: Bearer $CONTROL" http://127.0.0.1:9200/v1/tenants -d '{"id":"pilot-1"}'` then `POST /v1/tenants/pilot-1/keys` (ADR-0023; the secret is in the response once). Also add its HMAC key to `UMAI_TENANT_HMAC_KEYS` and `$D/umai.sh up`. The policy service port is not published; run the curl inside the network: `docker compose -p umai exec policy python -c …` or add a temporary `docker run --network umai_default` |
| Rotate a secret | VM, root | edit `/opt/umai/secrets.env` (or the vault), then `$D/umai.sh up` |
| Backup now | VM, root | `$D/umai.sh backup` |
| Restore | VM, root | `systemctl stop umai`; fetch the archive (`az storage blob download` on the workstation, then `scp`); `tar -xzf <archive> db/umai.sql.gz` and `docker run --rm -v umai_policies:/p -v $PWD/p:/in:ro alpine sh -c 'rm -rf /p/* && cp -a /in/. /p/'`; `systemctl start umai`, then `gunzip -c db/umai.sql.gz \| docker compose -p umai exec -T postgres psql -U umai_owner -d umai` into a freshly initialised database |
| Your IP changed (SSH times out) | workstation | `az network nsg rule update -g <rg> --nsg-name umaibeta-nsg -n ssh-operator --source-address-prefixes <ip>/32`; storage: `az storage account network-rule add/remove … --ip-address` |

## 5. Checks after a deployment

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

## 6. Tenants and their bundles (policy-bundle 0.8)

Bundles directly under the policy volume belong to the default tenant, which this stack sets to
the console's tenant (`UMAI_CONSOLE_TENANT_ID`, default `console`). Every other tenant evaluates
only bundles under `tenants/<tenant id>/` on the volume: a pilot tenant with its own API key gets
`CONFIG_MISSING` until it has one. Give it a guardrail by publishing through the policy service
with `X-Umai-Tenant: <tenant id>`, or by copying a signed version into
`/data/policies/tenants/<tenant id>/<bundle id>/` and re-signing it there (the manifest's
`tenant_id` must name the tenant). The policy service keeps the control token as the operator
(`UMAI_ACTOR_ASSERTION=optional`) until the console signs on-behalf-of assertions.

## 7. What this deployment does not have

No WAF, no HA, no DR region, one shared API token (body `tenant_id` not bound to the credential,
readiness T1), console basic auth instead of login, software-protected keys, no central log store;
in local-file mode the secrets sit on the VM disk (root only, encrypted at rest by Azure). Moving
on to the ADR-0031 stack is triggered by the conditions in ADR-0032 "Consequences".
