#!/usr/bin/env bash
set -Eeuo pipefail
DOMAIN=""; EMAIL=""; PORT="443"
while (($#)); do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --email) EMAIL="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done
exec /bin/bash /opt/sg-awg-panel/deploy/configure-panel-access.sh \
  --scheme https --domain "$DOMAIN" --email "$EMAIL" --port "$PORT"
