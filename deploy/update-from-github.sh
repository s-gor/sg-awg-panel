#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="${SG_AWG_PANEL_VERSION:-v0.1.0-alpha8}"
URL="https://github.com/s-gor/sg-awg-panel/archive/refs/tags/${VERSION}.tar.gz"
PROJECT_DIR="/opt/sg-awg-panel"
ENV_FILE="/etc/sg-awg-panel/web.env"
DATA_DIR="/var/lib/sg-awg-panel"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP_DIR="/root/sg-awg-panel-backups/${STAMP}-update-rollback"
STATUS_FILE="${SG_AWG_PANEL_UPDATE_STATUS:-/var/lib/sg-awg-panel/update-status.json}"
LOG_FILE="${SG_AWG_PANEL_UPDATE_LOG:-/var/lib/sg-awg-panel/update.log}"
TMP="$(mktemp -d)"
ROLLBACK_NEEDED=0
NGINX_WAS_ACTIVE=0
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$STATUS_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

log(){ printf '[SG-AWG-Panel] %s\n' "$*"; }
fail(){ printf '[SG-AWG-Panel] ERROR: %s\n' "$*" >&2; return 1; }
status(){ python3 - "$STATUS_FILE" "$1" "$VERSION" "${2:-}" <<'PY'
import json,sys
from datetime import datetime,timezone
p,state,version,message=sys.argv[1:]
open(p,'w',encoding='utf-8').write(json.dumps({'state':state,'version':version,'message':message,'updated_at':datetime.now(timezone.utc).isoformat()},ensure_ascii=False)+'\n')
PY
}
get_env(){
  local key="$1" default="$2" value first last
  value="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  if (( ${#value} >= 2 )); then
    first="${value:0:1}"; last="${value: -1}"
    if [[ "$first" == "'" && "$last" == "'" ]] || [[ "$first" == '"' && "$last" == '"' ]]; then value="${value:1:${#value}-2}"; fi
  fi
  printf '%s' "${value:-$default}"
}

NGINX_PATHS=(
  /etc/nginx/sites-available/sg-awg-panel
  /etc/nginx/sites-enabled/sg-awg-panel
  /etc/nginx/sites-available/sg-awg-panel.conf
  /etc/nginx/sites-enabled/sg-awg-panel.conf
  /etc/nginx/sites-available/sg-awg-placeholder.conf
  /etc/nginx/sites-enabled/sg-awg-placeholder.conf
)

backup_path(){
  local path="$1" name
  name="$(printf '%s' "$path" | sed 's#^/##; s#/#__#g')"
  if [[ -e "$path" || -L "$path" ]]; then
    cp -a "$path" "$BACKUP_DIR/$name"
    : > "$BACKUP_DIR/$name.exists"
  fi
}
restore_path(){
  local path="$1" name
  name="$(printf '%s' "$path" | sed 's#^/##; s#/#__#g')"
  rm -rf "$path"
  if [[ -f "$BACKUP_DIR/$name.exists" ]]; then
    cp -a "$BACKUP_DIR/$name" "$path"
  fi
}

rollback(){
  local code=$?
  trap - ERR
  if (( ROLLBACK_NEEDED )); then
    status rollback "$code"
    log "Update failed; restoring previous working version"
    systemctl stop sg-awg-panel.service 2>/dev/null || true
    rsync -a --delete --exclude '.venv/' "$BACKUP_DIR/project/" "$PROJECT_DIR/" || true
    [[ -f "$BACKUP_DIR/web.env" ]] && cp -a "$BACKUP_DIR/web.env" "$ENV_FILE"
    if [[ -f "$BACKUP_DIR/panel.db" ]]; then
      rm -f "$DATA_DIR/panel.db" "$DATA_DIR/panel.db-wal" "$DATA_DIR/panel.db-shm"
      cp -a "$BACKUP_DIR/panel.db" "$DATA_DIR/panel.db"
    fi
    for path in "${NGINX_PATHS[@]}"; do restore_path "$path"; done
    for unit in sg-awg-panel.service sg-awg-backup.service sg-awg-backup.timer sg-awg-recovery.service; do
      rm -f "/etc/systemd/system/$unit"
      [[ -f "$BACKUP_DIR/$unit" ]] && cp -a "$BACKUP_DIR/$unit" "/etc/systemd/system/$unit"
    done
    cd "$PROJECT_DIR" || true
    .venv/bin/pip install --no-cache-dir -q -e . || true
    systemctl daemon-reload || true
    systemctl restart sg-awg-panel.service || true
    if command -v nginx >/dev/null 2>&1; then
      nginx -t >/dev/null 2>&1 || true
      if (( NGINX_WAS_ACTIVE )); then systemctl restart nginx || true; else systemctl stop nginx 2>/dev/null || true; fi
    fi
    status rolled_back "Previous version restored"
  fi
  exit "$code"
}
trap rollback ERR

