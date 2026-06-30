#!/usr/bin/env bash
set -Eeuo pipefail

JOB_TOKEN=""
SCHEME="http"
DOMAIN=""
PORT="62443"
MANAGE_PLACEHOLDER="1"
JOB_ROOT="${AWGPANEL_ACCESS_JOBS_DIR:-/var/lib/sg-awg-panel/access-jobs}"
PROJECT_DIR="/opt/sg-awg-panel"
UPDATE_ROOT="${AWGPANEL_UPDATE_ROOT:-/var/www/sg-awg-update}"

while (($#)); do
  case "$1" in
    --job-token) JOB_TOKEN="${2:-}"; shift 2 ;;
    --scheme) SCHEME="${2:-}"; shift 2 ;;
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --manage-placeholder) MANAGE_PLACEHOLDER="${2:-}"; shift 2 ;;
    *) exit 2 ;;
  esac
done

[[ "$JOB_TOKEN" =~ ^[A-Za-z0-9_-]{32,80}$ ]] || exit 2
JOB_DIR="$JOB_ROOT/$JOB_TOKEN"
STATUS_FILE="$JOB_DIR/status.json"
LOG_FILE="$JOB_DIR/access.log"
mkdir -p "$JOB_DIR"
chmod 700 "$JOB_ROOT" "$JOB_DIR"
touch "$LOG_FILE"
chmod 600 "$LOG_FILE"

if [[ "$SCHEME" == "https" ]]; then DEFAULT_PORT=443; else DEFAULT_PORT=80; fi
if [[ "$PORT" == "$DEFAULT_PORT" ]]; then
  TARGET_URL="${SCHEME}://${DOMAIN:-SERVER_IP}"
else
  TARGET_URL="${SCHEME}://${DOMAIN:-SERVER_IP}:${PORT}"
fi

write_status(){
  local state="$1" message="$2" exit_code="${3:-}" error_code="${4:-}" retry_after="${5:-}" restored="${6:-0}"
  python3 - "$STATUS_FILE" "$state" "$message" "$TARGET_URL" "$exit_code" "$error_code" "$retry_after" "$restored" <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
path = Path(sys.argv[1])
state, message, target_url, exit_code, error_code, retry_after, restored = sys.argv[2:]
old = {}
try:
    old = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    pass
payload = {
    "state": state,
    "message": message,
    "targetUrl": target_url,
    "startedAt": old.get("startedAt") or datetime.now(timezone.utc).isoformat(),
    "updatedAt": datetime.now(timezone.utc).isoformat(),
}
if exit_code:
    payload["exitCode"] = int(exit_code)
if error_code:
    payload["errorCode"] = error_code
if retry_after:
    payload["retryAfterUtc"] = retry_after
if restored == "1":
    payload["restored"] = True
tmp = path.with_suffix(".tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
tmp.chmod(0o600)
tmp.replace(path)
PY
}

publish_transition_status(){
  install -d -m 0755 "$UPDATE_ROOT"
  python3 - "$STATUS_FILE" "$UPDATE_ROOT/status.json" <<'PY'
import json
from pathlib import Path
import sys
source, target = map(Path, sys.argv[1:])
data = json.loads(source.read_text(encoding="utf-8"))
tmp = target.with_suffix(".tmp")
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
tmp.chmod(0o644)
tmp.replace(target)
PY
}

log_phase(){
  printf '[Этап] %s\n' "$1" >> "$LOG_FILE"
  write_status running "$1"
}

write_status running "Проверка параметров и подготовка"
{
  printf '[SG-AWG-Panel] Настройка публичного доступа\n'
  printf '[SG-AWG-Panel] Режим: %s\n' "$SCHEME"
  printf '[SG-AWG-Panel] Домен: %s\n' "${DOMAIN:-не задан}"
  printf '[SG-AWG-Panel] Порт панели: %s\n' "$PORT"
} >> "$LOG_FILE"

log_phase "Проверка Nginx, порта и необходимых пакетов"
args=(
  /bin/bash "$PROJECT_DIR/deploy/configure-panel-access.sh"
  --scheme "$SCHEME"
  --port "$PORT"
  --manage-placeholder "$MANAGE_PLACEHOLDER"
)
[[ -n "$DOMAIN" ]] && args+=(--domain "$DOMAIN")

# Certbot is a Python application. Force unbuffered output so every line is
# visible in the browser terminal while certificate issuance is still running.
export PYTHONUNBUFFERED=1
set +e
if command -v stdbuf >/dev/null 2>&1; then
  stdbuf -oL -eL "${args[@]}" >> "$LOG_FILE" 2>&1
else
  "${args[@]}" >> "$LOG_FILE" 2>&1
fi
code=$?
set -e

if (( code != 0 )); then
  printf '[Ошибка] Настройка не применена. Предыдущее состояние восстановлено.\n' >> "$LOG_FILE"
  error_code="panel_access_failed"
  retry_after=""
  message="Настройка не выполнена. Предыдущий рабочий доступ сохранён."
  if grep -Fqi "too many certificates (5) already issued for this exact set of identifiers" "$LOG_FILE"; then
    error_code="letsencrypt_rate_limit_exact_set"
    message="Let's Encrypt временно запретил новый выпуск: для этого домена уже выдано 5 сертификатов за 7 дней."
    retry_after="$(python3 - "$LOG_FILE" <<'PY'
from pathlib import Path
import re, sys
text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
patterns = (
    r"retry after\s+([^\n,;]+?\s+UTC)(?:\s|$)",
    r"retry after\s+([0-9T:+.-]+Z)",
    r"retry after\s+([0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9:.+-]+)",
)
for pattern in patterns:
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        print(match.group(1).strip())
        break
PY
)"
    printf '%s\n' '[Объяснение] Это временный лимит Let’s Encrypt: для этого же домена уже выпущено 5 сертификатов за последние 7 дней.' >> "$LOG_FILE"
    printf '%s\n' '[Объяснение] Это не ошибка SG-AWG-Panel, DNS или Nginx. Повторные запросы до окончания лимита не помогут.' >> "$LOG_FILE"
    if [[ -n "$retry_after" ]]; then
      printf '[Повтор] Новый запрос можно выполнить после: %s\n' "$retry_after" >> "$LOG_FILE"
    fi
  fi
  write_status error "$message" "$code" "$error_code" "$retry_after" 1
  publish_transition_status || true
  exit "$code"
fi

printf '[Готово] Новый адрес панели: %s\n' "$TARGET_URL" >> "$LOG_FILE"
write_status success "Публичный адрес настроен. Проверяем запуск backend." 0
publish_transition_status || true

# The environment changed (including Secure cookies). Restart the backend only
# after the successful status has been written, so the new URL can finish the
# transition on a fresh login page.
systemd-run \
  --unit="sg-awg-panel-restart-${JOB_TOKEN:0:8}" \
  --collect --on-active=3s \
  /bin/systemctl restart sg-awg-panel.service >/dev/null 2>&1 || true
