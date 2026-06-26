#!/usr/bin/env bash
set -Eeuo pipefail
VERSION="${SG_AWG_PANEL_VERSION:-v0.1.0-alpha1}"
URL="https://github.com/s-gor/sg-awg-panel/archive/refs/tags/${VERSION}.tar.gz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates tar
curl -fL "$URL" -o "$TMP/source.tar.gz"
tar -xzf "$TMP/source.tar.gz" -C "$TMP"
DIR="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
bash "$DIR/deploy/first-install.sh"
