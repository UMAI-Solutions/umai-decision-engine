#!/usr/bin/env bash
# Operator entry point for the single-VM stack (ADR-0032). Runs on the VM as root.
#
#   umai.sh init      generate the local secrets file (no Key Vault mode) and the signing key
#   umai.sh secrets   write the runtime secrets into /run/umai (tmpfs)
#   umai.sh up        secrets + deploy the shipped images without downtime (also at boot)
#   umai.sh up --recreate   stop the whole stack and start it again (network, Postgres, Caddy changes)
#   umai.sh backup    tenant store + policy volume + applications -> Blob Storage
#   umai.sh restore-test [ARCHIVE | BLOB]   restore a backup into a scratch project, check it, remove it
#   umai.sh status    container status and health
#   umai.sh set-secret NAME   store a secret read from stdin in secrets.env (local-file mode)
#
# Non-secret settings: /opt/umai/config.env (see config.env.example). Secrets come from Key Vault
# (UMAI_KEY_VAULT set; managed identity) or, until Key Vault is available, from the root-only file
# /opt/umai/secrets.env (ADR-0032 §5). Containers only ever see /run/umai, cleared at reboot.
# UMAI_CONFIG, UMAI_SECRETS and UMAI_RUN_DIR move these paths (tests; UMAI_RUN_DIR as in Compose).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${UMAI_CONFIG:-/opt/umai/config.env}"
SECRETS="${UMAI_SECRETS:-/opt/umai/secrets.env}"
APP_UID=10001  # user in the engine image (Dockerfile)
CONSOLE_UID=1001  # user in the console image (umai-control-center Dockerfile)

# shellcheck source=/dev/null
set -a; . "$CONFIG"; set +a
: "${UMAI_BACKUP_ACCOUNT:?}"
UMAI_KEY_VAULT="${UMAI_KEY_VAULT:-}"
RUN="${UMAI_RUN_DIR:-/run/umai}"
UMAI_CONSOLE_BASIC_AUTH="${UMAI_CONSOLE_BASIC_AUTH:-true}"  # unset keeps the gate of earlier releases
UMAI_ACTOR_KEY_ID="${UMAI_ACTOR_KEY_ID:-console-1}"
UMAI_GATEWAY="${UMAI_GATEWAY:-false}"  # true: run the UMAI gateway (Compose profile "gateway")
UMAI_PLANS="${UMAI_PLANS:-false}"  # true: the image's plans and monthly quotas (ADR-0043)
# A security gate is never switched off by a typo ("False", "no", "yes" …): refuse to go on.
if [[ "$UMAI_CONSOLE_BASIC_AUTH" != "true" && "$UMAI_CONSOLE_BASIC_AUTH" != "false" ]]; then
  echo "umai: UMAI_CONSOLE_BASIC_AUTH in $CONFIG must be true or false" >&2
  exit 1
fi
if [[ "$UMAI_PLANS" != "true" && "$UMAI_PLANS" != "false" ]]; then
  echo "umai: UMAI_PLANS in $CONFIG must be true or false" >&2
  exit 1
fi
UMAI_BILLING="${UMAI_BILLING:-none}"  # iyzico: sell plans through iyzico (ADR-0044)
if [[ "$UMAI_BILLING" != "none" && "$UMAI_BILLING" != "iyzico" ]]; then
  echo "umai: UMAI_BILLING in $CONFIG must be none or iyzico" >&2
  exit 1
fi

compose() {
  local profiles=()
  [[ "$UMAI_GATEWAY" == "true" ]] && profiles=(--profile gateway)
  docker compose --project-directory "$HERE" --env-file "$CONFIG" "${profiles[@]}" "$@"
}

# ------------------------------------------------------------------ deploys (ADR-0052)
# `up` replaces a service by starting its new container beside the old one, waiting for Docker's
# health check and only then stopping the old one. Caddy needs no change for that: it sends each
# request to a running container of the service, looked up in Docker's DNS every second, retries
# a refused connection on another one and keeps no idle connections to them (Caddyfile). A
# stopping container refuses new connections and finishes the requests it has (uvicorn and
# Next.js on SIGTERM). The console calls the engine and the policy service through Caddy too.
# Postgres and Caddy cannot run twice, and a changed network makes Compose 2.40 reconnect
# containers without their DNS names (2026-10-31): `up` refuses those changes, and
# `up --recreate` stops the stack and starts it again.
ROLLING=(policy engine gateway console)  # the policy service first: it runs the migrations

running() {  # $1 = service: ids of its running containers, oldest first
  compose ps -q --status running "$1" 2>/dev/null |
    xargs -r docker inspect -f '{{index .Config.Labels "com.docker.compose.container-number"}} {{.Id}}' |
    sort -n | awk '{print $2}'
}

