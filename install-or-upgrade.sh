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

wait_for_apt(){
  local waited=0 timeout="${APT_LOCK_TIMEOUT:-900}"
  local locks=(/var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock)
  while command -v fuser >/dev/null 2>&1 && fuser "${locks[@]}" >/dev/null 2>&1; do
    (( waited == 0 )) && log "Waiting for real apt/dpkg locks"
    (( waited >= timeout )) && fail "apt/dpkg locks were not released after ${timeout} seconds"
    sleep 5
    waited=$((waited + 5))
  done
  dpkg --configure -a
}

[[ $EUID -eq 0 ]] || fail "run as root"
[[ -r /etc/os-release ]] || fail "cannot detect operating system"
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || fail "Alpha 4 supports Ubuntu only"
case "${VERSION_ID:-}" in
  22.04|24.04) ;;
  *) fail "Alpha 4 is intended for Ubuntu 22.04/24.04; found ${VERSION_ID:-unknown}" ;;
esac

get_env(){
  local key="$1" default="$2" value first last
  value="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  if (( ${#value} >= 2 )); then
    first="${value:0:1}"
    last="${value: -1}"
    if [[ "$first" == "'" && "$last" == "'" ]] || [[ "$first" == '"' && "$last" == '"' ]]; then
      value="${value:1:${#value}-2}"
    fi
  fi
  printf '%s' "${value:-$default}"
}

if [[ -x "$PROJECT_DIR/.venv/bin/python" && -f "$ENV_FILE" ]]; then
  log "Ubuntu ${VERSION_ID}; existing installation detected - system packages will not be touched"
else
  log "Ubuntu ${VERSION_ID}; starting clean installation"
  wait_for_apt
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip rsync ca-certificates
fi

mkdir -p "$BACKUP_DIR" "$DATA_DIR" "$ENV_DIR"
if [[ -f "$DATA_DIR/panel.db" ]]; then
  SOURCE_DB="$DATA_DIR/panel.db" TARGET_DB="$BACKUP_DIR/panel.db" python3 - <<'PYDB'
import os
import sqlite3

source = sqlite3.connect(os.environ["SOURCE_DB"])
target = sqlite3.connect(os.environ["TARGET_DB"])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PYDB
  chmod 600 "$BACKUP_DIR/panel.db"
fi
[[ -f "$ENV_FILE" ]] && cp -a "$ENV_FILE" "$BACKUP_DIR/web.env"
[[ -f /etc/amnezia/amneziawg/awg0.conf ]] && cp -a /etc/amnezia/amneziawg/awg0.conf "$BACKUP_DIR/awg0.conf"

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
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  PASSWORD_HASH="$(AWGPANEL_PASSWORD="$AWGPANEL_ADMIN_PASSWORD" .venv/bin/python -c 'import os; from werkzeug.security import generate_password_hash; print(generate_password_hash(os.environ["AWGPANEL_PASSWORD"]))')"
  cat > "$ENV_FILE" <<ENVEOF
AWGPANEL_SECRET_KEY=$SECRET_KEY
AWGPANEL_PASSWORD_HASH=$PASSWORD_HASH
AWGPANEL_ENV_FILE=/etc/sg-awg-panel/web.env
AWGPANEL_BIND_ADDRESS=${AWGPANEL_BIND_ADDRESS:-0.0.0.0}
AWGPANEL_PORT=${AWGPANEL_PORT:-8080}
AWGPANEL_SECURE_COOKIES=0
AWGPANEL_TRUST_PROXY_HEADERS=0
AWGPANEL_DB=/var/lib/sg-awg-panel/panel.db
AWGPANEL_AWG_CONFIG_DIR=/etc/amnezia/amneziawg
AWGPANEL_AWG_SERVICE=sg-awg-server
AWGPANEL_BACKUP_DIR=/var/lib/sg-awg-panel/backups
AWGPANEL_BACKUP_KEEP=20
ENVEOF
  chmod 600 "$ENV_FILE"
fi

# Do not source web.env as a shell script. Password hashes contain '$'.
DB_PATH="$(get_env AWGPANEL_DB /var/lib/sg-awg-panel/panel.db)"
AWG_CONFIG_DIR="$(get_env AWGPANEL_AWG_CONFIG_DIR /etc/amnezia/amneziawg)"
AWG_SERVICE="$(get_env AWGPANEL_AWG_SERVICE sg-awg-server)"
PORT="$(get_env AWGPANEL_PORT 8080)"

AWGPANEL_DB="$DB_PATH" \
AWGPANEL_AWG_CONFIG_DIR="$AWG_CONFIG_DIR" \
AWGPANEL_AWG_SERVICE="$AWG_SERVICE" \
  .venv/bin/python -m awgpanel init-db

bash deploy/install-service.sh
systemctl restart sg-awg-panel.service
systemctl is-active --quiet sg-awg-panel.service || {
  systemctl --no-pager --full status sg-awg-panel.service || true
  journalctl -u sg-awg-panel.service -n 80 --no-pager || true
  fail "web service is not active"
}

log "Ready: SG-AWG-Panel $(.venv/bin/python -m awgpanel --version | awk '{print $2}')"
log "Web: http://SERVER_IP:${PORT}"
log "Backup: $BACKUP_DIR"
