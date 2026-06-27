#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
cat > /etc/systemd/system/sg-awg-recovery.service <<'UNIT'
[Unit]
Description=SG-AWG-Panel recovery after reboot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/sg-awg-panel/deploy/recover-after-boot.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now sg-awg-recovery.service >/dev/null
