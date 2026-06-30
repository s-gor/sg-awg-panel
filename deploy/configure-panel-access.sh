#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/install-common.sh
. "$SCRIPT_DIR/install-common.sh"

SCHEME="http"
DOMAIN=""
PUBLIC_PORT="62443"
BACKEND_PORT="18080"
MANAGE_PLACEHOLDER="1"
ENV_FILE="/etc/sg-awg-panel/web.env"
PANEL_SITE="/etc/nginx/sites-available/sg-awg-panel.conf"
PANEL_LINK="/etc/nginx/sites-enabled/sg-awg-panel.conf"
PLACEHOLDER_SITE="/etc/nginx/sites-available/sg-awg-placeholder.conf"
PLACEHOLDER_LINK="/etc/nginx/sites-enabled/sg-awg-placeholder.conf"
LEGACY_SITE="/etc/nginx/sites-available/sg-awg-panel"
LEGACY_LINK="/etc/nginx/sites-enabled/sg-awg-panel"
ACME_ROOT="/var/www/sg-awg-acme"
PLACEHOLDER_ROOT="/var/www/sg-awg-placeholder"
UPDATE_ROOT="/var/www/sg-awg-update"
ROLLBACK_DIR="$(mktemp -d /tmp/sg-awg-access.XXXXXX)"
NGINX_WAS_ACTIVE=0
COMMITTED=0

log(){ printf '[SG-AWG-Panel Access] %s\n' "$*"; }
fail(){ printf '[SG-AWG-Panel Access] ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --scheme) SCHEME="${2:-}"; shift 2 ;;
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --port) PUBLIC_PORT="${2:-}"; shift 2 ;;
    --backend-port) BACKEND_PORT="${2:-}"; shift 2 ;;
    --manage-placeholder) MANAGE_PLACEHOLDER="${2:-}"; shift 2 ;;
    *) fail "Unknown argument: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || fail "run as root"
[[ "$SCHEME" == "http" || "$SCHEME" == "https" ]] || fail "scheme must be http or https"
[[ "$PUBLIC_PORT" =~ ^[0-9]+$ ]] && (( PUBLIC_PORT >= 49152 && PUBLIC_PORT <= 65535 )) || fail "panel port must be in dynamic range 49152-65535"
[[ "$BACKEND_PORT" =~ ^[0-9]+$ ]] && (( BACKEND_PORT >= 1 && BACKEND_PORT <= 65535 )) || fail "invalid backend port"
[[ "$MANAGE_PLACEHOLDER" == "0" || "$MANAGE_PLACEHOLDER" == "1" ]] || fail "manage-placeholder must be 0 or 1"
[[ "$PUBLIC_PORT" != "$BACKEND_PORT" ]] || fail "public and backend ports must be different"
[[ -f "$ENV_FILE" ]] || fail "missing $ENV_FILE"