name_of() { docker inspect -f '{{.Name}}' "$1" | sed 's|^/||'; }

quiet() {  # run a command; show its output only when it fails
  local out
  out="$("$@" 2>&1)" || {
    printf '%s\n' "$out" >&2
    return 1
  }
}

# What `compose up` would change, from Compose itself: "<Container|Network|Volume> <name> <action>".
# A dry `create`, not `up`: a dry `up` waits forever for services that are not running yet.
plan() {
  local out
  if ! out="$(compose --dry-run create --no-build --remove-orphans 2>&1)"; then
    printf '%s\n' "$out" >&2
    echo "umai: the Compose dry run failed; nothing was changed" >&2
    exit 1
  fi
  awk '$1 == "DRY-RUN" && $2 == "MODE" { print $4, $5, $6 }' <<<"$out"
}

recreated() {  # $1 = service, stdin = plan: true when Compose would recreate its container
  awk -v svc="$1" '$1 == "Container" && $3 == "Recreate" {
    n = $2; sub(/^[0-9a-f]+_/, "", n); if (n ~ ("^umai-" svc "-[0-9]+$")) found = 1
  } END { exit !found }'
}

disruptive() {  # stdin = plan; stdout: the changes that cannot be rolled out side by side
  # A network or volume that does not exist yet is created ("Creating"): that is no change.
  awk '
    $1 == "Network" && $3 != "Creating" && $3 != "Created" { net[$2] = 1 }
    $1 == "Volume" && $3 != "Creating" && $3 != "Created" { vol[$2] = 1 }
    $1 == "Container" && $3 == "Recreate" {
      n = $2; sub(/^[0-9a-f]+_/, "", n)
      if (n ~ /^umai-(postgres|caddy)-[0-9]+$/) { sub(/^umai-/, "", n); sub(/-[0-9]+$/, "", n); svc[n] = 1 }
    }
    END {
      for (n in net) print "the network " n " changes (Compose would reconnect containers without their DNS names)"
      for (v in vol) print "the volume " v " changes"
      for (s in svc) print "the " s " container changes (it cannot run twice)"
    }'
}

# A rotated file a container mounts (the console's actor key, the signing key) needs a new
# container: a single-file bind mount keeps the old file.
mounted_changed() {  # $1 = service, then pairs: path in the container, file on the host
  local service="$1"
  shift
  while (($#)); do
    compose exec -T "$service" cat "$1" </dev/null 2>/dev/null | cmp -s - "$2" || return 0
    shift 2
  done
  return 1
}

needs_new() {  # $1 = service, $2 = plan
  recreated "$1" <<<"$2" && return 0
  case "$1" in
    policy) mounted_changed policy /run/secrets/signing.key "$RUN/signing.key" ;;
    console) mounted_changed console /run/secrets/actor.pem "$RUN/actor.pem" ;;  # rotated UMAI_CONSOLE_ACTOR_KEY
    *) return 1 ;;
  esac
}

# Caddy reads one directory, so a changed Caddyfile or console gate takes effect with a reload
# (a single-file mount would keep the old file until a restart). Both set CADDY_CHANGED. A new
# Caddyfile waits as Caddyfile.new until the running Caddy has validated it (caddy_take).
CADDY_CHANGED=""
caddy_files() {
  install -d -m 0755 "$RUN/caddy"
  if ! cmp -s "$HERE/Caddyfile" "$RUN/caddy/Caddyfile"; then
    install -m 0444 "$HERE/Caddyfile" "$RUN/caddy/Caddyfile.new"
    CADDY_CHANGED=1
  fi
}

caddy_validate() {
  [[ -e "$RUN/caddy/Caddyfile.new" ]] || return 0
  quiet compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile.new --adapter caddyfile || {
    rm -f "$RUN/caddy/Caddyfile.new"
    echo "umai: the shipped Caddyfile is invalid; nothing was changed" >&2
    exit 1
  }
}

caddy_take() { [[ ! -e "$RUN/caddy/Caddyfile.new" ]] || mv -f "$RUN/caddy/Caddyfile.new" "$RUN/caddy/Caddyfile"; }

# A reload switches configurations at once; requests that arrive at that instant can fail
# (measured: about five per reload under a continuous load of 16 clients). Only for a changed
# Caddyfile or gate, never for an image deploy.
caddy_reload() {
  quiet compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile ||
    quiet compose up -d --no-build --no-deps caddy  # not running (boot): starts with the files
}

