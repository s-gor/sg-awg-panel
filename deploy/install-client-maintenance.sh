#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="/opt/sg-awg-panel"
ENV_FILE="/etc/sg-awg-panel/web.env"

[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ -x "$PROJECT_DIR/.venv/bin/python" ]] || { echo "Missing panel virtualenv" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "Missing panel environment" >&2; exit 1; }

cat > /etc/systemd/system/sg-awg-clients-maintenance.service <<UNIT
[Unit]
Description=SG-AWG-Panel client expiration maintenance
After=network-online.target sg-awg-panel.service sg-awg-server.service sg-awg-traffic.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$PROJECT_DIR/.venv/bin/python -m awgpanel clients-tick
User=root
Group=root
Nice=10
UNIT

cat > /etc/systemd/system/sg-awg-clients-maintenance.timer <<'UNIT'
[Unit]
Description=Check SG-AWG-Panel client expiration every minute

[Timer]
OnBootSec=90s
OnUnitActiveSec=60s
AccuracySec=10s
Persistent=true
Unit=sg-awg-clients-maintenance.service

[Install]
WantedBy=timers.target
UNIT

chmod 0644 \
  /etc/systemd/system/sg-awg-clients-maintenance.service \
  /etc/systemd/system/sg-awg-clients-maintenance.timer
systemctl daemon-reload
systemctl enable --now sg-awg-clients-maintenance.timer >/dev/null
