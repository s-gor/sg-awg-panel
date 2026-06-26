#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="/opt/sg-awg-panel"
ENV_DIR="/etc/sg-awg-panel"
ENV_FILE="$ENV_DIR/web.env"
DATA_DIR="/var/lib/sg-awg-panel"
BACKUP_DIR="/root/sg-awg-panel-backups/$(date -u +%Y%m%d-%H%M%S)"

log(){ printf '[SG-AWG-Panel] %s\n' "$*"; }
fail(){ printf '[SG-AWG-Panel] ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "run as root"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip rsync ca-certificates

mkdir -p "$BACKUP_DIR" "$DATA_DIR" "$ENV_DIR"
[[ -f "$DATA_DIR/panel.db" ]] && cp -a "$DATA_DIR/panel.db" "$BACKUP_DIR/panel.db"
[[ -f "$ENV_FILE" ]] && cp -a "$ENV_FILE" "$BACKUP_DIR/web.env"

mkdir -p "$PROJECT_DIR"
rsync -a --delete \
  --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' \
  "$SOURCE_DIR/" "$PROJECT_DIR/"

cd "$PROJECT_DIR"
python3 -m venv .venv
.venv/bin/pip install --no-cache-dir -q --upgrade pip
.venv/bin/pip install --no-cache-dir -q -r requirements.txt

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -z "${AWGPANEL_ADMIN_PASSWORD:-}" ]]; then
    read -r -s -p "Admin password: " AWGPANEL_ADMIN_PASSWORD; echo
    read -r -s -p "Repeat password: " AWGPANEL_ADMIN_PASSWORD_2; echo
    [[ "$AWGPANEL_ADMIN_PASSWORD" == "$AWGPANEL_ADMIN_PASSWORD_2" ]] || fail "passwords do not match"
  fi
  [[ ${#AWGPANEL_ADMIN_PASSWORD} -ge 8 ]] || fail "password must contain at least 8 characters"
  SECRET_KEY="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
  PASSWORD_HASH="$(AWGPANEL_PASSWORD="$AWGPANEL_ADMIN_PASSWORD" .venv/bin/python - <<'PY'
import os
from werkzeug.security import generate_password_hash
print(generate_password_hash(os.environ['AWGPANEL_PASSWORD']))
PY
)"
  cat > "$ENV_FILE" <<EOF
AWGPANEL_SECRET_KEY=$SECRET_KEY
AWGPANEL_PASSWORD_HASH=$PASSWORD_HASH
AWGPANEL_BIND_ADDRESS=${AWGPANEL_BIND_ADDRESS:-0.0.0.0}
AWGPANEL_PORT=${AWGPANEL_PORT:-8080}
AWGPANEL_SECURE_COOKIES=0
AWGPANEL_TRUST_PROXY_HEADERS=0
AWGPANEL_DB=/var/lib/sg-awg-panel/panel.db
AWGPANEL_AWG_CONFIG_DIR=/etc/amnezia/amneziawg
AWGPANEL_AWG_SERVICE=sg-awg-server
EOF
  chmod 600 "$ENV_FILE"
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
.venv/bin/python -m awgpanel init-db
bash deploy/install-service.sh
systemctl restart sg-awg-panel.service
systemctl is-active --quiet sg-awg-panel.service || {
  systemctl --no-pager --full status sg-awg-panel.service || true
  journalctl -u sg-awg-panel.service -n 80 --no-pager || true
  fail "web service is not active"
}

log "Ready: SG-AWG-Panel $(.venv/bin/python -m awgpanel --version | awk '{print $2}')"
log "Web: http://SERVER_IP:${AWGPANEL_PORT:-8080}"
log "Backup: $BACKUP_DIR"
