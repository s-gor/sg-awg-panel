#!/usr/bin/env bash
set -Eeuo pipefail
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/v\1/p' "$SOURCE_DIR/awgpanel/__init__.py" | head -n 1)"
[[ -n "$VERSION" ]] || { echo "Cannot determine candidate version" >&2; exit 1; }
exec env \
  SG_AWG_PANEL_VERSION="$VERSION" \
  SG_AWG_PANEL_SOURCE_DIR="$SOURCE_DIR" \
  bash "$SOURCE_DIR/deploy/update-from-github.sh"
