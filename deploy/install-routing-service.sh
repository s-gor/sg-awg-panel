#!/usr/bin/env bash
# One-time compatibility bridge used only by an updater process that started
# from SG-AWG-Panel Beta 4. RC3 itself uses Traffic Rules.
set -Eeuo pipefail

PROJECT_DIR="${SG_AWG_PROJECT_DIR:-/opt/sg-awg-panel}"
SYSTEMD_DIR="${SG_AWG_SYSTEMD_DIR:-/etc/systemd/system}"
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }

bash "$PROJECT_DIR/deploy/install-traffic-service.sh"

cat > "$SYSTEMD_DIR/sg-awg-routing.service" <<'UNIT'
[Unit]
Description=Temporary SG-AWG-Panel Beta 4 update bridge
After=network-online.target sg-awg-panel.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/systemctl restart sg-awg-traffic.service
ExecStart=/bin/systemctl is-active --quiet sg-awg-traffic.service
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT

chmod 0644 "$SYSTEMD_DIR/sg-awg-routing.service"
systemctl daemon-reload
systemctl enable sg-awg-routing.service >/dev/null
