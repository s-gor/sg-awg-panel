#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
read -r -p "Type DELETE SG-AWG-PANEL: " answer
[[ "$answer" == "DELETE SG-AWG-PANEL" ]] || { echo "Cancelled"; exit 1; }
systemctl disable --now sg-awg-panel.service 2>/dev/null || true
systemctl disable --now sg-awg-server.service 2>/dev/null || true
rm -f /etc/systemd/system/sg-awg-panel.service /etc/systemd/system/sg-awg-server.service
rm -f /etc/sysctl.d/90-sg-awg-panel.conf
rm -rf /opt/sg-awg-panel /etc/sg-awg-panel /var/lib/sg-awg-panel /etc/amnezia/amneziawg
systemctl daemon-reload
sysctl --system >/dev/null || true
echo "SG-AWG-Panel data and services removed. Package amneziawg was left installed."
