#!/usr/bin/env bash
# Operator entry point for the single-VM stack (ADR-0032). Runs on the VM as root.
#
#   umai.sh secrets   fetch secrets from Key Vault (managed identity) into /run/umai (tmpfs)
#   umai.sh up        secrets + build + start (also used by umai.service at boot)
#   umai.sh update    git pull both repositories, rebuild, restart
#   umai.sh backup    policy volume + policy-service database -> Blob Storage
#   umai.sh status    container status and health
#
# Non-secret settings: /opt/umai/config.env (see config.env.example). Secrets never touch the
# disk: Key Vault -> /run/umai, which is cleared at every reboot.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG=/opt/umai/config.env
RUN=/run/umai
APP_UID=10001     # user in the engine image (Dockerfile)
CONSOLE_UID=1001  # user in the console image

# shellcheck source=/dev/null
set -a; . "$CONFIG"; set +a
: "${UMAI_KEY_VAULT:?}" "${UMAI_BACKUP_ACCOUNT:?}" "${UMAI_CONSOLE_USER:?}"

compose() { docker compose --project-directory "$HERE" --env-file "$CONFIG" "$@"; }

imds_token() {  # $1 = resource
  curl -sf -H Metadata:true \
    "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=$1" |
    jq -r .access_token
}

kv() {  # $1 = secret name
  curl -sf -H "Authorization: Bearer $KV_TOKEN" \
    "https://${UMAI_KEY_VAULT}.vault.azure.net/secrets/$1?api-version=7.4" | jq -r .value
}

put() {  # $1 = file, $2 = owner uid, stdin = content
  install -m 0400 -o "$2" -g "$2" /dev/null "$1"
  cat >"$1"
}

secrets() {
  mkdir -p "$RUN"; chmod 0711 "$RUN"
  KV_TOKEN="$(imds_token https://vault.azure.net)"
  local api control hmac
  api="$(kv umai-api-token)"
  control="$(kv umai-control-token)"
  hmac="$(kv umai-tenant-hmac-keys)"
  kv umai-signing-key | put "$RUN/signing.key" "$APP_UID"
  printf 'UMAI_DE_API_TOKEN=%s\nUMAI_DE_TENANT_HMAC_KEYS=%s\n' "$api" "$hmac" |
    put "$RUN/engine.env" 0
  printf 'UMAI_DE_CONTROL_API_TOKEN=%s\n' "$control" | put "$RUN/policy.env" 0
  printf 'UMAI_ENGINE_TOKEN=%s\nUMAI_CONTROL_TOKEN=%s\n' "$api" "$control" |
    put "$RUN/console.env" 0
  printf '%s %s\n' "$UMAI_CONSOLE_USER" "$(kv umai-console-password-hash)" |
    put "$RUN/console-auth.caddy" 0
  chmod 0444 "$RUN/console-auth.caddy"  # read by the caddy user inside its container
  unset KV_TOKEN
  echo "secrets: written to $RUN"
}

backup() {
  local stamp file token
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  file="/var/tmp/umai-backup-$stamp.tar.gz"
  # Consistent SQLite copy through the policy-service container, then both volumes.
  compose exec -T policy python -c \
    "import sqlite3;s=sqlite3.connect('/data/control/control.sqlite');d=sqlite3.connect('/data/control/backup.sqlite');s.backup(d);d.close()"
  docker run --rm -v umai_policies:/p:ro -v umai_control:/c:ro -v /var/tmp:/out alpine \
    tar -czf "/out/$(basename "$file")" -C / p c/backup.sqlite
  compose exec -T policy rm -f /data/control/backup.sqlite
  token="$(imds_token https://storage.azure.com/)"
  curl -sf -X PUT -T "$file" \
    -H "Authorization: Bearer $token" -H "x-ms-version: 2023-11-03" -H "x-ms-blob-type: BlockBlob" \
    "https://${UMAI_BACKUP_ACCOUNT}.blob.core.windows.net/backups/$(basename "$file")"
  rm -f "$file"
  echo "backup: uploaded $(basename "$file")"
}

case "${1:-}" in
  secrets) secrets ;;
  up) secrets; compose up -d --build --remove-orphans ;;
  update)
    git -C "$HERE/../.." pull --ff-only
    git -C "${UMAI_CONSOLE_SRC:-/opt/umai/src/umai-control-center}" pull --ff-only
    secrets; compose up -d --build --remove-orphans; docker image prune -f ;;
  backup) backup ;;
  status) compose ps ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
