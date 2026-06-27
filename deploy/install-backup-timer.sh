#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="/opt/sg-awg-panel"
ENV_FILE="/etc/sg-awg-panel/web.env"
SERVICE_FILE="/etc/systemd/system/sg-awg-backup.service"
TIMER_FILE="/etc/systemd/system/sg-awg-backup.timer"
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ -x "$PROJECT_DIR/.venv/bin/python" ]] || { echo "Panel virtual environment not found" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE" >&2; exit 1; }

get_env(){ local key="$1" default="$2" value; value="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"; value="${value#\'}"; value="${value%\'}"; printf '%s' "${value:-$default}"; }
DB_PATH="$(get_env AWGPANEL_DB /var/lib/sg-awg-panel/panel.db)"
readarray -t POLICY < <(cd "$PROJECT_DIR" && AWGPANEL_DB="$DB_PATH" .venv/bin/python - <<'PY'
from awgpanel.core import get_panel_settings, backup_calendar
p=get_panel_settings(); print(p['backup_schedule']); print(backup_calendar(str(p['backup_schedule'])))
PY
)
SCHEDULE="${POLICY[0]:-daily}"
CALENDAR="${POLICY[1]:-daily}"

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

if [[ "$SCHEDULE" == "disabled" || -z "$CALENDAR" ]]; then
  rm -f "$TIMER_FILE"
  systemctl daemon-reload
  systemctl disable --now sg-awg-backup.timer >/dev/null 2>&1 || true
  exit 0
fi

cat > "$TIMER_FILE" <<UNIT
[Unit]
Description=Scheduled SG-AWG-Panel backup

[Timer]
OnCalendar=$CALENDAR
Persistent=true
RandomizedDelaySec=15m
Unit=sg-awg-backup.service

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now sg-awg-backup.timer >/dev/null