if [[ -n "$DOMAIN" ]]; then
  [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || fail "invalid domain"
fi
DOMAIN_RE="${DOMAIN//./\.}"
if [[ "$SCHEME" == "https" ]]; then
  [[ -n "$DOMAIN" ]] || fail "HTTPS requires --domain"
  [[ "$DOMAIN" == *.* ]] || fail "use a full domain name"
fi

backup_path(){
  local path="$1" name="$2"
  if [[ -e "$path" || -L "$path" ]]; then
    cp -a "$path" "$ROLLBACK_DIR/$name"
    : > "$ROLLBACK_DIR/$name.exists"
  fi
}
restore_path(){
  local path="$1" name="$2"
  rm -rf "$path"
  if [[ -f "$ROLLBACK_DIR/$name.exists" ]]; then
    cp -a "$ROLLBACK_DIR/$name" "$path"
  fi
}

backup_path "$PANEL_SITE" panel-site
backup_path "$PANEL_LINK" panel-link
backup_path "$PLACEHOLDER_SITE" placeholder-site
backup_path "$PLACEHOLDER_LINK" placeholder-link
backup_path "$LEGACY_SITE" legacy-site
backup_path "$LEGACY_LINK" legacy-link
backup_path "$ENV_FILE" web-env
systemctl is-active --quiet nginx.service && NGINX_WAS_ACTIVE=1 || true

rollback(){
  local code=$?
  trap - ERR EXIT
  if (( ! COMMITTED )); then
    log "Configuration failed; restoring previous Nginx and panel access settings"
    restore_path "$PANEL_SITE" panel-site
    restore_path "$PANEL_LINK" panel-link
    restore_path "$PLACEHOLDER_SITE" placeholder-site
    restore_path "$PLACEHOLDER_LINK" placeholder-link
    restore_path "$LEGACY_SITE" legacy-site
    restore_path "$LEGACY_LINK" legacy-link
    restore_path "$ENV_FILE" web-env
    if command -v nginx >/dev/null 2>&1; then
      nginx -t >/dev/null 2>&1 || true
      if (( NGINX_WAS_ACTIVE )); then
        systemctl restart nginx.service >/dev/null 2>&1 || true
      else
        systemctl stop nginx.service >/dev/null 2>&1 || true
      fi
    fi
  fi
  rm -rf "$ROLLBACK_DIR"
  exit "$code"
}
trap rollback ERR EXIT

wait_for_apt(){
  local waited=0 timeout="${APT_LOCK_TIMEOUT:-900}"
  local locks=(/var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock)
  while command -v fuser >/dev/null 2>&1 && fuser "${locks[@]}" >/dev/null 2>&1; do
    (( waited == 0 )) && log "Waiting for real apt/dpkg locks"
    (( waited >= timeout )) && fail "apt/dpkg locks were not released"
    sleep 5; waited=$((waited + 5))
  done
}

log "Проверка необходимых пакетов"
packages=(nginx)
[[ "$SCHEME" == "https" && "$MANAGE_PLACEHOLDER" == "1" ]] && packages+=(certbot)
missing=()
for package in "${packages[@]}"; do
  dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed' || missing+=("$package")
done
if ((${#missing[@]})); then
  wait_for_apt
  dpkg --configure -a
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
fi

log "Подготовка каталогов и конфигурации Nginx"
install -d -m 0755 /etc/nginx/sites-available /etc/nginx/sites-enabled
install -d -m 0755 "$ACME_ROOT/.well-known/acme-challenge" "$PLACEHOLDER_ROOT" "$UPDATE_ROOT"
if [[ ! -s "$PLACEHOLDER_ROOT/index.html" ]]; then
  cat > "$PLACEHOLDER_ROOT/index.html" <<'HTML'
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Welcome</title></head>
<body><main><h1>Welcome</h1><p>This web server is running normally.</p></main></body>
</html>
HTML
  chmod 0644 "$PLACEHOLDER_ROOT/index.html"
fi

cat > "$UPDATE_ROOT/offline.html" <<'HTML'
<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>SG-AWG-Panel</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:system-ui,sans-serif;background:#eef2f7;color:#152033}main{width:min(92vw,680px);background:#fff;border-radius:18px;padding:32px;box-shadow:0 18px 50px rgba(20,32,50,.12)}h1{margin:0 0 10px}p{color:#5a6676;line-height:1.55}.status{border:1px solid #dce3ec;border-radius:12px;padding:16px;margin:20px 0}.address{display:grid;gap:5px;margin:16px 0}.address code{overflow-wrap:anywhere}.buttons{display:flex;gap:10px;flex-wrap:wrap}.button{border:0;border-radius:9px;padding:11px 15px;background:#e8edf5;color:#152033;text-decoration:none;font-weight:700;cursor:pointer}.button.primary{background:#2457d6;color:#fff}.ok{color:#146c43}.error{color:#a22121}details{margin-top:22px}pre{white-space:pre-wrap;background:#f6f8fb;border-radius:12px;padding:16px;max-height:220px;overflow:auto}</style></head>
<body><main><h1>Панель перезапускается</h1><p>Это ожидаемо во время обновления или смены домена. Автоматическая проверка выполняется один раз; кнопки ниже остаются доступны всегда.</p><div class="status"><strong id="message">Проверяем запуск backend</strong><p id="countdown">Автоматическая проверка через 30 с.</p></div><div class="address"><span>Новый адрес</span><code id="target"></code></div><div class="buttons"><button class="button" id="check" type="button">Проверить сейчас</button><a class="button primary" id="open" href="/login" hidden>Открыть страницу входа</a></div><details><summary>Ход выполнения</summary><pre id="state">Ожидание статуса…</pre></details></main>
<script>(()=>{const state=document.getElementById('state'),msg=document.getElementById('message'),count=document.getElementById('countdown'),target=document.getElementById('target'),open=document.getElementById('open'),check=document.getElementById('check');let targetUrl=location.origin,seconds=30,autoUsed=false,redirectUsed=false,terminal=false;target.textContent=targetUrl;function login(){return targetUrl.replace(/\/$/,'')+'/login'}function ready(auto){msg.textContent='Backend запущен';msg.className='ok';count.textContent='Новый адрес готов. Используйте кнопку, если переход не произошёл.';open.href=login();open.hidden=false;if(auto&&!redirectUsed){redirectUsed=true;setTimeout(()=>location.replace(login()),1800)}}async function probe(auto=false){msg.textContent='Проверяем запуск backend';count.textContent='Проверяется новый адрес…';try{await fetch(targetUrl.replace(/\/$/,'')+'/health?t='+Date.now(),{cache:'no-store',mode:'no-cors'});ready(auto)}catch(e){count.textContent='Backend пока не ответил. Нажмите «Проверить сейчас» ещё раз.'}}check.addEventListener('click',()=>probe(false));const timer=setInterval(()=>{seconds--;count.textContent=seconds>0?'Автоматическая проверка через '+seconds+' с.':'Проверяется новый адрес…';if(seconds<=0){clearInterval(timer);autoUsed=true;probe(true)}},1000);async function pollLog(){try{const r=await fetch('/sg-awg-update/update.log?t='+Date.now(),{cache:'no-store'});if(r.ok){const t=await r.text();if(t.trim()){state.textContent=t.slice(-32000);state.scrollTop=state.scrollHeight}}}catch(e){}}setInterval(pollLog,1000);pollLog();async function poll(){try{const r=await fetch('/sg-awg-update/status.json?t='+Date.now(),{cache:'no-store'});if(r.ok){const d=await r.json();if(d.targetUrl){targetUrl=String(d.targetUrl).replace(/\/$/,'');target.textContent=targetUrl;open.href=login()}if(d.state==='success'&&!autoUsed){msg.textContent='Проверяем запуск backend'}if(d.state==='rolled_back'||d.state==='error'){terminal=true;msg.textContent=d.restored?'Предыдущая рабочая конфигурация восстановлена':'Операция не завершена';msg.className='error';count.textContent='Откройте прежний адрес панели или повторите настройку позднее.';open.href=login();open.hidden=false;return}}}catch(e){}if(!terminal)setTimeout(poll,1800)}poll()})();</script></body></html>
HTML
chmod 0644 "$UPDATE_ROOT/offline.html"
if [[ ! -s "$UPDATE_ROOT/status.json" ]]; then
  printf '%s\n' '{"state":"idle","message":""}' > "$UPDATE_ROOT/status.json"
fi
chmod 0644 "$UPDATE_ROOT/status.json"

log "Проверка выбранного TCP-порта ${PUBLIC_PORT}"
# Reject a port already owned by a non-Nginx process. Existing Nginx listeners
# are checked against enabled site files below.
if command -v ss >/dev/null 2>&1; then
  PORT_LISTENERS="$(ss -H -ltnp "sport = :${PUBLIC_PORT}" 2>/dev/null || true)"
  if [[ -n "$PORT_LISTENERS" && "$PORT_LISTENERS" != *nginx* ]]; then
    fail "TCP port ${PUBLIC_PORT} is already occupied by another process. Choose another panel port."
  fi
fi

# The selected TCP port must not already belong to another Nginx site.
for enabled in /etc/nginx/sites-enabled/*; do
  [[ -e "$enabled" || -L "$enabled" ]] || continue
  resolved="$(readlink -f "$enabled" 2>/dev/null || printf '%s' "$enabled")"
  case "$resolved" in
    "$PANEL_SITE"|"$PLACEHOLDER_SITE"|"$LEGACY_SITE") continue ;;
  esac
  if grep -Eq "^[[:space:]]*listen[[:space:]]+([^;[:space:]]*:)?${PUBLIC_PORT}([[:space:]][^;]*)?;" "$resolved" 2>/dev/null; then
    fail "TCP port ${PUBLIC_PORT} is already used by another Nginx site: ${enabled}. Choose another panel port."
  fi
done

CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
CERT_FULLCHAIN="${CERT_DIR}/fullchain.pem"
CERT_PRIVATE="${CERT_DIR}/privkey.pem"

proxy_block(){
  cat <<EOF_PROXY
    location ^~ /sg-awg-update/ {
        alias ${UPDATE_ROOT}/;
        add_header Cache-Control "no-store" always;
    }

    error_page 502 503 504 = /sg-awg-update/offline.html;

    location / {
        proxy_pass http://127.0.0.1:${BACKEND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port \$server_port;
        # Required for the real-time HTTPS/Certbot event stream.
        proxy_buffering off;
        proxy_cache off;
        gzip off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        client_max_body_size 1m;
    }
EOF_PROXY
}

rm -f "$LEGACY_LINK" "$LEGACY_SITE"

if [[ "$SCHEME" == "https" ]]; then
  if [[ "$MANAGE_PLACEHOLDER" == "0" ]]; then
    [[ -s "$CERT_FULLCHAIN" && -s "$CERT_PRIVATE" ]] || fail "Placeholder management is disabled, but no existing certificate was found for ${DOMAIN}. Enable the 443 placeholder once, or install the certificate separately."
    rm -f "$PLACEHOLDER_LINK" "$PLACEHOLDER_SITE"
  else
    # Do not collide with another project already managing this domain on 443.
    for enabled in /etc/nginx/sites-enabled/*; do
      [[ -e "$enabled" || -L "$enabled" ]] || continue
      resolved="$(readlink -f "$enabled" 2>/dev/null || printf '%s' "$enabled")"
      case "$resolved" in
        "$PANEL_SITE"|"$PLACEHOLDER_SITE"|"$LEGACY_SITE") continue ;;
      esac
      if grep -Eq "^[[:space:]]*listen[[:space:]]+([^;[:space:]]*:)?443([[:space:]][^;]*)?;" "$resolved" 2>/dev/null \
         && grep -Eq "^[[:space:]]*server_name[[:space:]]+([^;]*[[:space:]])?${DOMAIN_RE}([[:space:]]|;)" "$resolved" 2>/dev/null; then
        fail "Another Nginx site already manages ${DOMAIN} on TCP 443. Disable 'manage placeholder' and reuse its certificate."
      fi
    done

    log "Подготовка HTTP challenge на TCP 80"
    # Port 80 is used only for ACME and the harmless placeholder, never for the panel.
    cat > "$PLACEHOLDER_SITE" <<EOF_HTTP
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    root ${PLACEHOLDER_ROOT};

    location ^~ /.well-known/acme-challenge/ {
        root ${ACME_ROOT};
        default_type text/plain;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF_HTTP
    ln -sfn "$PLACEHOLDER_SITE" "$PLACEHOLDER_LINK"
    nginx -t
    systemctl enable --now nginx.service
    systemctl reload nginx.service

    if [[ ! -s "$CERT_FULLCHAIN" || ! -s "$CERT_PRIVATE" ]]; then
      log "Запрос сертификата Let's Encrypt для ${DOMAIN}"
      certbot certonly \
        --webroot -w "$ACME_ROOT" \
        --non-interactive --agree-tos --register-unsafely-without-email \
        --keep-until-expiring \
        -d "$DOMAIN"
    else
      log "Используется существующий сертификат для ${DOMAIN}"
    fi
    [[ -s "$CERT_FULLCHAIN" && -s "$CERT_PRIVATE" ]] || fail "Let's Encrypt certificate was not created for ${DOMAIN}"
    log "Сертификат готов"

    cat >> "$PLACEHOLDER_SITE" <<EOF_HTTPS

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${DOMAIN};
    root ${PLACEHOLDER_ROOT};

    ssl_certificate ${CERT_FULLCHAIN};
    ssl_certificate_key ${CERT_PRIVATE};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SGAWGPlaceholder:10m;

    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;
    add_header X-Frame-Options DENY always;

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF_HTTPS
  fi

  cat > "$PANEL_SITE" <<EOF_PANEL_HTTPS
server {
    listen ${PUBLIC_PORT} ssl;
    listen [::]:${PUBLIC_PORT} ssl;
    server_name ${DOMAIN};

    ssl_certificate ${CERT_FULLCHAIN};
    ssl_certificate_key ${CERT_PRIVATE};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SGAWGPanel:10m;

    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;
    add_header X-Frame-Options DENY always;
EOF_PANEL_HTTPS
  proxy_block >> "$PANEL_SITE"
  echo "}" >> "$PANEL_SITE"
else
  rm -f "$PLACEHOLDER_LINK" "$PLACEHOLDER_SITE"
  SERVER_NAME="${DOMAIN:-_}"
  cat > "$PANEL_SITE" <<EOF_PANEL_HTTP
server {
    listen ${PUBLIC_PORT};
    listen [::]:${PUBLIC_PORT};
    server_name ${SERVER_NAME};
EOF_PANEL_HTTP
  proxy_block >> "$PANEL_SITE"
  echo "}" >> "$PANEL_SITE"
fi

log "Проверка итоговой конфигурации Nginx"
ln -sfn "$PANEL_SITE" "$PANEL_LINK"
nginx -t
systemctl enable --now nginx.service
systemctl reload nginx.service

log "Сохранение настроек панели"
python3 - "$ENV_FILE" "$SCHEME" "$DOMAIN" "$PUBLIC_PORT" "$BACKEND_PORT" "$MANAGE_PLACEHOLDER" <<'PYENV'
from pathlib import Path
import sys
path = Path(sys.argv[1])
scheme, domain, public_port, backend_port, manage_placeholder = sys.argv[2:]
updates = {
    "AWGPANEL_BIND_ADDRESS": "127.0.0.1",
    "AWGPANEL_PORT": backend_port,
    "AWGPANEL_BACKEND_PORT": backend_port,
    "AWGPANEL_PUBLIC_SCHEME": scheme,
    "AWGPANEL_PUBLIC_HOST": domain,
    "AWGPANEL_PUBLIC_PORT": public_port,
    "AWGPANEL_MANAGE_PLACEHOLDER": manage_placeholder,
    "AWGPANEL_HTTPS_EMAIL": "",
    "AWGPANEL_SECURE_COOKIES": "1" if scheme == "https" else "0",
    "AWGPANEL_TRUST_PROXY_HEADERS": "1",
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

if [[ -x /opt/sg-awg-panel/.venv/bin/python ]]; then
  DB_PATH="$(grep -E '^AWGPANEL_DB=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
  DB_PATH="${DB_PATH#\'}"; DB_PATH="${DB_PATH%\'}"; DB_PATH="${DB_PATH:-/var/lib/sg-awg-panel/panel.db}"
  AWGPANEL_DB="$DB_PATH" PANEL_SCHEME="$SCHEME" PANEL_DOMAIN="$DOMAIN" PANEL_PORT="$PUBLIC_PORT" PANEL_PLACEHOLDER="$MANAGE_PLACEHOLDER" /opt/sg-awg-panel/.venv/bin/python - <<'PYDB'
import os
from awgpanel.db import connect, init_db
init_db()
with connect() as con:
    con.execute(
        """
        UPDATE panel_settings
        SET public_scheme=?, public_host=?, public_port=?, https_email='',
            https_enabled=?, manage_placeholder=?,
            backend_address='127.0.0.1', backend_port=18080,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=1
        """,
        (
            os.environ["PANEL_SCHEME"],
            os.environ["PANEL_DOMAIN"],
            int(os.environ["PANEL_PORT"]),
            1 if os.environ["PANEL_SCHEME"] == "https" else 0,
            int(os.environ["PANEL_PLACEHOLDER"]),
        ),
    )
PYDB
fi

COMMITTED=1
rm -rf "$ROLLBACK_DIR"
trap - ERR EXIT
DISPLAY_HOST="$DOMAIN"
if [[ -z "$DISPLAY_HOST" ]]; then
  DISPLAY_HOST="$(detect_public_ipv4)"
fi
if [[ -n "$DISPLAY_HOST" ]]; then
  log "Ready: ${SCHEME}://${DISPLAY_HOST}:${PUBLIC_PORT}"
else
  log "Ready on TCP port ${PUBLIC_PORT}; use the server public IP"
fi
if [[ "$SCHEME" == "https" ]]; then
  if [[ "$MANAGE_PLACEHOLDER" == "1" ]]; then
    log "Placeholder: https://${DOMAIN}:443"
  else
    log "TCP 443 placeholder is managed by another Nginx site"
  fi
fi