[[ $EUID -eq 0 ]] || fail "run as root"
[[ -x "$PROJECT_DIR/.venv/bin/python" ]] || fail "existing installation not found"
[[ -f "$ENV_FILE" ]] || fail "missing $ENV_FILE"
[[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta|rc)[0-9]+)?$ ]] || fail "invalid version"
for command in curl tar rsync python3; do command -v "$command" >/dev/null 2>&1 || fail "required command not found: $command"; done

status downloading "Downloading source"
log "Downloading ${VERSION}"
curl -fsSL "$URL" -o "$TMP/source.tar.gz"
tar -xzf "$TMP/source.tar.gz" -C "$TMP"
SOURCE_DIR="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
[[ -n "$SOURCE_DIR" ]] || fail "downloaded archive is empty"

mkdir -p "$BACKUP_DIR/project"
rsync -a --exclude '.venv/' "$PROJECT_DIR/" "$BACKUP_DIR/project/"
cp -a "$ENV_FILE" "$BACKUP_DIR/web.env"
if [[ -f "$DATA_DIR/panel.db" ]]; then
  SOURCE_DB="$DATA_DIR/panel.db" TARGET_DB="$BACKUP_DIR/panel.db" python3 - <<'PYDB'
import os,sqlite3
s=sqlite3.connect(os.environ['SOURCE_DB']); t=sqlite3.connect(os.environ['TARGET_DB'])
try:s.backup(t)
finally:t.close();s.close()
PYDB
fi
for path in "${NGINX_PATHS[@]}"; do backup_path "$path"; done
systemctl is-active --quiet nginx 2>/dev/null && NGINX_WAS_ACTIVE=1 || true
for unit in sg-awg-panel.service sg-awg-backup.service sg-awg-backup.timer sg-awg-recovery.service; do
  [[ -f "/etc/systemd/system/$unit" ]] && cp -a "/etc/systemd/system/$unit" "$BACKUP_DIR/$unit"
done
ROLLBACK_NEEDED=1

OLD_PORT="$(get_env AWGPANEL_PUBLIC_PORT "$(get_env AWGPANEL_PORT 8080)")"
OLD_SCHEME="$(get_env AWGPANEL_PUBLIC_SCHEME http)"
OLD_HOST="$(get_env AWGPANEL_PUBLIC_HOST '')"
OLD_PLACEHOLDER="$(get_env AWGPANEL_MANAGE_PLACEHOLDER 1)"

if [[ "$OLD_PORT" == "443" ]]; then
  fail "The current panel uses TCP 443. Before updating, change the Alpha 7 panel port to a separate port such as 62443."
fi

status installing "Installing panel files"
rsync -a --delete --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' "$SOURCE_DIR/" "$PROJECT_DIR/"
cd "$PROJECT_DIR"
.venv/bin/pip install --no-cache-dir -q -r requirements.txt
.venv/bin/pip install --no-cache-dir -q -e .

