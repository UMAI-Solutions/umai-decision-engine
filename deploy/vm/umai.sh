#!/usr/bin/env bash
# Operator entry point for the single-VM stack (ADR-0032). Runs on the VM as root.
#
#   umai.sh init      generate the local secrets file (no Key Vault mode) and the signing key
#   umai.sh secrets   write the runtime secrets into /run/umai (tmpfs)
#   umai.sh up        secrets + start the shipped images (also used by umai.service at boot)
#   umai.sh backup    policy volume + policy-service database -> Blob Storage
#   umai.sh status    container status and health
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

# shellcheck source=/dev/null
set -a; . "$CONFIG"; set +a
: "${UMAI_BACKUP_ACCOUNT:?}" "${UMAI_CONSOLE_USER:?}"
UMAI_KEY_VAULT="${UMAI_KEY_VAULT:-}"

compose() { docker compose --project-directory "$HERE" --env-file "$CONFIG" "$@"; }

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

put() {  # $1 = file, $2 = owner uid, stdin = content
  install -m 0400 -o "$2" -g "$2" /dev/null "$1"
  cat >"$1"
}

secrets() {
  mkdir -p "$RUN"
  chmod 0711 "$RUN"
  if [[ -n "$UMAI_KEY_VAULT" ]]; then KV_TOKEN="$(imds_token https://vault.azure.net)"; fi
  local api control hmac
  api="$(secret umai-api-token)"
  control="$(secret umai-control-token)"
  hmac="$(secret umai-tenant-hmac-keys)"
  secret umai-signing-key | put "$RUN/signing.key" "$APP_UID"
  printf 'UMAI_DE_API_TOKEN=%s\nUMAI_DE_TENANT_HMAC_KEYS=%s\n' "$api" "$hmac" |
    put "$RUN/engine.env" 0
  printf 'UMAI_DE_CONTROL_API_TOKEN=%s\n' "$control" | put "$RUN/policy.env" 0
  printf 'UMAI_ENGINE_TOKEN=%s\nUMAI_CONTROL_TOKEN=%s\n' "$api" "$control" |
    put "$RUN/console.env" 0
  printf '%s %s\n' "$UMAI_CONSOLE_USER" "$(secret umai-console-password-hash)" |
    put "$RUN/console-auth.caddy" 0
  chmod 0444 "$RUN/console-auth.caddy"  # read by the caddy user inside its container
  unset KV_TOKEN
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

backup() {
  local stamp name file url token
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  name="umai-backup-$stamp.tar.gz"
  file="/var/tmp/$name"
  # Consistent SQLite copy through the policy-service container, then both volumes.
  compose exec -T policy python -c \
    "import sqlite3;s=sqlite3.connect('/data/control/control.sqlite');d=sqlite3.connect('/data/control/backup.sqlite');s.backup(d);d.close()"
  docker run --rm -v umai_policies:/p:ro -v umai_control:/c:ro -v /var/tmp:/out alpine \
    tar -czf "/out/$name" -C / p c/backup.sqlite
  compose exec -T policy rm -f /data/control/backup.sqlite
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
  up) secrets; compose up -d --no-build --remove-orphans; docker image prune -f >/dev/null ;;
  backup) backup ;;
  status) compose ps ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
