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

The first run stops after delivering images and files, because no secrets exist yet. Later runs
end with `umai.sh up`, which replaces the changed services without downtime (§4a); a change that
needs downtime stops it with exit status 3 and a message, and nothing has changed.

## 3. First-time setup on the VM

```bash
ssh -i ~/.ssh/umai_beta_ed25519 umaiops@<hostname>
sudo -i
D=/opt/umai/src/umai-decision-engine/deploy/vm
docker pull caddy:2.11.7-alpine && docker pull alpine
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
| Deploy new code | workstation | `SSH_KEY=~/.ssh/umai_beta_ed25519 deploy/vm/ship.sh umaiops@<hostname>`: no downtime (§4a) |
| Deploy a network, Postgres or Caddy change | VM, root | `$D/umai.sh up --recreate` (about half a minute without service; §4a) |
| Status | VM, root | `$D/umai.sh status` |
| Logs | VM, root | `docker compose -p umai logs -f engine` (no raw content is logged, ADR-0005). Container names change with every deploy (`umai-engine-3`): address services, `docker compose -p umai exec <service> …` |
| API token for a pilot client | VM, root | `grep UMAI_API_TOKEN /opt/umai/secrets.env`; hand over on a secure channel |
| Add a pilot tenant | VM, root | Prefer an organization created in the console (ADR-0033): its owner makes keys on Get started. By hand: `curl -X POST -H "Authorization: Bearer $CONTROL" http://127.0.0.1:9200/v1/tenants -d '{"id":"pilot-1"}'` then `POST /v1/tenants/pilot-1/keys` (ADR-0023; the secret is in the response once). Its HMAC key is derived from the master key; an explicit key in `UMAI_TENANT_HMAC_KEYS` overrides it. The policy service port is not published; run the curl inside the network: `docker compose -p umai exec policy python -c …` or add a temporary `docker run --network umai_default` |
| Rotate a secret | VM, root | `printf %s "$new" \| $D/umai.sh set-secret UMAI_NAME` (or the vault), then `$D/umai.sh up`. Rotating `UMAI_SESSION_SECRET` signs everyone out; `UMAI_CONSOLE_ACTOR_KEY` takes effect for both sides at once (`up` restarts the console when its key file changed); never rotate `UMAI_TENANT_HMAC_MASTER_KEY` casually: hashes of derived tenants change; never rotate `UMAI_WEBHOOK_MASTER_KEY` (generated by `umai.sh`, ADR-0048) casually: every webhook signing secret changes and stored endpoint URLs can no longer be read (organizations re-add their endpoints) |
| Backup now | VM, root | `$D/umai.sh backup` |
| Restore test | VM, root | `$D/umai.sh restore-test` (a backup taken now), or a blob name with a read SAS on stdin (§4b) |
| Restore | VM, root | §4c; never copy a backup off the VM |
| Your IP changed (SSH times out) | workstation | `az network nsg rule update -g <rg> --nsg-name umaibeta-nsg -n ssh-operator --source-address-prefixes <ip>/32`; storage: `az storage account network-rule add/remove … --ip-address` |

## 4a. Deploys without downtime (ADR-0052)

`umai.sh up` (run by `ship.sh`) asks Compose what it would change and replaces each changed
service of policy, engine, gateway and console side by side: a new container starts beside the old
one, Docker's health check passes, the old one gets SIGTERM, finishes its requests (within
`stop_grace_period`: 30 s, the gateway 5 min for streams) and is removed. The policy service goes
first; it runs the migrations. Caddy finds the containers in Docker's DNS every second and retries
a refused connection on another container, so it is never reloaded for this; the console reaches
the engine and the policy service through Caddy as well (`http://caddy:9100`, `:9200`). A deploy
takes about a minute and holds one extra container at a time (measured: +150 MiB).

- **A new container that does not become healthy** is stopped and removed; the old one keeps
  serving, `up` exits 1 with the container's name. Read its log
  (`docker compose -p umai logs <service>`), fix, ship again.
- **Refused changes.** A changed network or volume, or a new Postgres or Caddy container (image,
  settings, mounts), cannot run twice: `up` names the change, exits 3 and changes nothing. Run
  `$D/umai.sh up --recreate` when a short outage is acceptable: it takes every container of every
  profile down (volumes stay) and starts the stack again, about 30 s locally. After a network
  change it is the only safe way: Compose 2.40 reconnects containers to a recreated network
  without their DNS names (2026-10-31, the policy service lost `postgres`).
- **The Caddyfile and the console gate** live in `/run/umai/caddy` (one directory mount); `up`
  validates and reloads Caddy only when one of them changed. A reload can fail the requests that
  arrive at that instant: ship Caddyfile changes at a quiet time.
