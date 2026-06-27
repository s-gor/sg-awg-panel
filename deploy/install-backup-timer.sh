#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="/opt/sg-awg-panel"
ENV_FILE="/etc/sg-awg-panel/web.env"
SERVICE_FILE="/etc/systemd/system/sg-awg-backup.service"
TIMER_FILE="/etc/systemd/system/sg-awg-backup.timer"

[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ -x "$PROJECT_DIR/.venv/bin/python" ]] || { echo "Panel virtual environment not found" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE" >&2; exit 1; }

cat > "$SERVICE_FILE" <<UNIT
[Unit]
Description=SG-AWG-Panel automatic backup
After=sg-awg-panel.service

[Service]
Type=oneshot
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$PROJECT_DIR/.venv/bin/python -m awgpanel backup
User=root
Group=root
UMask=0077
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=/var/lib/sg-awg-panel /etc/amnezia/amneziawg
UNIT

cat > "$TIMER_FILE" <<'UNIT'
[Unit]
Description=Daily SG-AWG-Panel backup

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=30m
Unit=sg-awg-backup.service

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now sg-awg-backup.timer >/dev/null
