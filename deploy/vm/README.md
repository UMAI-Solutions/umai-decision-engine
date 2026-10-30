# Single-VM deployment on Azure (runbook)

Decision and limits: [ADR-0032](../../docs/adr/0032-single-vm-starter-on-azure.md). For demos and a
few pilot tenants, not paid SaaS. About $45/month at list price.

```text
Internet ──443──▶ Caddy ──/v1, /healthz, /readyz──▶ engine :9100 ──reads──▶ policies volume (+ .keys/)
                   │                                                         ▲ signs + writes
                   └──everything else (sign-in)────▶ console :3100 ──▶ policy service :9200 ──▶ postgres :5432
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
vi /opt/umai/config.env        # UMAI_HOSTNAME, UMAI_BACKUP_ACCOUNT, UMAI_OIDC_* (§3a); vault empty
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
`umai-control-token`, `umai-tenant-hmac-keys`, `umai-tenant-hmac-master-key`, `umai-signing-key`,
`umai-console-password-hash`, `umai-session-secret`, `umai-console-actor-key` (base64 of a P-256
PKCS#8 PEM), `umai-oidc-client-secret`), set `UMAI_KEY_VAULT` and `UMAI_SIGNING_KEY_ID` /
`UMAI_BUNDLE_PUBLIC_KEYS` by hand; `umai.sh init` is not used.

In local-file mode `umai.sh up` generates the newer secrets once, on first use: the database
passwords, the tenant HMAC master key (ADR-0032 amendment), the console's session secret and its
assertion key (the policy service gets the matching public key automatically).

Caddy obtains the TLS certificate on the first HTTPS request.

### 3a. Console sign-in (Auth0)

The console signs people in with OIDC (ADR-0024; `umai-control-center` docs/sign-in.md). Its
Auth0 application is Terraform in `umai-control-center/identity/auth0` (`console_url` in the
workspace's tfvars). After `terraform apply` there, on the workstation:

```bash
# the secret goes from Terraform straight into the VM file, never onto the screen
terraform output -raw console_client_secret |
  ssh -i ~/.ssh/umai_beta_ed25519 umaiops@<hostname> \
    "sudo /opt/umai/src/umai-decision-engine/deploy/vm/umai.sh set-secret UMAI_OIDC_CLIENT_SECRET"
terraform output -raw console_client_id      # not secret: UMAI_OIDC_CLIENT_ID in config.env
```

Set `UMAI_OIDC_ISSUER` (`https://<tenant domain>/`) and `UMAI_OIDC_CLIENT_ID` in
`/opt/umai/config.env`; `umai.sh up` refuses to start without them. `UMAI_CONSOLE_BASIC_AUTH=true`
keeps Caddy's basic auth in front as well (default for installs that had it); set `false` to
let people sign up and onboard on their own. Any other value stops `umai.sh` with an error, so a
typo never switches the gate off.

## 4. Day-to-day

| Task | Where | Command |
|---|---|---|
| Deploy new code | workstation | `SSH_KEY=~/.ssh/umai_beta_ed25519 deploy/vm/ship.sh umaiops@<hostname>` |
| Status | VM, root | `$D/umai.sh status` |
| Logs | VM, root | `docker compose -p umai logs -f engine` (no raw content is logged, ADR-0005) |
| API token for a pilot client | VM, root | `grep UMAI_API_TOKEN /opt/umai/secrets.env`; hand over on a secure channel |
| Add a pilot tenant | VM, root | Prefer an organization created in the console (ADR-0033): its owner makes keys on Get started. By hand: `curl -X POST -H "Authorization: Bearer $CONTROL" http://127.0.0.1:9200/v1/tenants -d '{"id":"pilot-1"}'` then `POST /v1/tenants/pilot-1/keys` (ADR-0023; the secret is in the response once). Its HMAC key is derived from the master key; an explicit key in `UMAI_TENANT_HMAC_KEYS` overrides it. The policy service port is not published; run the curl inside the network: `docker compose -p umai exec policy python -c …` or add a temporary `docker run --network umai_default` |
| Rotate a secret | VM, root | `printf %s "$new" \| $D/umai.sh set-secret UMAI_NAME` (or the vault), then `$D/umai.sh up`. Rotating `UMAI_SESSION_SECRET` signs everyone out; `UMAI_CONSOLE_ACTOR_KEY` takes effect for both sides at once (`up` restarts the console when its key file changed); never rotate `UMAI_TENANT_HMAC_MASTER_KEY` casually: hashes of derived tenants change |
| Backup now | VM, root | `$D/umai.sh backup` |
| Restore | VM, root | `systemctl stop umai`; fetch the archive (`az storage blob download` on the workstation, then `scp`); `tar -xzf <archive> db/umai.sql.gz` and `docker run --rm -v umai_policies:/p -v $PWD/p:/in:ro alpine sh -c 'rm -rf /p/* && cp -a /in/. /p/'`; `systemctl start umai`, then `gunzip -c db/umai.sql.gz \| docker compose -p umai exec -T postgres psql -U umai_owner -d umai` into a freshly initialised database |
| Your IP changed (SSH times out) | workstation | `az network nsg rule update -g <rg> --nsg-name umaibeta-nsg -n ssh-operator --source-address-prefixes <ip>/32`; storage: `az storage account network-rule add/remove … --ip-address` |

