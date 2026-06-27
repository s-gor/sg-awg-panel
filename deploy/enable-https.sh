#!/usr/bin/env bash
set -Eeuo pipefail

DOMAIN=""
EMAIL=""
ENV_FILE="/etc/sg-awg-panel/web.env"
SITE_FILE="/etc/nginx/sites-available/sg-awg-panel"

log(){ printf '[SG-AWG-Panel HTTPS] %s\n' "$*"; }
fail(){ printf '[SG-AWG-Panel HTTPS] ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --email) EMAIL="${2:-}"; shift 2 ;;
    *) fail "Unknown argument: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || fail "run as root"
[[ -n "$DOMAIN" ]] || fail "use --domain panel.example.com"
[[ -n "$EMAIL" ]] || fail "use --email you@example.com"
[[ -f "$ENV_FILE" ]] || fail "SG-AWG-Panel is not installed"
[[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || fail "invalid domain"

locks=(/var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock)
waited=0
while command -v fuser >/dev/null 2>&1 && fuser "${locks[@]}" >/dev/null 2>&1; do
  (( waited == 0 )) && log "Waiting for apt/dpkg locks"
  (( waited >= 900 )) && fail "apt/dpkg locks were not released"
  sleep 5
  waited=$((waited + 5))
done

dpkg --configure -a
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot python3-certbot-nginx

python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
updates = {
    "AWGPANEL_BIND_ADDRESS": "127.0.0.1",
    "AWGPANEL_SECURE_COOKIES": "1",
    "AWGPANEL_TRUST_PROXY_HEADERS": "1",
}
lines = path.read_text(encoding="utf-8").splitlines()
seen = set()
out = []
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
PY
chmod 600 "$ENV_FILE"

cat > "$SITE_FILE" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
        client_max_body_size 1m;
    }
}
NGINX
ln -sfn "$SITE_FILE" /etc/nginx/sites-enabled/sg-awg-panel
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl restart sg-awg-panel
certbot --nginx --non-interactive --agree-tos --redirect -m "$EMAIL" -d "$DOMAIN"
nginx -t
systemctl reload nginx

log "Ready: https://$DOMAIN"
log "You can now close public TCP 8080 in the cloud firewall."
