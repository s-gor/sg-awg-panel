#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${SG_AWG_PROJECT_DIR:-/opt/sg-awg-panel}"
SYSTEMD_DIR="${SG_AWG_SYSTEMD_DIR:-/etc/systemd/system}"
SERVICE_FILE="$SYSTEMD_DIR/sg-awg-traffic.service"

[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ -x "$PROJECT_DIR/.venv/bin/python" ]] || { echo "Missing panel virtualenv" >&2; exit 1; }
mkdir -p "$SYSTEMD_DIR"

cat > "$SERVICE_FILE" <<UNIT
[Unit]
Description=SG-AWG-Panel policy traffic and outbound tunnels
After=network-online.target sg-awg-server.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=/etc/sg-awg-panel/web.env
ExecStart=$PROJECT_DIR/.venv/bin/python -m awgpanel apply-traffic
ExecReload=$PROJECT_DIR/.venv/bin/python -m awgpanel apply-traffic
ExecStop=$PROJECT_DIR/.venv/bin/python -m awgpanel clear-traffic
TimeoutStartSec=120
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable sg-awg-traffic.service >/dev/null
