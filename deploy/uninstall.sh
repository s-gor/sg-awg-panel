#!/usr/bin/env bash
set -Eeuo pipefail

PURGE_AWG=0
[[ "${1:-}" == "--purge-amneziawg" ]] && PURGE_AWG=1
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }

printf 'This removes SG-AWG-Panel, all clients, keys, backups and awg0.conf.\n'
(( PURGE_AWG == 1 )) && printf 'The AmneziaWG package and PPA will also be removed.\n'
read -r -p "Type DELETE SG-AWG-PANEL: " answer
[[ "$answer" == "DELETE SG-AWG-PANEL" ]] || { echo "Cancelled"; exit 1; }

if command -v awg-quick >/dev/null 2>&1 && [[ -f /etc/amnezia/amneziawg/awg0.conf ]]; then
  awg-quick down /etc/amnezia/amneziawg/awg0.conf >/dev/null 2>&1 || true
fi
systemctl disable --now sg-awg-panel.service 2>/dev/null || true
systemctl disable --now sg-awg-backup.timer 2>/dev/null || true
systemctl disable --now sg-awg-recovery.service 2>/dev/null || true
systemctl disable --now sg-awg-server.service 2>/dev/null || true
rm -f /etc/systemd/system/sg-awg-panel.service /etc/systemd/system/sg-awg-server.service \
  /etc/systemd/system/sg-awg-backup.service /etc/systemd/system/sg-awg-backup.timer \
  /etc/systemd/system/sg-awg-recovery.service
rm -f /etc/sysctl.d/90-sg-awg-panel.conf
rm -f /etc/nginx/sites-enabled/sg-awg-panel /etc/nginx/sites-available/sg-awg-panel
rm -rf /var/www/sg-awg-panel-acme
nginx -t >/dev/null 2>&1 && systemctl reload nginx 2>/dev/null || true
rm -rf /opt/sg-awg-panel /etc/sg-awg-panel /var/lib/sg-awg-panel /etc/amnezia/amneziawg
systemctl daemon-reload
systemctl reset-failed >/dev/null 2>&1 || true
sysctl --system >/dev/null 2>&1 || true

if (( PURGE_AWG == 1 )); then
  DEBIAN_FRONTEND=noninteractive apt-get purge -y amneziawg || true
  DEBIAN_FRONTEND=noninteractive apt-get autoremove -y --purge || true
  rm -f /etc/apt/sources.list.d/*amnezia*.list /etc/apt/sources.list.d/*amnezia*.sources
  apt-get update || true
  echo "SG-AWG-Panel and AmneziaWG packages removed."
else
  echo "SG-AWG-Panel data and services removed. Package amneziawg was left installed."
fi
