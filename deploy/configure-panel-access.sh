#!/usr/bin/env bash
set -Eeuo pipefail

SCHEME="http"
DOMAIN=""
EMAIL=""
PUBLIC_PORT="8080"
BACKEND_PORT="18080"
ENV_FILE="/etc/sg-awg-panel/web.env"
SITE_FILE="/etc/nginx/sites-available/sg-awg-panel"
ACME_ROOT="/var/www/sg-awg-panel-acme"

log(){ printf '[SG-AWG-Panel Access] %s\n' "$*"; }
fail(){ printf '[SG-AWG-Panel Access] ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --scheme) SCHEME="${2:-}"; shift 2 ;;
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --email) EMAIL="${2:-}"; shift 2 ;;
    --port) PUBLIC_PORT="${2:-}"; shift 2 ;;
    --backend-port) BACKEND_PORT="${2:-}"; shift 2 ;;
    *) fail "Unknown argument: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || fail "run as root"
[[ "$SCHEME" == "http" || "$SCHEME" == "https" ]] || fail "scheme must be http or https"
[[ "$PUBLIC_PORT" =~ ^[0-9]+$ ]] && (( PUBLIC_PORT >= 1 && PUBLIC_PORT <= 65535 )) || fail "invalid public port"
[[ "$BACKEND_PORT" =~ ^[0-9]+$ ]] && (( BACKEND_PORT >= 1 && BACKEND_PORT <= 65535 )) || fail "invalid backend port"
[[ "$PUBLIC_PORT" != "$BACKEND_PORT" ]] || fail "public and backend ports must be different"
[[ -f "$ENV_FILE" ]] || fail "missing $ENV_FILE"
if [[ -n "$DOMAIN" ]]; then
  [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || fail "invalid domain"
fi
if [[ "$SCHEME" == "https" ]]; then
  [[ -n "$DOMAIN" ]] || fail "HTTPS requires --domain"
  [[ "$DOMAIN" == *.* ]] || fail "use a full domain name"
  [[ -n "$EMAIL" ]] || fail "HTTPS requires --email"
  [[ "$EMAIL" == *@*.* ]] || fail "invalid email"
  (( PUBLIC_PORT != 80 )) || fail "HTTPS public port cannot be 80"
fi

wait_for_apt(){
  local waited=0 timeout="${APT_LOCK_TIMEOUT:-900}"
  local locks=(/var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock)
  while command -v fuser >/dev/null 2>&1 && fuser "${locks[@]}" >/dev/null 2>&1; do
    (( waited == 0 )) && log "Waiting for real apt/dpkg locks"
    (( waited >= timeout )) && fail "apt/dpkg locks were not released"
    sleep 5; waited=$((waited + 5))
  done
}

packages=(nginx)
[[ "$SCHEME" == "https" ]] && packages+=(certbot)
missing=()
for package in "${packages[@]}"; do dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed' || missing+=("$package"); done
if ((${#missing[@]})); then
  wait_for_apt
  dpkg --configure -a
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
fi

install -d -m 0755 "$ACME_ROOT/.well-known/acme-challenge" /etc/nginx/sites-available /etc/nginx/sites-enabled
SERVER_NAME="${DOMAIN:-_}"

proxy_block(){
  cat <<EOF
    location / {
        proxy_pass http://127.0.0.1:${BACKEND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
        client_max_body_size 1m;
    }
EOF
}

if [[ "$SCHEME" == "http" ]]; then
  {
    cat <<EOF
server {
    listen ${PUBLIC_PORT};
    listen [::]:${PUBLIC_PORT};
    server_name ${SERVER_NAME};
EOF
    proxy_block
    echo "}"
  } > "$SITE_FILE"
else
  # HTTP challenge server must exist before requesting the certificate.
  cat > "$SITE_FILE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    location /.well-known/acme-challenge/ { root ${ACME_ROOT}; }
    location / { return 200 'SG-AWG-Panel certificate setup'; add_header Content-Type text/plain; }
}
EOF
  ln -sfn "$SITE_FILE" /etc/nginx/sites-enabled/sg-awg-panel
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl enable --now nginx
  systemctl reload nginx

  if [[ ! -s "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" || ! -s "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" ]]; then
    certbot certonly --webroot -w "$ACME_ROOT" --non-interactive --agree-tos -m "$EMAIL" -d "$DOMAIN"
  fi

  REDIRECT_TARGET="https://\$host"
  (( PUBLIC_PORT == 443 )) || REDIRECT_TARGET="https://\$host:${PUBLIC_PORT}"
  cat > "$SITE_FILE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    location /.well-known/acme-challenge/ { root ${ACME_ROOT}; }
    location / { return 301 ${REDIRECT_TARGET}\$request_uri; }
}

server {
    listen ${PUBLIC_PORT} ssl;
    listen [::]:${PUBLIC_PORT} ssl;
    server_name ${DOMAIN};
    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;
    add_header X-Frame-Options DENY always;
EOF
  proxy_block >> "$SITE_FILE"
  echo "}" >> "$SITE_FILE"
fi

ln -sfn "$SITE_FILE" /etc/nginx/sites-enabled/sg-awg-panel
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx

python3 - "$ENV_FILE" "$SCHEME" "$DOMAIN" "$PUBLIC_PORT" "$EMAIL" "$BACKEND_PORT" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
scheme, domain, public_port, email, backend_port = sys.argv[2:]
updates = {
    "AWGPANEL_BIND_ADDRESS": "127.0.0.1",
    "AWGPANEL_PORT": backend_port,
    "AWGPANEL_BACKEND_PORT": backend_port,
    "AWGPANEL_PUBLIC_SCHEME": scheme,
    "AWGPANEL_PUBLIC_HOST": domain,
    "AWGPANEL_PUBLIC_PORT": public_port,
    "AWGPANEL_HTTPS_EMAIL": email,
    "AWGPANEL_SECURE_COOKIES": "1" if scheme == "https" else "0",
    "AWGPANEL_TRUST_PROXY_HEADERS": "1",
}
lines = path.read_text(encoding="utf-8").splitlines()
out=[]; seen=set()
for line in lines:
    key = line.split("=",1)[0] if "=" in line else ""
    if key in updates:
        out.append(f"{key}={updates[key]}"); seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen: out.append(f"{key}={value}")
path.write_text("\n".join(out)+"\n", encoding="utf-8")
PY
chmod 600 "$ENV_FILE"

if [[ -x /opt/sg-awg-panel/.venv/bin/python ]]; then
  DB_PATH="$(grep -E '^AWGPANEL_DB=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
  DB_PATH="${DB_PATH#'}"; DB_PATH="${DB_PATH%'}"; DB_PATH="${DB_PATH:-/var/lib/sg-awg-panel/panel.db}"
  AWGPANEL_DB="$DB_PATH" PANEL_SCHEME="$SCHEME" PANEL_DOMAIN="$DOMAIN" PANEL_PORT="$PUBLIC_PORT" PANEL_EMAIL="$EMAIL" /opt/sg-awg-panel/.venv/bin/python - <<'PYDB'
import os
from awgpanel.db import connect, init_db
init_db()
with connect() as con:
    con.execute("""UPDATE panel_settings SET public_scheme=?, public_host=?, public_port=?, https_email=?, https_enabled=?, backend_address='127.0.0.1', backend_port=18080, updated_at=CURRENT_TIMESTAMP WHERE id=1""", (
        os.environ['PANEL_SCHEME'], os.environ['PANEL_DOMAIN'], int(os.environ['PANEL_PORT']), os.environ['PANEL_EMAIL'], 1 if os.environ['PANEL_SCHEME']=='https' else 0
    ))
PYDB
fi

log "Ready: ${SCHEME}://${DOMAIN:-SERVER_IP}:${PUBLIC_PORT}"
