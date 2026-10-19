#!/usr/bin/env bash
# Operator entry point for the single-VM stack (ADR-0032). Runs on the VM as root.
#
#   umai.sh init      generate the local secrets file (no Key Vault mode) and the signing key
#   umai.sh secrets   write the runtime secrets into /run/umai (tmpfs)
#   umai.sh up        secrets + start the shipped images (also used by umai.service at boot)
#   umai.sh backup    policy volume + policy-service database -> Blob Storage
#   umai.sh status    container status and health
#   umai.sh set-secret NAME   store a secret read from stdin in secrets.env (local-file mode)
#
# Non-secret settings: /opt/umai/config.env (see config.env.example). Secrets come from Key Vault
# (UMAI_KEY_VAULT set; managed identity) or, until Key Vault is available, from the root-only file
# /opt/umai/secrets.env (ADR-0032 §5). Containers only ever see /run/umai, cleared at reboot.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG=/opt/umai/config.env
SECRETS=/opt/umai/secrets.env
RUN=/run/umai
APP_UID=10001  # user in the engine image (Dockerfile)
CONSOLE_UID=1001  # user in the console image (umai-control-center Dockerfile)

# shellcheck source=/dev/null
set -a; . "$CONFIG"; set +a
: "${UMAI_BACKUP_ACCOUNT:?}"
UMAI_KEY_VAULT="${UMAI_KEY_VAULT:-}"
UMAI_CONSOLE_BASIC_AUTH="${UMAI_CONSOLE_BASIC_AUTH:-true}"  # unset keeps the gate of earlier releases
UMAI_ACTOR_KEY_ID="${UMAI_ACTOR_KEY_ID:-console-1}"
UMAI_GATEWAY="${UMAI_GATEWAY:-false}"  # true: run the UMAI gateway (Compose profile "gateway")

compose() {
  local profiles=()
  [[ "$UMAI_GATEWAY" == "true" ]] && profiles=(--profile gateway)
  docker compose --project-directory "$HERE" --env-file "$CONFIG" "${profiles[@]}" "$@"
}

# ship.sh replaces the Caddyfile by extracting a new file, and umai.sh rewrites the console gate,
# but Caddy's single-file bind mounts keep the old files open, so a change only takes effect after
# a restart (seconds of downtime).
caddy_follow_config() {
  if ! compose exec -T caddy cat /etc/caddy/Caddyfile </dev/null 2>/dev/null | cmp -s - "$HERE/Caddyfile" ||
    ! compose exec -T caddy cat /run/umai/console-gate.caddy </dev/null 2>/dev/null | cmp -s - "$RUN/console-gate.caddy"; then
    echo "umai: Caddy configuration changed, restarting caddy"
    compose restart caddy >/dev/null
  fi
}

imds_token() {  # $1 = resource
  curl -sf -H Metadata:true \
    "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=$1" |
    jq -r .access_token
}

secret() {  # $1 = Key Vault secret name; in secrets.env: upper case, '-' -> '_'
  if [[ -n "$UMAI_KEY_VAULT" ]]; then
    curl -sf -H "Authorization: Bearer $KV_TOKEN" \
      "https://${UMAI_KEY_VAULT}.vault.azure.net/secrets/$1?api-version=7.4" | jq -r .value
  else
    local var="${1//-/_}"
    var="${var^^}"
    # shellcheck source=/dev/null
    (set -a; . "$SECRETS"; printf '%s' "${!var:?missing $var in $SECRETS}")
  fi
}

new_hex() { openssl rand -hex 24; }
new_key32() { openssl rand -base64 32; }
# The console's P-256 key for on-behalf-of assertions (credentials 0.3): PKCS#8 PEM, base64 on one line.
new_actor_key() { openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 2>/dev/null | base64 -w0; }

ensure_local_secret() {  # $1 = name, $2 = generator (default new_hex); local-file mode: generate once
  [[ -n "$UMAI_KEY_VAULT" ]] && return 0
  grep -q "^$1=" "$SECRETS" 2>/dev/null && return 0
  printf "%s='%s'\n" "$1" "$("${2:-new_hex}")" >>"$SECRETS"
}

put() {  # $1 = file, $2 = owner uid, stdin = content
  install -m 0400 -o "$2" -g "$2" /dev/null "$1"
  cat >"$1"
}

# Caddy snippet in front of the console: basic auth (earlier releases, or an extra gate), or
# nothing, because the console signs people in itself (OIDC, ADR-0024).
# Secrets are assigned to variables before use: a failing $(secret …) inside an argument list
# would not stop the script (set -e), and an empty value would be written.
console_gate() {
  if [[ "$UMAI_CONSOLE_BASIC_AUTH" == "true" ]]; then
    : "${UMAI_CONSOLE_USER:?set UMAI_CONSOLE_USER in $CONFIG, or UMAI_CONSOLE_BASIC_AUTH=false}"
    local hash
    hash="$(secret umai-console-password-hash)"
    printf 'basic_auth {\n\t%s %s\n}\n' "$UMAI_CONSOLE_USER" "$hash"
  else
    printf '# UMAI_CONSOLE_BASIC_AUTH=false: the console signs people in (OIDC)\nvars console_gate oidc\n'
  fi | put "$RUN/console-gate.caddy" 0
  chmod 0444 "$RUN/console-gate.caddy"  # read by the caddy user inside its container
}

