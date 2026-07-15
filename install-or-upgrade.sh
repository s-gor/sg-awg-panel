#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$SOURCE_DIR/deploy"
# shellcheck source=deploy/install-common.sh
. "$SCRIPT_DIR/install-common.sh"

PROJECT_DIR="/opt/sg-awg-panel"
ENV_DIR="/etc/sg-awg-panel"
ENV_FILE="$ENV_DIR/web.env"
DATA_DIR="/var/lib/sg-awg-panel"
BACKUP_DIR="/root/sg-awg-panel-backups/$(date -u +%Y%m%d-%H%M%S)"
PASSWORD_MIN_LENGTH=8

install_log_init
require_root
require_supported_ubuntu
require_supported_architecture
require_no_pending_reboot

if [[ ! -f "$ENV_FILE" ]]; then
  prompt_instance_name "SG-AWG-Panel"
  prompt_admin_password "$PASSWORD_MIN_LENGTH"
  prompt_public_port 62443
fi

get_env() {
  local key="$1" default="$2" value first last
  value="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
  if (( ${#value} >= 2 )); then
    first="${value:0:1}"
    last="${value: -1}"
    if [[ "$first" == "'" && "$last" == "'" ]] \
      || [[ "$first" == '"' && "$last" == '"' ]]; then
      value="${value:1:${#value}-2}"
    fi
  fi
  printf '%s' "${value:-$default}"
}

wait_for_apt
run_logged "Подготовка пакетной системы..." dpkg --configure -a
run_logged "Обновление списка пакетов..." apt-get -o Dpkg::Use-Pty=0 update -qq
run_logged "Установка Python, Nginx и зависимостей панели..." \
  env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get -o Dpkg::Use-Pty=0 install -y -qq \
  python3 python3-venv python3-pip rsync ca-certificates nginx curl tar psmisc nftables conntrack iproute2 util-linux dnsmasq

# dnsmasq is panel-managed and must not listen on public interfaces before
# the panel binds automatic DNS routing to the private AWG server address.
if [[ ! -f /etc/dnsmasq.d/sg-awg-traffic.conf ]]; then
  systemctl disable --now dnsmasq.service >>"$LOG_FILE" 2>&1 || true
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
[[ -f /etc/amnezia/amneziawg/awg0.conf ]] \
  && cp -a /etc/amnezia/amneziawg/awg0.conf "$BACKUP_DIR/awg0.conf"

install_info "Установка файлов панели..."
mkdir -p "$PROJECT_DIR"
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  "$SOURCE_DIR/" "$PROJECT_DIR/" >>"$INSTALL_LOG" 2>&1 \
  || install_fail "не удалось установить файлы панели"
run_logged "Нормализация прав установочных скриптов..." \
  find "$PROJECT_DIR" -type f -name '*.sh' -exec chmod 0755 {} +

cd "$PROJECT_DIR"
run_logged "Создание Python-окружения..." python3 -m venv .venv
run_logged "Установка Python-зависимостей..." .venv/bin/pip install \
  --disable-pip-version-check --no-cache-dir -q --upgrade pip
run_logged "Установка SG-AWG-Panel..." .venv/bin/pip install \
  --disable-pip-version-check --no-cache-dir -q -r requirements.txt
run_logged "Регистрация Python-пакета..." .venv/bin/pip install \
  --disable-pip-version-check --no-cache-dir -q -e .
run_logged "Установка SSH-команды управления..." \
  ln -sfn "$PROJECT_DIR/.venv/bin/sg-awg-panel" /usr/local/sbin/sg-awg-panel

if [[ ! -f "$ENV_FILE" ]]; then
  [[ ${#AWGPANEL_ADMIN_PASSWORD} -ge $PASSWORD_MIN_LENGTH ]] \
    || install_fail "пароль администратора должен содержать не менее ${PASSWORD_MIN_LENGTH} символов"

  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  PASSWORD_HASH="$(
    AWGPANEL_PASSWORD="$AWGPANEL_ADMIN_PASSWORD" .venv/bin/python -c \
      'import os; from werkzeug.security import generate_password_hash; print(generate_password_hash(os.environ["AWGPANEL_PASSWORD"]))'
  )"

  cat > "$ENV_FILE" <<ENVEOF
AWGPANEL_SECRET_KEY=$SECRET_KEY
AWGPANEL_PASSWORD_HASH=$PASSWORD_HASH
AWGPANEL_INSTANCE_NAME=SG-AWG-Panel
AWGPANEL_ENV_FILE=/etc/sg-awg-panel/web.env
AWGPANEL_BIND_ADDRESS=127.0.0.1
AWGPANEL_PORT=18080
AWGPANEL_BACKEND_PORT=18080
AWGPANEL_PUBLIC_SCHEME=http
AWGPANEL_PUBLIC_HOST=
AWGPANEL_PUBLIC_PORT=${AWGPANEL_PUBLIC_PORT:-62443}
AWGPANEL_MANAGE_PLACEHOLDER=1
AWGPANEL_HTTPS_EMAIL=
AWGPANEL_SECURE_COOKIES=0
AWGPANEL_TRUST_PROXY_HEADERS=1
AWGPANEL_DB=/var/lib/sg-awg-panel/panel.db
AWGPANEL_AWG_CONFIG_DIR=/etc/amnezia/amneziawg
AWGPANEL_AWG_SERVICE=sg-awg-server
AWGPANEL_OUTBOUND_CONFIG_DIR=/etc/amnezia/amneziawg/outbounds
AWGPANEL_TRAFFIC_STATE_DIR=/var/lib/sg-awg-panel/traffic-rules
AWGPANEL_TRAFFIC_LOCK=/run/lock/sg-awg-panel-traffic.lock
AWGPANEL_DNSMASQ_CONFIG=/etc/dnsmasq.d/sg-awg-traffic.conf
AWGPANEL_TRAFFIC_SCHEDULE_STATE=/var/lib/sg-awg-panel/traffic-rules/schedule-state.json
AWGPANEL_BACKUP_DIR=/var/lib/sg-awg-panel/backups
AWGPANEL_BACKUP_KEEP=20
AWGPANEL_ACCESS_JOBS_DIR=/var/lib/sg-awg-panel/access-jobs
AWGPANEL_OPERATION_JOBS_DIR=/var/lib/sg-awg-panel/operation-jobs
AWGPANEL_PROJECT_DIR=/opt/sg-awg-panel
ENVEOF
  chmod 600 "$ENV_FILE"
  AWGPANEL_INSTANCE_NAME_VALUE="${AWGPANEL_INSTANCE_NAME:-SG-AWG-Panel}" python3 - "$ENV_FILE" <<'PYINSTANCE'
from pathlib import Path
import os
import shlex
import sys

path = Path(sys.argv[1])
value = os.environ.get("AWGPANEL_INSTANCE_NAME_VALUE", "SG-AWG-Panel")
lines = path.read_text(encoding="utf-8").splitlines()
out = []
replaced = False
for line in lines:
    if line.startswith("AWGPANEL_INSTANCE_NAME="):
        out.append("AWGPANEL_INSTANCE_NAME=" + shlex.quote(value))
        replaced = True
    else:
        out.append(line)
if not replaced:
    out.append("AWGPANEL_INSTANCE_NAME=" + shlex.quote(value))
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PYINSTANCE
fi
unset AWGPANEL_ADMIN_PASSWORD || true

# A persistent secret is mandatory.  Never let Flask silently create a new
# secret on every restart because that invalidates sessions and CSRF tokens.
ENV_FILE_PATH="$ENV_FILE" python3 - <<'PYSECRET'
from pathlib import Path
import os
import secrets

path = Path(os.environ["ENV_FILE_PATH"])
lines = path.read_text(encoding="utf-8").splitlines()
values = {}
for line in lines:
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
if not values.get("AWGPANEL_SECRET_KEY"):
    lines.append("AWGPANEL_SECRET_KEY=" + secrets.token_urlsafe(48))
if not values.get("AWGPANEL_PASSWORD_HASH"):
    raise SystemExit("AWGPANEL_PASSWORD_HASH is missing; run the full installer or sudo sg-awg-panel password")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
os.chmod(path, 0o600)
PYSECRET

# web.env is parsed as data and is never sourced as shell code.
DB_PATH="$(get_env AWGPANEL_DB /var/lib/sg-awg-panel/panel.db)"
AWG_CONFIG_DIR="$(get_env AWGPANEL_AWG_CONFIG_DIR /etc/amnezia/amneziawg)"
AWG_SERVICE="$(get_env AWGPANEL_AWG_SERVICE sg-awg-server)"
BACKEND_PORT="$(get_env AWGPANEL_PORT 18080)"
PUBLIC_PORT="$(get_env AWGPANEL_PUBLIC_PORT 62443)"
PUBLIC_SCHEME="$(get_env AWGPANEL_PUBLIC_SCHEME http)"
PUBLIC_HOST="$(get_env AWGPANEL_PUBLIC_HOST '')"
MANAGE_PLACEHOLDER="$(get_env AWGPANEL_MANAGE_PLACEHOLDER 1)"
INSTANCE_NAME="$(get_env AWGPANEL_INSTANCE_NAME SG-AWG-Panel)"

python3 - "$ENV_FILE" "$PUBLIC_PORT" "$PUBLIC_SCHEME" "$PUBLIC_HOST" "$MANAGE_PLACEHOLDER" "$INSTANCE_NAME" <<'PYENV'
from pathlib import Path
import shlex
import sys

path = Path(sys.argv[1])
public_port, scheme, host, manage_placeholder, instance_name = sys.argv[2:]
updates = {
    "AWGPANEL_INSTANCE_NAME": shlex.quote(instance_name),
    "AWGPANEL_BIND_ADDRESS": "127.0.0.1",
    "AWGPANEL_PORT": "18080",
    "AWGPANEL_BACKEND_PORT": "18080",
    "AWGPANEL_PUBLIC_PORT": public_port,
    "AWGPANEL_PUBLIC_SCHEME": scheme,
    "AWGPANEL_PUBLIC_HOST": host,
    "AWGPANEL_MANAGE_PLACEHOLDER": manage_placeholder,
    "AWGPANEL_HTTPS_EMAIL": "",
    "AWGPANEL_TRUST_PROXY_HEADERS": "1",
    "AWGPANEL_SECURE_COOKIES": "1" if scheme == "https" else "0",
    "AWGPANEL_ACCESS_JOBS_DIR": "/var/lib/sg-awg-panel/access-jobs",
    "AWGPANEL_OPERATION_JOBS_DIR": "/var/lib/sg-awg-panel/operation-jobs",
    "AWGPANEL_PROJECT_DIR": "/opt/sg-awg-panel",
}
lines = path.read_text(encoding="utf-8").splitlines()
out = []
seen = set()
for line in lines:
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PYENV
chmod 600 "$ENV_FILE"

install_info "Инициализация базы данных..."
AWGPANEL_DB="$DB_PATH" \
AWGPANEL_AWG_CONFIG_DIR="$AWG_CONFIG_DIR" \
AWGPANEL_AWG_SERVICE="$AWG_SERVICE" \
  .venv/bin/python -m awgpanel init-db >>"$INSTALL_LOG" 2>&1 \
  || install_fail "не удалось инициализировать базу данных"

AWGPANEL_DB="$DB_PATH" \
PUBLIC_SCHEME_SYNC="$PUBLIC_SCHEME" \
PUBLIC_HOST_SYNC="$PUBLIC_HOST" \
PUBLIC_PORT_SYNC="$PUBLIC_PORT" \
MANAGE_PLACEHOLDER_SYNC="$MANAGE_PLACEHOLDER" \
INSTANCE_NAME_SYNC="$INSTANCE_NAME" \
  .venv/bin/python - <<'PYSET'
import os
from awgpanel.db import connect

with connect() as con:
    con.execute(
        """
        UPDATE panel_settings
        SET instance_name=?, public_scheme=?, public_host=?, public_port=?, https_email='',
            https_enabled=?, manage_placeholder=?, backend_address='127.0.0.1',
            backend_port=18080, updated_at=CURRENT_TIMESTAMP
        WHERE id=1
        """,
        (
            os.environ["INSTANCE_NAME_SYNC"],
            os.environ["PUBLIC_SCHEME_SYNC"],
            os.environ["PUBLIC_HOST_SYNC"],
            int(os.environ["PUBLIC_PORT_SYNC"]),
            1 if os.environ["PUBLIC_SCHEME_SYNC"] == "https" else 0,
            int(os.environ["MANAGE_PLACEHOLDER_SYNC"]),
        ),
    )
PYSET

install_info "Автоматическая настройка AWG Server..."
AWGPANEL_DB="$DB_PATH" \
AWGPANEL_AWG_CONFIG_DIR="$AWG_CONFIG_DIR" \
AWGPANEL_AWG_SERVICE="$AWG_SERVICE" \
  .venv/bin/python -m awgpanel ensure-server >>"$INSTALL_LOG" 2>&1 \
  || install_fail "не удалось автоматически настроить и запустить AWG Server"

run_logged "Настройка службы панели..." bash deploy/install-service.sh
run_logged "Настройка резервного копирования..." bash deploy/install-backup-timer.sh
run_logged "Настройка policy routing..." bash deploy/install-traffic-service.sh
run_logged "Настройка Traffic Rules..." bash deploy/install-traffic-maintenance.sh
run_logged "Настройка сроков действия клиентов..." bash deploy/install-client-maintenance.sh
run_logged "Настройка восстановления после reboot..." bash deploy/install-recovery-service.sh
run_logged "Запуск панели..." systemctl restart sg-awg-panel.service
systemctl is-active --quiet sg-awg-panel.service \
  || install_fail "служба панели не запустилась"

ACCESS_ARGS=(
  --scheme "$PUBLIC_SCHEME"
  --port "$PUBLIC_PORT"
  --manage-placeholder "$MANAGE_PLACEHOLDER"
)
[[ -n "$PUBLIC_HOST" ]] && ACCESS_ARGS+=(--domain "$PUBLIC_HOST")
run_logged "Настройка Nginx..." \
  bash deploy/configure-panel-access.sh "${ACCESS_ARGS[@]}"

curl -fsS --max-time 10 "http://127.0.0.1:${BACKEND_PORT}/health" >/dev/null \
  || install_fail "backend панели не отвечает после запуска"
curl -fsS --max-time 10 "http://127.0.0.1:${PUBLIC_PORT}/health" >/dev/null \
  || install_fail "Nginx не передаёт запросы панели"

run_logged "Проверка SSH-команды управления..." \
  /usr/local/sbin/sg-awg-panel status

run_logged "Проверка восстановления после reboot..." \
  systemctl restart sg-awg-recovery.service
systemctl is-active --quiet sg-awg-recovery.service \
  || install_fail "служба восстановления не запустилась"
systemctl is-active --quiet sg-awg-traffic.service \
  || install_fail "служба Traffic Rules не запустилась"
systemctl is-active --quiet sg-awg-traffic-schedule.timer \
  || install_fail "таймер расписаний Traffic Rules не запустился"
systemctl is-active --quiet sg-awg-clients-maintenance.timer \
  || install_fail "таймер сроков действия клиентов не запустился"

install_info "Версия: $(.venv/bin/python -m awgpanel --version | awk '{print $2}')"
install_info "Backend: 127.0.0.1:${BACKEND_PORT}"
DISPLAY_HOST="$PUBLIC_HOST"
if [[ -z "$DISPLAY_HOST" ]]; then
  DISPLAY_HOST="$(detect_public_ipv4)"
fi
if [[ -n "$DISPLAY_HOST" ]]; then
  install_info "Панель: ${PUBLIC_SCHEME}://${DISPLAY_HOST}:${PUBLIC_PORT}"
else
  install_info "Панель настроена на TCP-порту ${PUBLIC_PORT}; откройте публичный IP сервера"
fi
