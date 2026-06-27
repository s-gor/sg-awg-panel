#!/usr/bin/env bash
set -Eeuo pipefail
sysctl -w net.ipv4.ip_forward=1 >/dev/null
modprobe amneziawg >/dev/null 2>&1 || true
systemctl is-active --quiet sg-awg-panel.service || systemctl restart sg-awg-panel.service
if systemctl cat nginx.service >/dev/null 2>&1; then systemctl is-active --quiet nginx.service || systemctl restart nginx.service; fi
if [[ -s /etc/amnezia/amneziawg/awg0.conf ]]; then systemctl is-active --quiet sg-awg-server.service || systemctl restart sg-awg-server.service; fi
