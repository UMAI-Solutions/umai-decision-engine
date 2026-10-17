#!/usr/bin/env bash
# Build both images on the operator workstation and ship them to the VM over SSH (ADR-0032).
# The VM holds no repository credentials: it receives images and the deploy/vm files only.
#
#   deploy/vm/ship.sh <ssh target> [console repo path]
#   e.g. deploy/vm/ship.sh umaiops@umai-beta.westeurope.cloudapp.azure.com ../umai-control-center
#
# Needs docker and ssh locally (Git Bash on Windows works). SSH_KEY selects the identity file.
set -euo pipefail

target="${1:?ssh target, e.g. umaiops@host}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine_repo="$(cd "$here/../.." && pwd)"
console_repo="$(cd "${2:-$engine_repo/../umai-control-center}" && pwd)"
ssh_opts=(-o ConnectTimeout=20)
[[ -n "${SSH_KEY:-}" ]] && ssh_opts+=(-i "$SSH_KEY")
remote_dir=/opt/umai/src/umai-decision-engine/deploy

engine_rev="$(git -C "$engine_repo" rev-parse --short HEAD)"
console_rev="$(git -C "$console_repo" rev-parse --short HEAD)"
echo "ship: engine $engine_rev, console $console_rev -> $target"

docker build --platform linux/amd64 -t umai-decision-engine:current \
  --label "org.opencontainers.image.revision=$engine_rev" "$engine_repo"
docker build --platform linux/amd64 -t umai-control-center:current \
  --label "org.opencontainers.image.revision=$console_rev" "$console_repo"

docker save umai-decision-engine:current umai-control-center:current | gzip -1 |
  ssh "${ssh_opts[@]}" "$target" 'gunzip | sudo docker load'

tar -C "$here/.." -cf - postgres-init.sql vm/docker-compose.yml vm/Caddyfile vm/seed-policies.sh \
  vm/postgres-init.sh vm/umai.sh vm/umai.service vm/umai-backup.service vm/umai-backup.timer \
  vm/config.env.example |
  ssh "${ssh_opts[@]}" "$target" \
    "sudo mkdir -p $remote_dir && sudo tar -C $remote_dir -xf - && sudo chmod +x $remote_dir/vm/umai.sh $remote_dir/vm/postgres-init.sh"

ssh "${ssh_opts[@]}" "$target" "sudo test -f /opt/umai/secrets.env -o -n \"\$(sudo grep -s '^UMAI_KEY_VAULT=.' /opt/umai/config.env)\"" &&
  ssh "${ssh_opts[@]}" "$target" "sudo $remote_dir/vm/umai.sh up" ||
  echo "ship: images and files delivered; finish first-time setup (README §3), then run umai.sh up"
