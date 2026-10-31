#!/bin/sh
# Installs the UMAI container health reporter on a single-VM host (ADR-0050, deploy/azure/README.md
# §5). Run once as root by the managed run command in monitoring/environment.bicep; running it again
# rewrites the same three files. Every minute the reporter writes one syslog line per container of
# the Compose project "umai" (daemon.info, tag umai-health), which the Azure Monitor Agent sends to
# Log Analytics, where the "containers" alert reads it:
#
#   container=umai-engine-1 state=running health=healthy restarts=0
#   report containers=6
#
# It reads Docker's state only: no container output, no environment, no content (ADR-0005).
# It also lets the agent (user azuremonitoragent) read the containers' log files, which Docker
# keeps root-only; a new container or a rotated file is readable within a minute.
set -eu

command -v setfacl >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q acl

cat >/usr/local/sbin/umai-health-report <<'EOF'
#!/bin/sh
# One line per container of the Compose project "umai", then a summary line, so that silence
# means the reporter, Docker or the VM stopped.
project=com.docker.compose.project=umai
docker ps -a --filter "label=$project" --format '{{.Names}}' | while read -r name; do
  docker inspect -f 'container={{.Name}} state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}}' "$name"
done | sed 's#^container=/#container=#' | logger -p daemon.info -t umai-health
echo "report containers=$(docker ps -q --filter "label=$project" | wc -l)" | logger -p daemon.info -t umai-health

# Read access for the monitoring agent to Docker's json-file logs (ADR-0051): traverse the
# Docker root, list the container directories, read the log files; a default ACL covers files
# Docker creates later (rotation). Nothing else under /var/lib/docker becomes readable.
if id azuremonitoragent >/dev/null 2>&1; then
  c=/var/lib/docker/containers
  setfacl -m u:azuremonitoragent:x /var/lib/docker
  setfacl -m u:azuremonitoragent:rx "$c"
  for d in "$c"/*/; do
    [ -d "$d" ] || continue
    setfacl -m u:azuremonitoragent:rx -m d:u:azuremonitoragent:r "$d"
    for f in "$d"*-json.log*; do [ -f "$f" ] && setfacl -m u:azuremonitoragent:r "$f"; done
  done
fi
EOF
chmod 0755 /usr/local/sbin/umai-health-report

cat >/etc/systemd/system/umai-health-report.service <<'EOF'
[Unit]
Description=UMAI container health line to syslog (Azure Monitor, deploy/azure/README.md)
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/umai-health-report
EOF

cat >/etc/systemd/system/umai-health-report.timer <<'EOF'
[Unit]
Description=UMAI container health report every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=10s

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now umai-health-report.timer
echo "health-report: installed, $(systemctl is-active umai-health-report.timer)"