python3 - "$ENV_FILE" "$OLD_PORT" "$OLD_SCHEME" "$OLD_HOST" "$OLD_PLACEHOLDER" <<'PYENV'
from pathlib import Path
import sys
p=Path(sys.argv[1]); public_port,scheme,host,manage_placeholder=sys.argv[2:]
updates={
 'AWGPANEL_BIND_ADDRESS':'127.0.0.1',
 'AWGPANEL_PORT':'18080',
 'AWGPANEL_BACKEND_PORT':'18080',
 'AWGPANEL_PUBLIC_PORT':public_port,
 'AWGPANEL_PUBLIC_SCHEME':scheme,
 'AWGPANEL_PUBLIC_HOST':host,
 'AWGPANEL_MANAGE_PLACEHOLDER':manage_placeholder,
 'AWGPANEL_HTTPS_EMAIL':'',
 'AWGPANEL_TRUST_PROXY_HEADERS':'1',
 'AWGPANEL_SECURE_COOKIES':'1' if scheme=='https' else '0',
}
lines=p.read_text(encoding='utf-8').splitlines(); out=[]; seen=set()
for line in lines:
 k=line.split('=',1)[0] if '=' in line else ''
 if k in updates: out.append(f'{k}={updates[k]}');seen.add(k)
 else: out.append(line)
for k,v in updates.items():
 if k not in seen: out.append(f'{k}={v}')
p.write_text('\n'.join(out)+'\n',encoding='utf-8')
PYENV
chmod 600 "$ENV_FILE"

DB_PATH="$(get_env AWGPANEL_DB /var/lib/sg-awg-panel/panel.db)"
AWG_CONFIG_DIR="$(get_env AWGPANEL_AWG_CONFIG_DIR /etc/amnezia/amneziawg)"
AWG_SERVICE="$(get_env AWGPANEL_AWG_SERVICE sg-awg-server)"
AWGPANEL_DB="$DB_PATH" AWGPANEL_AWG_CONFIG_DIR="$AWG_CONFIG_DIR" AWGPANEL_AWG_SERVICE="$AWG_SERVICE" .venv/bin/python -m awgpanel init-db
AWGPANEL_DB="$DB_PATH" PUBLIC_SCHEME_SYNC="$OLD_SCHEME" PUBLIC_HOST_SYNC="$OLD_HOST" PUBLIC_PORT_SYNC="$OLD_PORT" MANAGE_PLACEHOLDER_SYNC="$OLD_PLACEHOLDER" .venv/bin/python - <<'PYSET'
import os
from awgpanel.db import connect
with connect() as con:
    con.execute(
        """UPDATE panel_settings SET public_scheme=?, public_host=?, public_port=?, https_email='', https_enabled=?, manage_placeholder=?, backend_address='127.0.0.1', backend_port=18080, updated_at=CURRENT_TIMESTAMP WHERE id=1""",
        (
            os.environ['PUBLIC_SCHEME_SYNC'], os.environ['PUBLIC_HOST_SYNC'], int(os.environ['PUBLIC_PORT_SYNC']),
            1 if os.environ['PUBLIC_SCHEME_SYNC']=='https' else 0, int(os.environ['MANAGE_PLACEHOLDER_SYNC'])
        ),
    )
PYSET

bash deploy/install-service.sh
bash deploy/install-backup-timer.sh
bash deploy/install-recovery-service.sh
systemctl restart sg-awg-panel.service
for _ in {1..20}; do curl -fsS http://127.0.0.1:18080/health >/dev/null && break; sleep 1; done
curl -fsS http://127.0.0.1:18080/health >/dev/null

ACCESS_ARGS=(--scheme "$OLD_SCHEME" --port "$OLD_PORT" --manage-placeholder "$OLD_PLACEHOLDER")
[[ -n "$OLD_HOST" ]] && ACCESS_ARGS+=(--domain "$OLD_HOST")
bash deploy/configure-panel-access.sh "${ACCESS_ARGS[@]}"
nginx -t
systemctl enable --now nginx sg-awg-panel.service sg-awg-recovery.service >/dev/null
[[ -s /etc/amnezia/amneziawg/awg0.conf ]] && systemctl enable sg-awg-server.service >/dev/null || true

ROLLBACK_NEEDED=0
status success "Update completed"
log "Ready: $(.venv/bin/python -m awgpanel --version)"
log "Automatic rollback backup: $BACKUP_DIR"
log "AmneziaWG tunnel was not restarted"
