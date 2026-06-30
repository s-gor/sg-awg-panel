#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="${SG_AWG_PROJECT_DIR:-/opt/sg-awg-panel}"
ENV_FILE="${SG_AWG_ENV_FILE:-/etc/sg-awg-panel/web.env}"
SYSTEMD_DIR="${SG_AWG_SYSTEMD_DIR:-/etc/systemd/system}"
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ -x "$PROJECT_DIR/.venv/bin/python" ]] || { echo "Missing panel virtualenv" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "Missing panel environment" >&2; exit 1; }
mkdir -p "$SYSTEMD_DIR"
cat > $SYSTEMD_DIR/sg-awg-traffic-schedule.service <<UNIT
[Unit]
Description=SG-AWG-Panel scheduled Traffic Rules check
After=network-online.target sg-awg-traffic.service
Wants=network-online.target
[Service]
Type=oneshot
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$PROJECT_DIR/.venv/bin/python -m awgpanel traffic-tick
User=root
Group=root
Nice=10
UNIT
cat > $SYSTEMD_DIR/sg-awg-traffic-schedule.timer <<'UNIT'
[Unit]
Description=Check SG-AWG-Panel Traffic Rules schedules every minute
[Timer]
OnBootSec=2min
OnUnitActiveSec=60s
AccuracySec=10s
Unit=sg-awg-traffic-schedule.service
[Install]
WantedBy=timers.target
UNIT
rm -f $SYSTEMD_DIR/sg-awg-traffic-lists.service $SYSTEMD_DIR/sg-awg-traffic-lists.timer
chmod 0644 $SYSTEMD_DIR/sg-awg-traffic-schedule.service $SYSTEMD_DIR/sg-awg-traffic-schedule.timer
systemctl daemon-reload
systemctl disable --now sg-awg-traffic-lists.timer sg-awg-traffic-lists.service 2>/dev/null || true
systemctl enable --now sg-awg-traffic-schedule.timer >/dev/null
