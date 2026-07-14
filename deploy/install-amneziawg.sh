#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/install-common.sh
. "$SCRIPT_DIR/install-common.sh"

install_log_init
require_root
require_supported_ubuntu
require_supported_architecture
require_no_pending_reboot

KERNEL="$(uname -r)"

enable_ubuntu_source_repositories() {
  python3 - <<'PYAPT'
from __future__ import annotations

import re
from pathlib import Path


def is_ubuntu_archive(text: str) -> bool:
    lowered = text.lower()
    return "ubuntu.com/ubuntu" in lowered or "ubuntu.com/ubuntu-ports" in lowered


found = False

# Ubuntu 24.04 uses deb822 .sources files. Add deb-src only to official
# Ubuntu archive stanzas and preserve every other field unchanged.
for path in sorted(Path("/etc/apt/sources.list.d").glob("*.sources")):
    original = path.read_text(encoding="utf-8")
    stanzas = re.split(r"(\n\s*\n)", original)
    changed = False
    for index in range(0, len(stanzas), 2):
        stanza = stanzas[index]
        types_match = re.search(r"(?m)^Types:\s*(.+)$", stanza)
        uris_match = re.search(r"(?m)^URIs:\s*(.+)$", stanza)
        if not types_match or not uris_match or not is_ubuntu_archive(uris_match.group(1)):
            continue
        types = types_match.group(1).split()
        if "deb" not in types:
            continue
        found = True
        if "deb-src" not in types:
            types.append("deb-src")
            replacement = "Types: " + " ".join(types)
            stanza = stanza[:types_match.start()] + replacement + stanza[types_match.end():]
            stanzas[index] = stanza
            changed = True
    if changed:
        path.write_text("".join(stanzas), encoding="utf-8")

# Fallback for Ubuntu 22.04 and images still using one-line sources.
source_lines: list[str] = []
list_paths = [Path("/etc/apt/sources.list")]
list_paths.extend(sorted(Path("/etc/apt/sources.list.d").glob("*.list")))
for path in list_paths:
    if not path.exists():
        continue
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped.startswith("deb ") or not is_ubuntu_archive(stripped):
            continue
        found = True
        source_lines.append(re.sub(r"^deb(\s+)", r"deb-src\1", stripped, count=1))

if source_lines:
    target = Path("/etc/apt/sources.list.d/sg-awg-ubuntu-deb-src.list")
    target.write_text("\n".join(dict.fromkeys(source_lines)) + "\n", encoding="utf-8")

if not found:
    raise SystemExit("не найдены официальные репозитории Ubuntu для включения deb-src")
PYAPT
}

wait_for_apt
run_logged "Подготовка пакетной системы..." dpkg --configure -a
run_logged "Обновление списка пакетов..." apt-get -o Dpkg::Use-Pty=0 update -qq
run_logged "Установка системных зависимостей..." env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get -o Dpkg::Use-Pty=0 install -y -qq \
  software-properties-common python3-launchpadlib gnupg2 \
  "linux-headers-${KERNEL}" iptables nftables conntrack iproute2 util-linux ca-certificates curl tar psmisc

run_logged "Включение исходных репозиториев Ubuntu..." \
  enable_ubuntu_source_repositories
run_logged "Подключение официального репозитория Amnezia..." \
  add-apt-repository -y --no-update ppa:amnezia/ppa
run_logged "Обновление списка пакетов Amnezia..." apt-get -o Dpkg::Use-Pty=0 update -qq
if ! apt-cache showsrc linux-base >/dev/null 2>&1; then
  install_fail "исходные репозитории Ubuntu не включены; DKMS AmneziaWG не сможет получить исходники ядра"
fi
run_logged "Установка AmneziaWG..." env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get -o Dpkg::Use-Pty=0 install -y -qq amneziawg

command -v awg >/dev/null 2>&1 || install_fail "после установки не найдена команда awg"
command -v awg-quick >/dev/null 2>&1 || install_fail "после установки не найдена команда awg-quick"
run_logged "Загрузка модуля AmneziaWG..." modprobe amneziawg

install -d -m 0700 /etc/amnezia/amneziawg
cat > /etc/sysctl.d/90-sg-awg-panel.conf <<'SYSCTL'
net.ipv4.ip_forward=1
net.ipv4.conf.all.src_valid_mark=1
SYSCTL
run_logged "Включение IPv4 forwarding..." sysctl --system

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

run_logged "Регистрация службы AWG..." systemctl daemon-reload
run_logged "Включение автозапуска AWG..." systemctl enable sg-awg-server.service
install_info "AmneziaWG установлен"