## 5. Checks after a deployment

```bash
H=https://<hostname>
curl -s -o /dev/null -w '%{http_code}\n' $H/healthz              # 200
curl -s -o /dev/null -w '%{http_code}\n' $H/v1/bundles           # 401 (no token)
curl -s -o /dev/null -w '%{http_code}\n' $H/openapi.json         # 404
curl -s -o /dev/null -w '%{http_code}\n' $H/brand/umai-wordmark.svg  # 200 (public brand files, no basic auth)
curl -s -o /dev/null -w '%{http_code}\n' $H/                     # 401 with basic auth; else 307 to /auth/login
curl -sI $H/healthz | grep -i strict-transport-security
```

Then sign in to the console at `$H/` with a synthetic test user, create an organization (it
gets the starter guardrail), create a key on Get started and send its sample request: `BLOCK`
with `TR_TCKN`. Invite a second synthetic address and accept from a fresh browser.

## 6. Tenants and their bundles (policy-bundle 0.8)

Bundles directly under the policy volume belong to the default tenant, which this stack sets to
the console's tenant (`UMAI_CONSOLE_TENANT_ID`, default `console`). Every other tenant evaluates
only bundles under `tenants/<tenant id>/` on the volume: a pilot tenant with its own API key gets
`CONFIG_MISSING` until it has one. Give it a guardrail by publishing through the policy service
with `X-Umai-Tenant: <tenant id>`, or by copying a signed version into
`/data/policies/tenants/<tenant id>/<bundle id>/` and re-signing it there (the manifest's
`tenant_id` must name the tenant). Organizations created in the console get the default
tenant's starter guardrail there automatically, signed with the deployment key. The policy
service requires the console's on-behalf-of assertion (`UMAI_ACTOR_ASSERTION=required`); the
control token alone runs operator actions only.

## 7. The UMAI gateway (optional, ADR-0034)

The gateway answers OpenAI-compatible calls on the same hostname: `/v1/chat/completions`,
`/v1/responses`, `/v1/embeddings`, `/v1/models` (contract `docs/contracts/gateway-api.md`).

1. Provider keys the deployment holds for `credential: secret` providers, as one JSON object
   (`{}` if every provider passes the caller's key through):

   ```bash
   echo '{"openai": "<provider key>"}' | sudo /opt/umai/umai.sh set-secret UMAI_GATEWAY_PROVIDER_KEYS
   ```

   With Key Vault: secret `umai-gateway-provider-keys`.
2. Applications and providers (ADR-0039): an application has one guardrail and its own keys; the
   gateway sends its calls to the default provider (a built-in pass-through OpenAI provider
   unless another is marked default) or to `@<provider>/<model>`. Create them in the console
   (Applications, AI Gateway → Providers), with `PUT /v1/applications/<id>` and
   `PUT /v1/providers/<id>` on the policy service (control token with `X-Umai-Tenant`), or by hand
   in `/opt/umai/applications/<tenant>.yaml`. Reloaded within 5 seconds:

   ```yaml
   applications:
     - application_id: main
       guardrail: gr-demo-data-protection   # callers send X-Umai-Provider-Key (built-in OpenAI)
   ```

   Keys: `POST /v1/tenants/<tenant>/keys` with `{"name": ..., "application_id": "main"}`; the key
   then works on the gateway and on `/v1/evaluate`, both with the application's guardrail. The
   operator token acts as the tenant `UMAI_CONSOLE_TENANT_ID` and names the application with
   `X-Umai-Application`, unless the tenant has only one.

   **Upgrading from gateways (ADR-0038).** Migration 0004 runs when the policy service starts and
   renames each key's `gateway_id` to `application_id`. Before `umai.sh up`, write
   `/opt/umai/applications/<tenant>.yaml` for every `/opt/umai/gateways/<tenant>.yaml`: one
   application per gateway with the **same id** (so its keys keep working) and the same
   guardrail; a gateway's provider becomes an entry under `providers:` (not needed for
   `{ kind: openai }` with pass-through, which is the built-in provider). Then remove the old
   directory.
