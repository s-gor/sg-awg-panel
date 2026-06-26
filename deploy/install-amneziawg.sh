#!/usr/bin/env bash
set -Eeuo pipefail

log(){ printf '[SG-AWG-Panel] %s\n' "$*"; }
fail(){ printf '[SG-AWG-Panel] ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "run as root"
[[ -r /etc/os-release ]] || fail "cannot detect operating system"
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || fail "Alpha 1 supports Ubuntu only"
case "${VERSION_ID:-}" in
  22.04|24.04) ;;
  *) fail "Alpha 1 is intended for Ubuntu 22.04/24.04; found ${VERSION_ID:-unknown}" ;;
esac

KERNEL="$(uname -r)"
log "Ubuntu ${VERSION_ID}; kernel ${KERNEL}"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  software-properties-common python3-launchpadlib gnupg2 \
  "linux-headers-${KERNEL}" iptables ca-certificates

if ! grep -RqsE '^deb(-src)? .*ppa\.launchpad(content)?\.net/amnezia/ppa' \
  /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
  log "Adding Amnezia PPA"
  add-apt-repository -y ppa:amnezia/ppa
fi

for file in /etc/apt/sources.list.d/*amnezia*.sources; do
  [[ -f "$file" ]] || continue
  grep -q '^Types: deb$' "$file" && sed -i 's/^Types: deb$/Types: deb deb-src/' "$file" || true
done
for file in /etc/apt/sources.list.d/*amnezia*.list; do
  [[ -f "$file" ]] || continue
  if ! grep -q '^deb-src ' "$file"; then
    awk '/^deb /{line=$0; sub(/^deb /,"deb-src ",line); print line} {print}' "$file" > "$file.tmp"
    mv "$file.tmp" "$file"
  fi
done

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y amneziawg
command -v awg >/dev/null || fail "awg command not found"
command -v awg-quick >/dev/null || fail "awg-quick command not found"

if ! modprobe amneziawg; then
  dkms status || true
  journalctl -k -n 80 --no-pager || true
  fail "amneziawg module did not load; a reboot or another kernel may be required"
fi

install -d -m 0700 /etc/amnezia/amneziawg
cat > /etc/sysctl.d/90-sg-awg-panel.conf <<'SYSCTL'
net.ipv4.ip_forward=1
SYSCTL
sysctl --system >/dev/null

cat > /etc/systemd/system/sg-awg-server.service <<'UNIT'
[Unit]
Description=SG-AWG-Panel AmneziaWG server
After=network-online.target
Wants=network-online.target
ConditionPathExists=/etc/amnezia/amneziawg/awg0.conf

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/sbin/modprobe amneziawg
ExecStart=/usr/bin/awg-quick up /etc/amnezia/amneziawg/awg0.conf
ExecStop=/usr/bin/awg-quick down /etc/amnezia/amneziawg/awg0.conf
TimeoutStartSec=45
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable sg-awg-server.service >/dev/null
log "AmneziaWG installed; configure the server in the web panel"