# The gateway's provider keys (credential "secret" routes): a JSON object name -> key, from Key
# Vault (umai-gateway-provider-keys) or `umai.sh set-secret UMAI_GATEWAY_PROVIDER_KEYS`. The file
# always exists (Compose needs it); empty while the gateway is off.
gateway_env() {
  if [[ "$UMAI_GATEWAY" != "true" ]]; then
    : | put "$RUN/gateway.env" 0
    return
  fi
  local keys
  keys="$(secret umai-gateway-provider-keys)"
  {
    printf 'UMAI_DE_GATEWAY_PROVIDER_KEYS=%s\n' "$keys"
    if [[ -n "${UMAI_GATEWAY_UPSTREAM_ALLOWLIST:-}" ]]; then
      printf 'UMAI_DE_GATEWAY_UPSTREAM_ALLOWLIST=%s\n' "$UMAI_GATEWAY_UPSTREAM_ALLOWLIST"
    fi
  } | put "$RUN/gateway.env" 0
  unset keys
}

secrets() {
  : "${UMAI_HOSTNAME:?}" "${UMAI_OIDC_ISSUER:?set UMAI_OIDC_ISSUER in $CONFIG (console sign-in, README §3a)}"
  : "${UMAI_OIDC_CLIENT_ID:?set UMAI_OIDC_CLIENT_ID in $CONFIG (console sign-in, README §3a)}"
  mkdir -p "$RUN"
  chmod 0711 "$RUN"
  if [[ -n "$UMAI_KEY_VAULT" ]]; then KV_TOKEN="$(imds_token https://vault.azure.net)"; fi
  ensure_local_secret UMAI_DB_OWNER_PASSWORD  # added after the first release: generated once
  ensure_local_secret UMAI_DB_APP_PASSWORD
  ensure_local_secret UMAI_TENANT_HMAC_MASTER_KEY new_key32  # organizations (ADR-0032 amendment)
  ensure_local_secret UMAI_SESSION_SECRET                    # console session cookie key
  ensure_local_secret UMAI_CONSOLE_ACTOR_KEY new_actor_key   # console -> policy service assertions
  local api control hmac hmac_master db_owner db_app actor_public session oidc_secret
  db_owner="$(secret umai-db-owner-password)"
  db_app="$(secret umai-db-app-password)"
  api="$(secret umai-api-token)"
  control="$(secret umai-control-token)"
  hmac="$(secret umai-tenant-hmac-keys)"
  hmac_master="$(secret umai-tenant-hmac-master-key)"
  session="$(secret umai-session-secret)"
  oidc_secret="$(secret umai-oidc-client-secret)"  # README §3a: set-secret from Terraform's output
  secret umai-signing-key | put "$RUN/signing.key" "$APP_UID"
  printf 'UMAI_DE_API_TOKEN=%s\nUMAI_DE_TENANT_HMAC_KEYS=%s\nUMAI_DE_TENANT_HMAC_MASTER_KEY=%s\n' \
    "$api" "$hmac" "$hmac_master" | put "$RUN/engine.env" 0
  secret umai-console-actor-key | base64 -d | put "$RUN/actor.pem" "$CONSOLE_UID"
  actor_public="$(openssl pkey -in "$RUN/actor.pem" -pubout -outform DER | base64 -w0)"
  printf 'UMAI_DE_CONTROL_API_TOKEN=%s\n' "$control" | put "$RUN/policy.env" 0
  {
    printf 'UMAI_DE_DATABASE_URL=postgresql+asyncpg://umai_app:%s@postgres:5432/umai\n' "$db_app"
    printf 'UMAI_DE_DATABASE_ADMIN_URL=postgresql+asyncpg://umai_owner:%s@postgres:5432/umai\n' "$db_owner"
    printf 'UMAI_DE_ACTOR_PUBLIC_KEYS={"%s":"%s"}\n' "$UMAI_ACTOR_KEY_ID" "$actor_public"
  } >>"$RUN/policy.env"
  printf 'POSTGRES_PASSWORD=%s\nUMAI_DB_APP_PASSWORD=%s\n' "$db_owner" "$db_app" |
    put "$RUN/postgres.env" 0
  {
    printf 'UMAI_ENGINE_TOKEN=%s\nUMAI_CONTROL_TOKEN=%s\n' "$api" "$control"
    printf 'UMAI_SESSION_SECRET=%s\nUMAI_OIDC_CLIENT_SECRET=%s\n' "$session" "$oidc_secret"
  } | put "$RUN/console.env" 0
  gateway_env
  console_gate
  unset KV_TOKEN api control hmac hmac_master db_owner db_app session oidc_secret
  echo "secrets: written to $RUN"
}