- **Upgrading to this release (once):** the first `up` refuses because Caddy's container changes
  (Caddy 2.11.7, the directory mount); run `$D/umai.sh up --recreate` once. Caddy 2.11.4 or later
  is required: an older Caddy sends a retried request without its body.
- **Migrations expand, then contract.** For one deploy the old engine, gateway and console run
  against the schema the new policy service migrated. A migration may add tables, nullable
  columns or columns with defaults, indexes and grants; it must not drop, rename, retype or
  tighten anything the running release still uses. Removing something takes two releases: stop
  using it and ship, then drop it. A migration that cannot follow this ships with
  `up --recreate`, announced in its pull request.
- **Versions overlap for seconds.** A new container takes requests once it listens, before the
  health check has passed, and old and new answer side by side until the old one stops: keep
  console ↔ policy-service calls compatible across one release. Two policy services overlap, so a
  webhook may be delivered twice (at least once; receivers deduplicate on `event_id`).

## 4b. Restore test and recovery objectives (S0-14, ADR-0052)

```bash
$D/umai.sh restore-test                     # a backup taken now (exercises the backup too)
$D/umai.sh restore-test /var/tmp/<archive>  # an archive already on the VM
```

The nightly blobs: the stored SAS can only write, so give a read-only SAS for one blob on stdin
(Key Vault mode uses the managed identity). From the workstation, the SAS never on screen:

```bash
B=umai-backup-<UTC stamp>.tar.gz            # az storage blob list -c backups --account-name <SA> -o table
az storage blob generate-sas --account-name <SA> -c backups -n "$B" --permissions r \
  --expiry "$(date -u -d '+1 hour' +%Y-%m-%dT%H:%MZ)" --https-only --auth-mode key -o tsv |
  ssh -i ~/.ssh/umai_beta_ed25519 umaiops@<hostname> \
    "sudo /opt/umai/src/umai-decision-engine/deploy/vm/umai.sh restore-test $B"
```

It restores into a scratch Compose project (`umai-restore-<time>`: an internal network nothing can
leave, its own volumes, no ports, random secrets), prints rows per table (dump = restored), the
migration of the backup and of the image, starts the policy service and the engine and checks
them, removes everything and prints each phase's duration. Names, counts and durations only: the
backup holds real sign-in e-mails and stays on the VM. Backups from before ADR-0052 have no
GRANTs; the test adds the migrations' grants and says so. It needs about 0.4 GB of memory and a
restore's CPU for a minute or two: run it at a quiet time. Keep the output as the drill log
([`docs/security/backup-and-recovery.md`](../../docs/security/backup-and-recovery.md)).

| Objective | Target | Basis |
|---|---|---|
| RPO | 24 h | nightly backup at 02:30 UTC (+ up to 15 min) |
| RTO, VM intact | 1 h | restore ≈ 1 min locally (≤ 3 min on a B2s) + the operator |
| RTO, VM lost | 4 h | new VM (§1–3), `ship.sh`, restore; needs the secrets (Key Vault or a kept copy) |

## 4c. Restore

Test the archive first (§4b). Then, on the VM as root; the outage starts with `down`:

```bash
D=/opt/umai/src/umai-decision-engine/deploy/vm
C="docker compose --project-directory $D --env-file /opt/umai/config.env --profile gateway"
W="$(mktemp -d /var/tmp/umai-restore.XXXXXX)" && cd "$W"
B=umai-backup-<UTC stamp>.tar.gz
read -rs SAS                                # read-only SAS for $B (§4b), not echoed
curl -sf -o backup.tar.gz "https://<SA>.blob.core.windows.net/backups/$B?$SAS"; unset SAS
tar -xzf backup.tar.gz
$C down
docker volume rm umai_postgres
docker run --rm -v umai_policies:/p -v "$W/p":/in:ro alpine sh -c 'rm -rf /p/* /p/.[!.]* && cp -a /in/. /p/'
[ -d a ] && rm -rf /opt/umai/applications/* && cp -a a/. /opt/umai/applications/
$D/umai.sh secrets && $C up -d --wait postgres   # a fresh store with its roles
gunzip -c db/umai.sql.gz | $C exec -T postgres psql -U umai_owner -d umai -q -v ON_ERROR_STOP=1
$D/umai.sh up                               # starts everything; the policy service migrates
cd / && rm -rf "$W"
```

A backup from before ADR-0052 (`zgrep -c '^GRANT ' db/umai.sql.gz` prints 0) needs the
migrations' grants before the last `up`:

```bash
$C exec -T postgres psql -U umai_owner -d postgres -c 'CREATE DATABASE umai_ref'
$C run --rm --no-deps -T policy sh -c 'python -m umai_decision_engine.db.migrate "${UMAI_DE_DATABASE_ADMIN_URL%/umai}/umai_ref"'
$C exec -T postgres pg_dump -U umai_owner -d umai_ref --schema-only | grep -E '^(GRANT|REVOKE) ' |
  $C exec -T postgres psql -U umai_owner -d umai -q
$C exec -T postgres psql -U umai_owner -d postgres -c 'DROP DATABASE umai_ref'
```

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
control token alone runs operator actions only. Each assertion must name a live console session
(`UMAI_CONSOLE_SESSIONS=required`, ADR-0024 §2 amendment): ship the console together with an
engine image that has session records (migration 0011), or set `UMAI_CONSOLE_SESSIONS=optional`
in `config.env` until the console is updated. Cookies from before the update count as signed out,
so everyone signs in once.

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
sudo docker compose -p umai exec -T policy python - <<'EOF'
import os, httpx
headers = {"Authorization": f"Bearer {os.environ['UMAI_DE_CONTROL_API_TOKEN']}"}
print(httpx.put("http://127.0.0.1:9200/v1/tenants/<tenant>/plan", headers=headers,
                json={"edition": "enterprise"}).json())
EOF
```

## 7c. Billing with iyzico (optional, ADR-0044)

Needs plans (`UMAI_PLANS=true`, §7b) with a `billing` block for each paid edition. iyzico must
have enabled subscriptions and the `X-IYZ-SIGNATURE-V3` webhook signature for the merchant
(sandbox and live separately, entegrasyon@iyzico.com), and the live merchant must have no 3-D
Secure obligation (subscriptions are NON3D). Then:

1. Keys, never echoed: `read -rs K && printf %s "$K" | ssh … "sudo …/umai.sh set-secret UMAI_IYZICO_API_KEY"`,
   the same for `UMAI_IYZICO_SECRET_KEY`.
2. `config.env`: `UMAI_BILLING=iyzico`, `UMAI_IYZICO_MERCHANT_ID`, `UMAI_IYZICO_BASE_URL` (empty: sandbox).
3. Create the product and plans once, inside the policy container:
   `sudo docker compose -p umai exec policy python -m umai_decision_engine.billing.cli sync`; put the printed
   `{"pro": "…"}` into `UMAI_BILLING_PLAN_REFS`, then `umai.sh up`. `… billing.cli check` compares.
4. In iyzico's panel, set both notification URLs (merchant notifications and subscription
   notifications) to `https://<hostname>/hooks/iyzico`.

The policy service then offers each paid plan whose provider plan charges what the plans file
says; the console's Billing page sells it. `/hooks/iyzico` is the only path of the policy service
Caddy exposes.

## 7d. Client addresses and key network restrictions (ADR-0046)

Keys can be limited to networks (console: Applications → application → Keys → Allowed networks),
and a source address that presents too many bad credentials gets 429
`too_many_failed_attempts`. Both need the caller's address, which Caddy knows and the engine and
the gateway only see as Caddy's. So the Compose network has a fixed subnet, `172.30.240.0/24`
(other containers get addresses from its upper half), Caddy the fixed address `172.30.240.2`, and
the engine and the gateway trust that address alone: `UMAI_DE_TRUSTED_PROXIES:
'["172.30.240.2/32"]'` in `docker-compose.yml`. Caddy ignores an `X-Forwarded-For` sent by a client
and passes the address it saw; the engine reads the header only from Caddy and runs uvicorn with
`--no-proxy-headers`. Nothing else trusts the header.

- **Upgrading a VM from an earlier release:** a changed network is one of the changes `umai.sh up`
  refuses (§4a); `umai.sh up --recreate` takes the stack down and up with the fixed subnet. (On
  2026-10-31 a plain `up` let Compose 2.40 recreate the network in place and reconnect the
  containers without their DNS names.)
- **Subnet taken** (another Docker network or a VPN uses `172.30.240.0/24`): change the subnet, the
  `ip_range`, Caddy's `ipv4_address` and `UMAI_DE_TRUSTED_PROXIES` together in `docker-compose.yml`.
- **Check:** `docker compose -p umai logs engine | grep admission:` shows `trusted_proxies=1`. A key
  limited to your address works from it and gets 403 `ip_not_allowed` from another one.
- **Throttle:** 20 bad credentials per source address (IPv6: per /64) in 60 s, then 429 until the
  bucket refills (`UMAI_DE_AUTH_FAILURE_LIMIT`, `UMAI_DE_AUTH_FAILURE_WINDOW_S` in `engine.env` to
  change it; empty limit: off). The engine log names a throttled source once
  (`admission.throttled source=…`), never the credential it presented. Callers behind one NAT share
  a bucket.

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