wait_healthy() {  # $1 = container id
  local deadline=$((SECONDS + ${UMAI_HEALTH_TIMEOUT_S:-240})) state
  while :; do
    state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1")"
    case "$state" in
      healthy) return 0 ;;
      unhealthy | exited | dead) break ;;
    esac
    ((SECONDS < deadline)) || break
    sleep 2
  done
  echo "umai: $(name_of "$1") did not become healthy ($state)" >&2
  return 1
}

roll() {  # $1 = service: a new container beside the running one, then the old one stops
  local svc="$1" id new=""
  local -a old
  mapfile -t old < <(running "$svc")
  if ((${#old[@]})); then
    echo "umai: $svc: starting a new container beside the running one"
    quiet compose up -d --no-build --no-deps --no-recreate --scale "$svc=$((${#old[@]} + 1))" "$svc" || true
  else  # not running: nothing to keep serving
    quiet compose up -d --no-build --no-deps "$svc" || true
  fi
  for id in $(running "$svc"); do
    [[ " ${old[*]} " == *" $id "* ]] || new="$id"
  done
  if [[ -z "$new" ]] || ! wait_healthy "$new"; then
    # It may have answered a few requests already: stopped gracefully like an old container.
    [[ -z "$new" ]] || { docker stop "$new" >/dev/null; docker rm "$new" >/dev/null; }
    echo "umai: deploy stopped at $svc; the running containers keep serving" >&2
    exit 1
  fi
  if ((${#old[@]})); then
    # Graceful: refuses new connections (Caddy retries them on the new container) and finishes
    # the requests in flight, within the service's stop_grace_period (docker-compose.yml).
    docker stop "${old[@]}" >/dev/null
    docker rm "${old[@]}" >/dev/null
  fi
  echo "umai: $svc: replaced by $(name_of "$new")"
}

recreate_all() {  # every container and the network; volumes stay
  echo "umai: recreating the whole stack (the service is down until it is healthy again)"
  docker compose --project-directory "$HERE" --env-file "$CONFIG" --profile gateway \
    down --remove-orphans
  compose up -d --no-build --remove-orphans
}

up() {  # $1 = "--recreate" or nothing
  local changes current svc
  case "${1:-}" in
    "" | --recreate) ;;
    *) echo "usage: umai.sh up [--recreate]" >&2; exit 2 ;;
  esac
  secrets
  caddy_files
  if [[ "${1:-}" == "--recreate" ]]; then
    caddy_take
    recreate_all
    docker image prune -f >/dev/null
    return
  fi
  current="$(plan)"
  changes="$(disruptive <<<"$current")"
  if [[ -n "$changes" ]]; then
    {
      echo "umai: this change cannot be deployed without downtime:"
      sed 's/^/  - /' <<<"$changes"
      echo "  Run 'umai.sh up --recreate': it stops the whole stack and starts it again (README §4a)."
    } >&2
    exit 3
  fi
  if [[ -z "$(running caddy)" ]]; then
    # Nothing serves yet (first start, after a down, at boot): Compose starts everything. At boot
    # Caddy restarts before its files are back in /run/umai; restart it now rather than wait for
    # Docker's back-off.
    caddy_take
    compose up -d --no-build --remove-orphans
    if [[ -n "$CADDY_CHANGED" ]]; then quiet compose restart caddy; fi
  else
    if [[ -n "$CADDY_CHANGED" ]]; then caddy_validate && caddy_take; fi  # before anything else
    for svc in "${ROLLING[@]}"; do
      if needs_new "$svc" "$current"; then roll "$svc"; fi
    done
    # One-shot and newly enabled services, removed ones; nothing running is recreated here.
    if recreated seed <<<"$current"; then quiet compose rm -f -s seed; fi
    quiet compose up -d --no-build --no-recreate --remove-orphans
    if [[ -n "$CADDY_CHANGED" ]]; then
      echo "umai: the Caddyfile or the console gate changed: reloading Caddy"
      caddy_reload
    fi
  fi
  docker image prune -f >/dev/null
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

optional_secret() {  # like secret, but empty when the secret is not stored
  if [[ -n "$UMAI_KEY_VAULT" ]]; then
    curl -sf -H "Authorization: Bearer $KV_TOKEN" \
      "https://${UMAI_KEY_VAULT}.vault.azure.net/secrets/$1?api-version=7.4" | jq -r .value || true
  else
    local var="${1//-/_}"
    var="${var^^}"
    # shellcheck source=/dev/null
    (set -a; . "$SECRETS"; printf '%s' "${!var:-}")
  fi
}

# L1b decision provider (ADR-0040), for the engine and the gateway. Optional: without the key the
# engine has no decision provider, and a guardrail with decision policies answers its L1b part
# with the bundle's failure policy. UMAI_DECISION_PROVIDERS in config.env replaces the provider
# entry (e.g. https://eu.api.openai.com/v1 once OpenAI approved EU processing); the key belongs to
# the provider named "openai", the name the bundles use.
DEFAULT_DECISION_PROVIDERS='{"openai":{"adapter":"openai_decisions","base_url":"https://api.openai.com/v1","model":"gpt-6-luna"}}'

decision_env() {  # stdout: the engine.env lines, or nothing without a key
  local key
  key="$(optional_secret umai-openai-api-key)"
  if [[ -z "$key" ]]; then
    echo "umai: no UMAI_OPENAI_API_KEY stored: L1b has no decision provider" >&2
    return 0
  fi
  if [[ ! "$key" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "umai: UMAI_OPENAI_API_KEY has characters an OpenAI key does not have" >&2
    exit 1
  fi
  printf 'UMAI_DE_DECISION_PROVIDERS=%s\n' "${UMAI_DECISION_PROVIDERS:-$DEFAULT_DECISION_PROVIDERS}"
  printf 'UMAI_DE_DECISION_PROVIDER_KEYS={"openai":"%s"}\n' "$key"
}

# Billing (ADR-0044): iyzico subscriptions, sold by the policy service. UMAI_BILLING=iyzico in
# config.env with UMAI_IYZICO_MERCHANT_ID, UMAI_IYZICO_BASE_URL (sandbox by default) and
# UMAI_BILLING_PLAN_REFS (edition → pricing plan, from `billing.cli sync`); the keys via
# `umai.sh set-secret UMAI_IYZICO_API_KEY` and `… UMAI_IYZICO_SECRET_KEY`.
webhook_env() {  # stdout: the policy.env lines for outbound webhooks (ADR-0048) and console links
  printf 'UMAI_DE_CONSOLE_URL=https://%s
' "$UMAI_HOSTNAME"
  local key
  key="$(optional_secret umai-webhook-master-key)"
  # Without the key the policy service answers "webhooks unavailable" (Key Vault without it).
  [[ -z "$key" ]] || printf 'UMAI_DE_WEBHOOK_MASTER_KEY=%s
' "$key"
  unset key
}

billing_env() {  # stdout: the policy.env lines, or nothing while billing is off
  [[ "$UMAI_BILLING" == "iyzico" ]] || return 0
  local api secret_key
  api="$(optional_secret umai-iyzico-api-key)"
  secret_key="$(optional_secret umai-iyzico-secret-key)"
  if [[ -z "$api" || -z "$secret_key" || -z "${UMAI_IYZICO_MERCHANT_ID:-}" ]]; then
    echo "umai: UMAI_BILLING=iyzico needs UMAI_IYZICO_API_KEY, UMAI_IYZICO_SECRET_KEY (set-secret) and UMAI_IYZICO_MERCHANT_ID" >&2
    exit 1
  fi
  printf 'UMAI_DE_BILLING_PROVIDER=iyzico\n'
  printf 'UMAI_DE_IYZICO_BASE_URL=%s\n' "${UMAI_IYZICO_BASE_URL:-https://sandbox-api.iyzipay.com}"
  printf 'UMAI_DE_IYZICO_API_KEY=%s\nUMAI_DE_IYZICO_SECRET_KEY=%s\n' "$api" "$secret_key"
  printf 'UMAI_DE_IYZICO_MERCHANT_ID=%s\n' "$UMAI_IYZICO_MERCHANT_ID"
  printf 'UMAI_DE_BILLING_PLAN_REFS=%s\n' "${UMAI_BILLING_PLAN_REFS:-"{}"}"
  printf 'UMAI_DE_BILLING_RETURN_URL=https://%s/billing/return\n' "$UMAI_HOSTNAME"
}

new_hex() { openssl rand -hex 24; }
new_key32() { openssl rand -base64 32; }
# The console's P-256 key for on-behalf-of assertions (credentials 0.3): PKCS#8 PEM, base64 on one line.
new_actor_key() { openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 2>/dev/null | base64 -w0; }

ensure_local_secret() {  # $1 = name, $2 = generator (default new_hex); local-file mode: generate once
  [[ -n "$UMAI_KEY_VAULT" ]] && return 0
  grep -q "^$1=" "$SECRETS" 2>/dev/null && return 0
  local value  # assigned first: a failure inside printf's arguments would store an empty secret
  value="$("${2:-new_hex}")"
  [[ -n "$value" ]] || { echo "umai: could not generate $1; nothing stored" >&2; exit 1; }
  printf "%s='%s'\n" "$1" "$value" >>"$SECRETS"
  unset value
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
  fi | put "$RUN/caddy/console-gate.caddy.new" 0
  chmod 0444 "$RUN/caddy/console-gate.caddy.new"  # read by the caddy user inside its container
  if cmp -s "$RUN/caddy/console-gate.caddy.new" "$RUN/caddy/console-gate.caddy"; then
    rm -f "$RUN/caddy/console-gate.caddy.new"
  else  # whole, and applied by a reload (caddy_files)
    mv -f "$RUN/caddy/console-gate.caddy.new" "$RUN/caddy/console-gate.caddy"
    CADDY_CHANGED=1
  fi
}

# Gateway files. routes.env (not secret): the upstream allow-list, for the gateway and for the
# policy service that validates gateways. gateway.env: the provider keys of credential "secret"
# gateways, a JSON object name -> key, from Key Vault (umai-gateway-provider-keys) or
# `umai.sh set-secret UMAI_GATEWAY_PROVIDER_KEYS`; empty while the gateway is off. The
# applications directory belongs to the app user: the policy service writes it, the engine and
# the gateway read it.
gateway_env() {
  install -d -m 0755 -o "$APP_UID" -g "$APP_UID" "${UMAI_APPLICATIONS:-/opt/umai/applications}"
  if [[ -n "${UMAI_GATEWAY_UPSTREAM_ALLOWLIST:-}" ]]; then
    printf 'UMAI_DE_GATEWAY_UPSTREAM_ALLOWLIST=%s\n' "$UMAI_GATEWAY_UPSTREAM_ALLOWLIST"
  fi | put "$RUN/routes.env" 0
  if [[ "$UMAI_GATEWAY" != "true" ]]; then
    : | put "$RUN/gateway.env" 0
    return
  fi
  local keys
  keys="$(secret umai-gateway-provider-keys)"
  printf 'UMAI_DE_GATEWAY_PROVIDER_KEYS=%s\n' "$keys" | put "$RUN/gateway.env" 0
  unset keys
}

secrets() {
  : "${UMAI_HOSTNAME:?}" "${UMAI_OIDC_ISSUER:?set UMAI_OIDC_ISSUER in $CONFIG (console sign-in, README §3a)}"
  : "${UMAI_OIDC_CLIENT_ID:?set UMAI_OIDC_CLIENT_ID in $CONFIG (console sign-in, README §3a)}"
  mkdir -p "$RUN"
  chmod 0711 "$RUN"
  install -d -m 0755 "$RUN/caddy"  # Caddy's directory (caddy_files)
  if [[ -n "$UMAI_KEY_VAULT" ]]; then KV_TOKEN="$(imds_token https://vault.azure.net)"; fi
  ensure_local_secret UMAI_DB_OWNER_PASSWORD  # added after the first release: generated once
  ensure_local_secret UMAI_DB_APP_PASSWORD
  ensure_local_secret UMAI_TENANT_HMAC_MASTER_KEY new_key32  # organizations (ADR-0032 amendment)
  ensure_local_secret UMAI_SESSION_SECRET                    # console session cookie key
  ensure_local_secret UMAI_CONSOLE_ACTOR_KEY new_actor_key   # console -> policy service assertions
  ensure_local_secret UMAI_WEBHOOK_MASTER_KEY new_key32      # webhook secrets, URL encryption (ADR-0048)
  local api control hmac hmac_master db_owner db_app actor_public session oidc_secret decision
  db_owner="$(secret umai-db-owner-password)"
  db_app="$(secret umai-db-app-password)"
  api="$(secret umai-api-token)"
  control="$(secret umai-control-token)"
  hmac="$(secret umai-tenant-hmac-keys)"
  hmac_master="$(secret umai-tenant-hmac-master-key)"
  session="$(secret umai-session-secret)"
  oidc_secret="$(secret umai-oidc-client-secret)"  # README §3a: set-secret from Terraform's output
  secret umai-signing-key | put "$RUN/signing.key" "$APP_UID"
  decision="$(decision_env)"  # before engine.env is written: a bad key stops here
  {
    printf 'UMAI_DE_API_TOKEN=%s\nUMAI_DE_TENANT_HMAC_KEYS=%s\nUMAI_DE_TENANT_HMAC_MASTER_KEY=%s\n' \
      "$api" "$hmac" "$hmac_master"
    [[ -z "$decision" ]] || printf '%s\n' "$decision"
    # Records (ADR-0041): the engine writes decision events and usage with the application
    # role (INSERT and SELECT on the record tables only).
    printf 'UMAI_DE_DATABASE_URL=postgresql+asyncpg://umai_app:%s@postgres:5432/umai\n' "$db_app"
  } | put "$RUN/engine.env" 0
  secret umai-console-actor-key | base64 -d | put "$RUN/actor.pem" "$CONSOLE_UID"
  actor_public="$(openssl pkey -in "$RUN/actor.pem" -pubout -outform DER | base64 -w0)"
  printf 'UMAI_DE_CONTROL_API_TOKEN=%s\n' "$control" | put "$RUN/policy.env" 0
  {
    printf 'UMAI_DE_DATABASE_URL=postgresql+asyncpg://umai_app:%s@postgres:5432/umai\n' "$db_app"
    printf 'UMAI_DE_DATABASE_ADMIN_URL=postgresql+asyncpg://umai_owner:%s@postgres:5432/umai\n' "$db_owner"
    printf 'UMAI_DE_ACTOR_PUBLIC_KEYS={"%s":"%s"}\n' "$UMAI_ACTOR_KEY_ID" "$actor_public"
    # Plans: the policy service writes each tenant's limits into its snapshot (ADR-0043).
    if [[ "$UMAI_PLANS" == "true" ]]; then printf 'UMAI_DE_PLANS_FILE=/app/plans.yaml\n'; fi
    billing_env
    webhook_env
  } >>"$RUN/policy.env"
  printf 'POSTGRES_PASSWORD=%s\nUMAI_DB_APP_PASSWORD=%s\n' "$db_owner" "$db_app" |
    put "$RUN/postgres.env" 0
  {
    printf 'UMAI_ENGINE_TOKEN=%s\nUMAI_CONTROL_TOKEN=%s\n' "$api" "$control"
    printf 'UMAI_SESSION_SECRET=%s\nUMAI_OIDC_CLIENT_SECRET=%s\n' "$session" "$oidc_secret"
  } | put "$RUN/console.env" 0
  gateway_env
  console_gate
  unset KV_TOKEN api control hmac hmac_master db_owner db_app session oidc_secret decision
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
      "$(docker run --rm caddy:2.11.7-alpine caddy hash-password --plaintext "$password")"
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

# ------------------------------------------------------------------ backups (ADR-0032 §7, ADR-0052)
APPLICATIONS="${UMAI_APPLICATIONS:-/opt/umai/applications}"

# Archive: p/ the policy volume, a/ the applications directory (since ADR-0052), db/umai.sql.gz a
# logical dump of the tenant store with its GRANTs (the application role's rights live in the
# migrations, so a dump without them restores a store the services cannot read).
make_archive() {  # $1 = archive file, in a directory only root can read
  local tmp
  tmp="$(mktemp -d /var/tmp/umai-dump.XXXXXX)"
  compose exec -T postgres pg_dump -U umai_owner -d umai --no-owner | gzip -1 >"$tmp/umai.sql.gz"
  docker run --rm -v umai_policies:/p:ro -v "$APPLICATIONS:/a:ro" -v "$tmp:/db:ro" \
    -v "$(dirname "$1"):/out" alpine tar -czf "/out/$(basename "$1")" -C / p a db/umai.sql.gz
  rm -rf "$tmp"
}

backup() {
  local stamp name work url token
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  name="umai-backup-$stamp.tar.gz"
  work="$(mktemp -d /var/tmp/umai-backup.XXXXXX)"
  make_archive "$work/$name"
  url="https://${UMAI_BACKUP_ACCOUNT}.blob.core.windows.net/backups/$name"
  if [[ -n "$UMAI_KEY_VAULT" ]]; then  # managed identity with a blob data role
    token="$(imds_token https://storage.azure.com/)"
    curl -sf -X PUT -T "$work/$name" -H "Authorization: Bearer $token" \
      -H "x-ms-version: 2023-11-03" -H "x-ms-blob-type: BlockBlob" "$url"
  else                                  # write-only container SAS from secrets.env
    curl -sf -X PUT -T "$work/$name" -H "x-ms-blob-type: BlockBlob" "$url?$(secret umai-backup-sas)"
  fi
  rm -rf "$work"
  echo "backup: uploaded $name"
}

# A blob from Blob Storage onto this host (never elsewhere): managed identity in Key Vault mode,
# else a read-only SAS on stdin, because the stored SAS can only write (README §4b).
fetch_backup() {  # $1 = blob name, $2 = target file
  local url sas token
  [[ "$1" =~ ^umai-backup-[0-9]{8}T[0-9]{6}Z\.tar\.gz$ ]] || {
    echo "restore-test: '$1' is neither a file here nor a backup blob name (umai-backup-<UTC stamp>.tar.gz)" >&2
    exit 2
  }
  url="https://${UMAI_BACKUP_ACCOUNT}.blob.core.windows.net/backups/$1"
  if [[ -n "$UMAI_KEY_VAULT" ]]; then
    token="$(imds_token https://storage.azure.com/)"
    curl -sf -o "$2" -H "Authorization: Bearer $token" -H "x-ms-version: 2023-11-03" "$url"
  else
    IFS= read -r sas || true
    [[ -n "$sas" ]] || {
      echo "restore-test: pipe a read-only SAS for the blob on stdin (README §4b)" >&2
      exit 2
    }
    curl -sf -o "$2" "$url?${sas#\?}" || {
      echo "restore-test: download of $1 failed" >&2
      exit 1
    }
    unset sas
  fi
}

dump_counts() {  # stdin = plain pg_dump; stdout: "<table> <rows>" for every table it holds data for
  awk '
    /^COPY public\./ { t = $2; sub(/^public\./, "", t); gsub(/"/, "", t); n = 0; copying = 1; next }
    copying && $0 == "\\." { print t, n; copying = 0; next }
    copying { n++ }'
}

# ------------------------------------------------------------------ restore test (S0-14, ADR-0052)
# Restores a backup into a scratch Compose project (restore-test.yml: own network without a way
# out, own volumes, no published ports), checks it and removes it. Prints names, counts and
# durations only: the backup holds real sign-in e-mails and never leaves this host.
restore_test() {  # $1 = archive file on this host, a blob name, or nothing: a backup taken now
  local work project start mark archive backup_rev image_rev failed="" grants table expected restored
  local tenant
  start=$SECONDS mark=$SECONDS
  work="$(mktemp -d /var/tmp/umai-restore.XXXXXX)"
  project="umai-restore-$(date -u +%Y%m%dt%H%M%S)"
  RESTORE_DIR="$work"
  RESTORE_OWNER_PASSWORD="$(new_hex)" RESTORE_APP_PASSWORD="$(new_hex)"
  RESTORE_CONTROL_TOKEN="$(new_hex)" RESTORE_API_TOKEN="$(new_hex)" RESTORE_HMAC_KEY="$(new_key32)"
  export RESTORE_DIR RESTORE_OWNER_PASSWORD RESTORE_APP_PASSWORD RESTORE_CONTROL_TOKEN \
    RESTORE_API_TOKEN RESTORE_HMAC_KEY
  # shellcheck disable=SC2064  # expanded now: the project and directory of this run
  trap "docker compose -p '$project' -f '$HERE/restore-test.yml' --project-directory '$HERE' \
    down -v --remove-orphans >/dev/null 2>&1 || true; rm -rf '$work'" EXIT
  scratch() { docker compose -p "$project" -f "$HERE/restore-test.yml" --project-directory "$HERE" "$@"; }
  phase() {
    printf 'restore-test: %-34s %5d s\n' "$1" $((SECONDS - mark))
    mark=$SECONDS
  }
  sql() {  # stdin closed: inside a read loop, exec would take the loop's input
    scratch exec -T postgres psql -U umai_owner -d "${2:-umai}" -tAX -v ON_ERROR_STOP=1 -c "$1" </dev/null
  }
  check() {  # $1 = what, then the command
    local what="$1"
    shift
    if "$@"; then echo "restore-test: ok   $what"; else echo "restore-test: FAIL $what"; failed=1; fi
  }

  if [[ -z "${1:-}" ]]; then
    archive="$work/backup.tar.gz"
    make_archive "$archive"
    phase "backup taken now ($(du -h "$archive" | cut -f1))"
  elif [[ -f "$1" ]]; then
    archive="$1"
  else
    archive="$work/backup.tar.gz"
    fetch_backup "$1" "$archive"
    phase "download ($(du -h "$archive" | cut -f1))"
  fi
  tar -xzf "$archive" -C "$work"
  [[ -f "$work/db/umai.sql.gz" && -d "$work/p" ]] || {
    echo "restore-test: FAIL the archive has no db/umai.sql.gz or p/" >&2
    exit 1
  }
  gunzip -c "$work/db/umai.sql.gz" | dump_counts >"$work/expected"
  grants="$(gunzip -c "$work/db/umai.sql.gz" | grep -c '^GRANT ' || true)"
  phase "unpack, count the dump's rows"

  quiet scratch up -d --wait postgres
  gunzip -c "$work/db/umai.sql.gz" |
    scratch exec -T postgres psql -U umai_owner -d umai -q -v ON_ERROR_STOP=1 >/dev/null 2>"$work/psql.log" || {
    grep -E 'ERROR' "$work/psql.log" | head -n 5 >&2  # ERROR lines only: DETAIL may quote a row
    echo "restore-test: FAIL loading the dump" >&2
    exit 1
  }
  phase "database restored ($(wc -l <"$work/expected") tables)"

  echo "restore-test: rows per table (dump = restored)"
  while read -r table expected; do
    restored="$(sql "SELECT count(*) FROM public.\"$table\"")"
    printf 'restore-test:   %-24s %9s %s\n' "$table" "$expected" \
      "$([[ "$restored" == "$expected" ]] && echo "=" || echo "!= $restored")"
    [[ "$restored" == "$expected" ]] || failed=1
  done <"$work/expected"

  backup_rev="$(sql 'SELECT version_num FROM alembic_version')"
  image_rev="$(scratch run --rm --no-deps -T policy python -c 'from alembic.script import ScriptDirectory
from umai_decision_engine.db.migrate import alembic_config
print(ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head())' 2>"$work/run.log")"
  echo "restore-test: migration of the backup $backup_rev, of the image $image_rev"
  if ((grants == 0)); then
    # A backup from before ADR-0052 (dumped with --no-privileges): the application role gets the
    # rights its migrations grant, copied from a store migrated from scratch.
    echo "restore-test: the dump has no GRANTs (an older backup); granting what the migrations grant"
    sql 'CREATE DATABASE umai_ref' >/dev/null
    local admin="postgresql+asyncpg://umai_owner:$RESTORE_OWNER_PASSWORD@postgres:5432"
    quiet scratch run --rm --no-deps -T policy python -m umai_decision_engine.db.migrate "$admin/umai"
    quiet scratch run --rm --no-deps -T policy python -m umai_decision_engine.db.migrate "$admin/umai_ref"
    scratch exec -T postgres pg_dump -U umai_owner -d umai_ref --schema-only | grep -E '^(GRANT|REVOKE) ' |
      scratch exec -T postgres psql -U umai_owner -d umai -q -v ON_ERROR_STOP=1 >/dev/null
    sql 'DROP DATABASE umai_ref' >/dev/null
  fi
  phase "row counts and migrations"

  quiet scratch run --rm --no-deps -T files
  echo "restore-test: policy volume $(find "$work/p" -name bundle.yaml | wc -l) bundle versions," \
    "applications $(find "$work/a" -name '*.yaml' 2>/dev/null | wc -l) files"
  quiet scratch up -d --wait --wait-timeout "${UMAI_HEALTH_TIMEOUT_S:-240}" policy engine || {
    scratch logs --no-log-prefix --tail 5 policy engine >&2  # logs carry no content (ADR-0005)
    echo "restore-test: FAIL the policy service or the engine did not become healthy" >&2
    exit 1
  }
  phase "policy service and engine started"

  check "migrations at the image's head after start" \
    test "$(sql 'SELECT version_num FROM alembic_version')" = "$image_rev"
  check "policy service reads the tenants as the application role" \
    scratch exec -T policy python -c 'import os, urllib.request
r = urllib.request.Request("http://127.0.0.1:9200/v1/tenants",
    headers={"Authorization": "Bearer " + os.environ["UMAI_DE_CONTROL_API_TOKEN"]})
n = len(__import__("json").load(urllib.request.urlopen(r, timeout=10))["tenants"])
print("restore-test:      ", n, "tenants")'
  tenant="$(sql 'SELECT t.slug FROM tenants t JOIN api_keys k ON k.tenant_id = t.id GROUP BY t.slug ORDER BY count(*) DESC LIMIT 1')"
  if [[ -n "$tenant" ]]; then
    check "policy service reads a tenant's keys through row-level security" \
      scratch exec -T -e "T=$tenant" policy python -c 'import os, urllib.request
r = urllib.request.Request("http://127.0.0.1:9200/v1/tenants/" + os.environ["T"] + "/keys",
    headers={"Authorization": "Bearer " + os.environ["UMAI_DE_CONTROL_API_TOKEN"]})
urllib.request.urlopen(r, timeout=10)'
  fi
  check "engine loads the restored bundles without errors" \
    scratch exec -T engine python -c 'import json, urllib.request
s = json.load(urllib.request.urlopen("http://127.0.0.1:9100/readyz", timeout=10))
print("restore-test:      ", s["bundles"], "bundles,", s["load_errors"], "load errors")
raise SystemExit(s["load_errors"] != 0 or s["bundles"] == 0)'
  phase "checks"

  quiet scratch down -v --remove-orphans
  phase "scratch project removed"
  if [[ -n "$failed" ]]; then
    echo "restore-test: FAILED after $((SECONDS - start)) s"
    exit 1
  fi
  echo "restore-test: PASSED in $((SECONDS - start)) s (restore to healthy services: see the phases above)"
}

# Sourced (tests): define the functions only.
[[ "${BASH_SOURCE[0]}" == "$0" ]] || return 0

case "${1:-}" in
  init) init ;;
  secrets) secrets ;;
  up) up "${2:-}" ;;
  backup) backup ;;
  restore-test) restore_test "${2:-}" ;;
  status) compose ps ;;
  set-secret) set_secret "${2:-}" ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
