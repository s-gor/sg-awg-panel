#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }

bash "$PROJECT_DIR/deploy/install-amneziawg.sh"
bash "$PROJECT_DIR/install-or-upgrade.sh"

echo
echo "SG-AWG-Panel Alpha 3 installation completed."
echo "Open TCP 8080 only from your IP and UDP 585 for clients."
echo "Panel: http://SERVER_IP:8080"
