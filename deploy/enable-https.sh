#!/usr/bin/env bash
set -Eeuo pipefail
DOMAIN=""
PORT="62443"
MANAGE_PLACEHOLDER="1"
while (($#)); do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --manage-placeholder) MANAGE_PLACEHOLDER="${2:-}"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done
exec /bin/bash /opt/sg-awg-panel/deploy/configure-panel-access.sh \
  --scheme https --domain "$DOMAIN" --port "$PORT" \
  --manage-placeholder "$MANAGE_PLACEHOLDER"
