#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }

RECOVERY_SCRIPT="/opt/sg-awg-panel/deploy/recover-after-boot.sh"
[[ -f "$RECOVERY_SCRIPT" ]] || { echo "Missing $RECOVERY_SCRIPT" >&2; exit 1; }
chmod 0755 "$RECOVERY_SCRIPT"

cat > /etc/systemd/system/sg-awg-recovery.service <<'UNIT'
[Unit]
Description=SG-AWG-Panel recovery after reboot
After=network-online.target sg-awg-panel.service nginx.service sg-awg-traffic.service
Wants=network-online.target sg-awg-panel.service nginx.service sg-awg-traffic.service

[Service]
Type=oneshot
ExecStart=/usr/bin/bash /opt/sg-awg-panel/deploy/recover-after-boot.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable sg-awg-recovery.service >/dev/null
