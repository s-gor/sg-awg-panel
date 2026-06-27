#!/usr/bin/env bash
set -Eeuo pipefail
VERSION="${SG_AWG_PANEL_VERSION:-v0.1.0-alpha7}"
URL="https://github.com/s-gor/sg-awg-panel/archive/refs/tags/${VERSION}.tar.gz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

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
wait_for_apt
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates tar psmisc
curl -fL "$URL" -o "$TMP/source.tar.gz"
tar -xzf "$TMP/source.tar.gz" -C "$TMP"
DIR="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
[[ -n "$DIR" ]] || fail "downloaded archive is empty"
bash "$DIR/deploy/first-install.sh"
