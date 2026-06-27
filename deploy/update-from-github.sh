#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="${SG_AWG_PANEL_VERSION:-v0.1.0-alpha6}"
URL="https://github.com/s-gor/sg-awg-panel/archive/refs/tags/${VERSION}.tar.gz"
PROJECT_DIR="/opt/sg-awg-panel"
ENV_FILE="/etc/sg-awg-panel/web.env"
DATA_DIR="/var/lib/sg-awg-panel"
BACKUP_DIR="/root/sg-awg-panel-backups/$(date -u +%Y%m%d-%H%M%S)-update"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

log(){ printf '[SG-AWG-Panel] %s\n' "$*"; }
fail(){ printf '[SG-AWG-Panel] ERROR: %s\n' "$*" >&2; exit 1; }

get_env(){
  local key="$1" default="$2" value first last
  value="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  if (( ${#value} >= 2 )); then
    first="${value:0:1}"; last="${value: -1}"
    if [[ "$first" == "'" && "$last" == "'" ]] || [[ "$first" == '"' && "$last" == '"' ]]; then
      value="${value:1:${#value}-2}"
    fi
  fi
  printf '%s' "${value:-$default}"
}

[[ $EUID -eq 0 ]] || fail "run as root"
[[ -x "$PROJECT_DIR/.venv/bin/python" ]] || fail "existing installation not found; use install-from-github.sh"
[[ -f "$ENV_FILE" ]] || fail "missing $ENV_FILE"
for command in curl tar rsync python3; do command -v "$command" >/dev/null 2>&1 || fail "required command not found: $command"; done

log "Downloading ${VERSION}"
curl -fsSL "$URL" -o "$TMP/source.tar.gz"
tar -xzf "$TMP/source.tar.gz" -C "$TMP"
SOURCE_DIR="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
[[ -n "$SOURCE_DIR" ]] || fail "downloaded archive is empty"

mkdir -p "$BACKUP_DIR"
if [[ -f "$DATA_DIR/panel.db" ]]; then
  SOURCE_DB="$DATA_DIR/panel.db" TARGET_DB="$BACKUP_DIR/panel.db" python3 - <<'PYDB'
import os, sqlite3
source=sqlite3.connect(os.environ['SOURCE_DB']); target=sqlite3.connect(os.environ['TARGET_DB'])
try: source.backup(target)
finally: target.close(); source.close()
PYDB
  chmod 600 "$BACKUP_DIR/panel.db"
fi
cp -a "$ENV_FILE" "$BACKUP_DIR/web.env"
[[ -f /etc/amnezia/amneziawg/awg0.conf ]] && cp -a /etc/amnezia/amneziawg/awg0.conf "$BACKUP_DIR/awg0.conf"

log "Updating panel files without apt/dpkg"
rsync -a --delete --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' "$SOURCE_DIR/" "$PROJECT_DIR/"
cd "$PROJECT_DIR"
.venv/bin/pip install --no-cache-dir -q -r requirements.txt
.venv/bin/pip install --no-cache-dir -q -e .

DB_PATH="$(get_env AWGPANEL_DB /var/lib/sg-awg-panel/panel.db)"
AWG_CONFIG_DIR="$(get_env AWGPANEL_AWG_CONFIG_DIR /etc/amnezia/amneziawg)"
AWG_SERVICE="$(get_env AWGPANEL_AWG_SERVICE sg-awg-server)"
AWGPANEL_DB="$DB_PATH" AWGPANEL_AWG_CONFIG_DIR="$AWG_CONFIG_DIR" AWGPANEL_AWG_SERVICE="$AWG_SERVICE" .venv/bin/python -m awgpanel init-db

bash deploy/install-service.sh
bash deploy/install-backup-timer.sh
systemctl restart sg-awg-panel.service
systemctl is-active --quiet sg-awg-panel.service || {
  systemctl --no-pager --full status sg-awg-panel.service || true
  journalctl -u sg-awg-panel.service -n 80 --no-pager || true
  fail "web service is not active"
}

log "Ready: $(.venv/bin/python -m awgpanel --version)"
log "No apt update, package installation or AmneziaWG restart was performed"
log "Backup: $BACKUP_DIR"