init() {  # secrets.env for the no-Key-Vault mode; never overwrites
  if [[ -e "$SECRETS" ]]; then
    echo "init: $SECRETS exists, refusing to overwrite" >&2
    exit 1
  fi
  # Uses the shipped engine image (ship.sh); nothing is built on the VM.
  local keys private public password key_id
  keys="$(docker run --rm umai-decision-engine:current \
    python -m umai_decision_engine.policy.cli keygen)"
  private="$(awk '/^private/ {print $NF}' <<<"$keys")"
  public="$(awk '/^public/ {print $NF}' <<<"$keys")"
  key_id="${UMAI_SIGNING_KEY_ID:-vm-$(date -u +%Y-%m)}"
  password="$(openssl rand -base64 18)"
  install -m 0600 /dev/null "$SECRETS"
  {
    printf "UMAI_API_TOKEN='%s'\n" "$(openssl rand -hex 32)"
    printf "UMAI_CONTROL_TOKEN='%s'\n" "$(openssl rand -hex 32)"
    printf "UMAI_TENANT_HMAC_KEYS='{\"console\":\"%s\",\"pilot-1\":\"%s\"}'\n" \
      "$(openssl rand -base64 32)" "$(openssl rand -base64 32)"
    printf "UMAI_SIGNING_KEY='%s'\n" "$private"
    printf "UMAI_CONSOLE_PASSWORD_HASH='%s'\n" \
      "$(docker run --rm caddy:2.10-alpine caddy hash-password --plaintext "$password")"
    printf "UMAI_BACKUP_SAS=''\n"
  } >>"$SECRETS"
  install -m 0600 /dev/null /root/console-password.txt
  printf '%s\n' "$password" >/root/console-password.txt
  sed -i -e "s|^UMAI_SIGNING_KEY_ID=.*|UMAI_SIGNING_KEY_ID=$key_id|" \
    -e "s|^UMAI_BUNDLE_PUBLIC_KEYS=.*|UMAI_BUNDLE_PUBLIC_KEYS='{\"$key_id\":\"$public\"}'|" "$CONFIG"
  unset keys private password
  echo "init: secrets in $SECRETS, console password in /root/console-password.txt, key id $key_id"
}

set_secret() {  # $1 = NAME; the value comes on stdin, never on the command line
  [[ -z "$UMAI_KEY_VAULT" ]] || { echo "set-secret: Key Vault mode; put it in the vault" >&2; exit 1; }
  [[ "${1:-}" =~ ^UMAI_[A-Z0-9_]+$ ]] || { echo "usage: umai.sh set-secret UMAI_NAME < value" >&2; exit 2; }
  local value
  IFS= read -r value || true
  [[ -n "$value" && "$value" != *"'"* ]] || { echo "set-secret: empty value, or a value with a quote" >&2; exit 1; }
  sed -i "/^$1=/d" "$SECRETS"
  printf "%s='%s'\n" "$1" "$value" >>"$SECRETS"
  unset value
  echo "set-secret: $1 stored in $SECRETS (applied by the next umai.sh up)"
}

backup() {
  local stamp name file url token
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  name="umai-backup-$stamp.tar.gz"
  file="/var/tmp/$name"
  # Logical dump of the tenant store (owner role) plus the policy volume.
  mkdir -p /var/tmp/umai-backup && chmod 700 /var/tmp/umai-backup
  compose exec -T postgres pg_dump -U umai_owner -d umai --no-owner --no-privileges |
    gzip -1 >/var/tmp/umai-backup/umai.sql.gz
  docker run --rm -v umai_policies:/p:ro -v /var/tmp/umai-backup:/db:ro -v /var/tmp:/out alpine \
    tar -czf "/out/$name" -C / p db/umai.sql.gz
  rm -rf /var/tmp/umai-backup
  url="https://${UMAI_BACKUP_ACCOUNT}.blob.core.windows.net/backups/$name"
  if [[ -n "$UMAI_KEY_VAULT" ]]; then  # managed identity with a blob data role
    token="$(imds_token https://storage.azure.com/)"
    curl -sf -X PUT -T "$file" -H "Authorization: Bearer $token" \
      -H "x-ms-version: 2023-11-03" -H "x-ms-blob-type: BlockBlob" "$url"
  else                                  # write-only container SAS from secrets.env
    curl -sf -X PUT -T "$file" -H "x-ms-blob-type: BlockBlob" "$url?$(secret umai-backup-sas)"
  fi
  rm -f "$file"
  echo "backup: uploaded $name"
}

case "${1:-}" in
  init) init ;;
  secrets) secrets ;;
  up) secrets; compose up -d --no-build --remove-orphans; caddy_follow_config; docker image prune -f >/dev/null ;;
  backup) backup ;;
  status) compose ps ;;
  set-secret) set_secret "${2:-}" ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
