#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="/opt/sg-awg-panel"
ENV_FILE="/etc/sg-awg-panel/web.env"
SERVICE_FILE="/etc/systemd/system/sg-awg-panel.service"

[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE" >&2; exit 1; }

get_env(){
  local key="$1" default="$2" value
  value="$(grep -E "^${key}=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
  printf '%s' "${value:-$default}"
}

BIND_ADDRESS="$(get_env AWGPANEL_BIND_ADDRESS 0.0.0.0)"
PORT="$(get_env AWGPANEL_PORT 8080)"
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || { echo "Invalid port" >&2; exit 1; }
if [[ "$BIND_ADDRESS" == *:* && "$BIND_ADDRESS" != \[*\] ]]; then
  LISTEN="[$BIND_ADDRESS]:$PORT"
else
  LISTEN="$BIND_ADDRESS:$PORT"
fi

install -d -m 0700 /var/lib/sg-awg-panel /etc/sg-awg-panel /etc/amnezia/amneziawg

cat > "$SERVICE_FILE" <<UNIT
[Unit]
Description=SG-AWG-Panel web interface
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$PROJECT_DIR/.venv/bin/waitress-serve --threads=2 --listen=$LISTEN awgpanel.web:app
Restart=on-failure
RestartSec=3
User=root
Group=root
UMask=0077
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=/var/lib/sg-awg-panel /etc/sg-awg-panel /etc/amnezia/amneziawg

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable sg-awg-panel.service >/dev/null
