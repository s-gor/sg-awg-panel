#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="${SG_AWG_PANEL_VERSION:-v0.1.0-alpha3}"
URL="https://github.com/s-gor/sg-awg-panel/archive/refs/tags/${VERSION}.tar.gz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

log(){ printf '[SG-AWG-Panel] %s\n' "$*"; }
fail(){ printf '[SG-AWG-Panel] ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "run as root"
command -v awg >/dev/null 2>&1 || fail "AmneziaWG is not installed; use install-from-github.sh for the first installation"
command -v curl >/dev/null 2>&1 || fail "curl is not installed"

log "Downloading ${VERSION}"
curl -fL "$URL" -o "$TMP/source.tar.gz"
tar -xzf "$TMP/source.tar.gz" -C "$TMP"
DIR="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
[[ -n "$DIR" ]] || fail "downloaded archive is empty"
bash "$DIR/install-or-upgrade.sh"