3. `UMAI_GATEWAY=true` in `config.env`, then `sudo /opt/umai/umai.sh up`.
4. Check: `curl https://$UMAI_HOSTNAME/v1/models -H "Authorization: Bearer <key or token>"`.
   Clients use `base_url=https://<hostname>/v1` with the official OpenAI SDKs.

Caddy caps request bodies at 4 MB on the gateway paths (a whole conversation per call, up to
`UMAI_DE_GATEWAY_MAX_INPUT_CHARS`, default 256 000 characters) and 512 KB elsewhere. Switching the gateway off later: set
`UMAI_GATEWAY=false` and run `docker compose -p umai stop gateway` once (a profiled container is
not removed by `up`).

## 7a. L1b decision provider (optional, ADR-0040)

Guardrails with decision policies (L1b) ask the OpenAI Decisions API (`gpt-6-luna`). The
engine and the gateway get the provider when its key is stored; without it they have no
decision provider, and a guardrail with decision policies answers its L1b part with the bundle's
failure policy. Use a key of a dedicated OpenAI project, so its usage can be capped and revoked
on its own. On the workstation, in Git Bash (the key is never echoed or put in the history):

```bash
read -rs OPENAI_KEY && printf %s "$OPENAI_KEY" | ssh -i ~/.ssh/umai_beta_ed25519 umaiops@<hostname> \
  "sudo /opt/umai/src/umai-decision-engine/deploy/vm/umai.sh set-secret UMAI_OPENAI_API_KEY"; unset OPENAI_KEY
```

Then `umai.sh up` on the VM (Key Vault mode: secret `umai-openai-api-key`). It writes
`UMAI_DE_DECISION_PROVIDERS` (default: OpenAI, `https://api.openai.com/v1`, `gpt-6-luna`; replace it
with `UMAI_DECISION_PROVIDERS` in `config.env`, for example the EU endpoint once OpenAI approved EU
processing) and `UMAI_DE_DECISION_PROVIDER_KEYS` into `engine.env`; Compose recreates the engine
and the gateway. Redacted content then goes to OpenAI, in the US unless EU processing is approved
(Q59). Every converted policy stays in shadow until Q60 is decided.

## 7b. Plans and monthly quotas (optional, ADR-0043)

`UMAI_PLANS=true` in `config.env`, then `umai.sh up`: the policy service reads the image's
`/app/plans.yaml` (the repository's `deploy/plans.yaml`: Community 10 000 live and 1 000 test-key
requests a month, 32 000 characters per request; Enterprise metered, not refused) and rewrites
every tenant's signed snapshot with its limits. Over the limit the engine and the gateway answer
429 `quota_exceeded`. Counting needs the records (`UMAI_RECORDS_SINK=database`, the default).
Change one organization's plan as the operator, inside the policy container:

```bash
sudo docker exec -i umai-policy-1 python - <<'EOF'
import os, httpx
headers = {"Authorization": f"Bearer {os.environ['UMAI_DE_CONTROL_API_TOKEN']}"}
print(httpx.put("http://127.0.0.1:9200/v1/tenants/<tenant>/plan", headers=headers,
                json={"edition": "enterprise"}).json())
EOF
```

## 8. What this deployment does not have

Records (ADR-0041): the engine and the gateway write decision events and usage to the same
Postgres with the application role (`UMAI_DE_DATABASE_URL` in `engine.env`, `UMAI_RECORDS_SINK`
in `config.env`, default `database`; `none` turns it off). They start after the policy service,
which runs the migrations, serves the records (`GET /v1/decision-events`, `GET /v1/usage`) and
deletes them after retention every hour (30 days of decisions, 400 days of usage). A database outage never blocks a decision: records queue in memory and are dropped
and counted when the queue is full (`records.stats` in the engine log, ADR-0042).

No WAF, no HA, no DR region, one shared API token (body `tenant_id` not bound to the credential,
readiness T1), tenant HMAC keys derived from one master key, software-protected keys, no central log store;
in local-file mode the secrets sit on the VM disk (root only, encrypted at rest by Azure). Moving
on to the ADR-0031 stack is triggered by the conditions in ADR-0032 "Consequences".
