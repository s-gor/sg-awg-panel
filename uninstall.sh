#!/usr/bin/env bash
set -Eeuo pipefail

# Complete cleanup with the same live terminal progress style as install.sh.
# Removes SG-AWG-Panel, all panel data/backups, AmneziaWG/DKMS/PPA,
# Nginx/Certbot configuration and the packages installed specifically for the panel.

LOG_FILE="/root/sg-awg-panel-removal-$(date -u +%Y%m%d-%H%M%S).log"
ASSUME_YES=0
[[ "${1:-}" == "--yes" ]] && ASSUME_YES=1

# The uninstall progress intentionally uses the same live terminal style as install.sh.
STEP_LABEL=''
STEP_STARTED=0
STEP_ACTIVE=0
STEP_SPINNER_PID=''

info() {
  printf '[SG-AWG-Panel] %s\n' "$*"
}

stage() {
  printf '\n[SG-AWG-Panel] Этап %s/%s: %s\n' "$1" "$2" "$3"
}

stop_spinner() {
  if [[ -n "$STEP_SPINNER_PID" ]]; then
    kill "$STEP_SPINNER_PID" 2>/dev/null || true
    wait "$STEP_SPINNER_PID" 2>/dev/null || true
    STEP_SPINNER_PID=''
  fi
}

spinner_loop() {
  local label="$1"
  local started="$2"
  local frame_index=0 elapsed
  local frames='|/-\\'
  while true; do
    elapsed=$((SECONDS - started))
    printf '\r[SG-AWG-Panel] [%s] %s... (%s сек)' \
      "${frames:frame_index%4:1}" "$label" "$elapsed"
    frame_index=$((frame_index + 1))
    sleep 0.25
  done
}

step_begin() {
  stop_spinner
  STEP_LABEL="$1"
  STEP_STARTED=$SECONDS
  STEP_ACTIVE=1
  if [[ -t 1 ]]; then
    spinner_loop "$STEP_LABEL" "$STEP_STARTED" &
    STEP_SPINNER_PID=$!
  else
    printf '[SG-AWG-Panel] [..] %s...\n' "$STEP_LABEL"
  fi
}

step_ok() {
  local elapsed=$((SECONDS - STEP_STARTED))
  stop_spinner
  if [[ -t 1 ]]; then
    printf '\r[SG-AWG-Panel] [OK] %s... (%s сек)\033[K\n' "$STEP_LABEL" "$elapsed"
  else
    printf '[SG-AWG-Panel] [OK] %s... (%s сек)\n' "$STEP_LABEL" "$elapsed"
  fi
  STEP_ACTIVE=0
}

fail() {
  local message="$*"
  local elapsed=$((SECONDS - STEP_STARTED))
  if (( STEP_ACTIVE == 1 )); then
    stop_spinner
    if [[ -t 1 ]]; then
      printf '\r[SG-AWG-Panel] [ОШИБКА] %s... (%s сек)\033[K\n' \
        "$STEP_LABEL" "$elapsed" >&2
    else
      printf '[SG-AWG-Panel] [ОШИБКА] %s... (%s сек)\n' \
        "$STEP_LABEL" "$elapsed" >&2
    fi
    STEP_ACTIVE=0
  fi
  printf '[SG-AWG-Panel] [ERROR] %s\n' "$message" >&2
  if [[ -s "$LOG_FILE" ]]; then
    printf '\nПоследние строки журнала %s:\n' "$LOG_FILE" >&2
    tail -n 50 "$LOG_FILE" >&2 || true
  fi
  exit 1
}

cleanup_spinner() {
  stop_spinner
}
trap cleanup_spinner EXIT

on_error() {
  local code="$1" line="$2"
  trap - ERR
  fail "сбой в строке ${line}, код ${code}"
}
trap 'on_error "$?" "$LINENO"' ERR

run_logged() {
  local label="$1"
  shift
  printf '[SG-AWG-Panel] %s\n' "$label" >>"$LOG_FILE"
  "$@" >>"$LOG_FILE" 2>&1
}

run_logged_allow_fail() {
  local label="$1"
  shift
  printf '[SG-AWG-Panel] %s\n' "$label" >>"$LOG_FILE"
  "$@" >>"$LOG_FILE" 2>&1 || true
}

wait_for_apt() {
  local waited=0
  local timeout=900
  local locks=(
    /var/lib/dpkg/lock-frontend
    /var/lib/dpkg/lock
    /var/lib/apt/lists/lock
    /var/cache/apt/archives/lock
  )

  while command -v fuser >/dev/null 2>&1 \
    && fuser "${locks[@]}" >/dev/null 2>&1; do
    if (( waited == 0 )); then
      printf '[SG-AWG-Panel] Ожидание завершения apt/dpkg...\n' >>"$LOG_FILE"
    fi
    if (( waited >= timeout )); then
      fail "apt/dpkg не освободил блокировку за ${timeout} секунд"
    fi
    sleep 5
    waited=$((waited + 5))
  done
}

[[ $EUID -eq 0 ]] || fail "запустите скрипт через sudo"
[[ -r /etc/os-release ]] || fail "не удалось определить операционную систему"
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || fail "этот скрипт предназначен только для Ubuntu"

install -d -m 0700 "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"
chmod 0600 "$LOG_FILE"

cat <<'WARNING'

Будут безвозвратно удалены:
  - SG-AWG-Panel, база, пользователи, ключи и резервные копии;
  - службы sg-awg-* и конфигурация AWG;
  - AmneziaWG, DKMS-модуль и PPA Amnezia;
  - Nginx, Certbot и все их конфигурации/сертификаты на этом EC2;
  - системные файлы, созданные установщиком SG-AWG-Panel.

Скрипт рассчитан на отдельный чистый EC2, где Nginx и Certbot
не используются другими проектами.
WARNING

if (( ASSUME_YES == 0 )); then
  [[ -r /dev/tty && -w /dev/tty ]] \
    || fail "для подтверждения требуется обычная SSH-сессия"
  printf '\nДля продолжения введите: DELETE SG-AWG-PANEL COMPLETELY\n' >/dev/tty
  IFS= read -r answer </dev/tty
  [[ "$answer" == "DELETE SG-AWG-PANEL COMPLETELY" ]] || {
    info "Удаление отменено"
    exit 0
  }
fi

info "Журнал: $LOG_FILE"

# Leave a stable working directory before deleting application paths.
cd /

stage 1 4 "Остановка компонентов"
step_begin "Остановка служб и таймеров"
if command -v awg-quick >/dev/null 2>&1 \
  && [[ -f /etc/amnezia/amneziawg/awg0.conf ]]; then
  awg-quick down /etc/amnezia/amneziawg/awg0.conf >>"$LOG_FILE" 2>&1 || true
fi

for unit in \
  sg-awg-recovery.service \
  sg-awg-traffic-schedule.timer \
  sg-awg-clients-maintenance.timer \
  sg-awg-traffic-schedule.service \
  sg-awg-clients-maintenance.service \
  sg-awg-traffic.service \
  sg-awg-backup.timer \
  sg-awg-backup.service \
  sg-awg-server.service \
  sg-awg-panel.service; do
  systemctl disable --now "$unit" >>"$LOG_FILE" 2>&1 || true
done

step_ok
step_begin "Остановка Nginx и фоновых процессов"
# Stop Nginx before its package and configuration are removed.
systemctl disable --now nginx.service >>"$LOG_FILE" 2>&1 || true

# Terminate only a leftover process started from this project's virtualenv.
pkill -TERM -f '^/opt/sg-awg-panel/.venv/bin/(waitress-serve|python)' \
  >>"$LOG_FILE" 2>&1 || true
sleep 1
pkill -KILL -f '^/opt/sg-awg-panel/.venv/bin/(waitress-serve|python)' \
  >>"$LOG_FILE" 2>&1 || true

step_ok
step_begin "Отключение AWG-интерфейсов и Traffic Rules"
# Remove an interface even if the service/config shutdown was incomplete.
ip link delete awg0 >>"$LOG_FILE" 2>&1 || true
nft delete table inet sg_awg_traffic >>"$LOG_FILE" 2>&1 || true
nft delete table ip sg_awg_traffic_nat >>"$LOG_FILE" 2>&1 || true
for id in $(seq 1 32); do
  while ip rule del priority "$((12100 + id))" >>"$LOG_FILE" 2>&1; do :; done
  ip route flush table "$((21000 + id))" >>"$LOG_FILE" 2>&1 || true
  ip link delete "sgo${id}" >>"$LOG_FILE" 2>&1 || true
done
modprobe -r amneziawg >>"$LOG_FILE" 2>&1 || true
step_ok

stage 2 4 "Удаление системной интеграции"
step_begin "Удаление systemd-служб и таймеров"
rm -f \
  /etc/systemd/system/sg-awg-panel.service \
  /etc/systemd/system/sg-awg-server.service \
  /etc/systemd/system/sg-awg-backup.service \
  /etc/systemd/system/sg-awg-backup.timer \
  /etc/systemd/system/sg-awg-recovery.service \
  /etc/systemd/system/sg-awg-traffic.service \
  /etc/systemd/system/sg-awg-traffic-schedule.service \
  /etc/systemd/system/sg-awg-traffic-schedule.timer \
  /etc/systemd/system/sg-awg-clients-maintenance.service \
  /etc/systemd/system/sg-awg-clients-maintenance.timer

find /etc/systemd/system -type l \
  \( -name 'sg-awg-panel.service' \
     -o -name 'sg-awg-server.service' \
     -o -name 'sg-awg-backup.service' \
     -o -name 'sg-awg-backup.timer' \
     -o -name 'sg-awg-recovery.service' \
     -o -name 'sg-awg-traffic.service' \
     -o -name 'sg-awg-traffic-schedule.service' \
     -o -name 'sg-awg-traffic-schedule.timer' \
     -o -name 'sg-awg-traffic-lists.service' \
     -o -name 'sg-awg-traffic-lists.timer' \
     -o -name 'sg-awg-clients-maintenance.service' \
     -o -name 'sg-awg-clients-maintenance.timer' \) \
  -delete 2>/dev/null || true

systemctl daemon-reload >>"$LOG_FILE" 2>&1
systemctl reset-failed >>"$LOG_FILE" 2>&1 || true
step_ok

step_begin "Удаление файлов панели, данных и конфигурации Nginx"
rm -rf \
  /opt/sg-awg-panel \
  /etc/sg-awg-panel \
  /var/lib/sg-awg-panel \
  /root/sg-awg-panel-backups \
  /etc/amnezia/amneziawg \
  /var/www/sg-awg-panel-acme \
  /var/www/sg-awg-acme \
  /var/www/sg-awg-placeholder \
  /var/www/sg-awg-update \
  /tmp/sg-awg-access.*

rm -f \
  /var/log/sg-awg-panel-install.log \
  /etc/sysctl.d/90-sg-awg-panel.conf \
  /run/lock/sg-awg-panel-traffic.lock \
  /etc/dnsmasq.d/sg-awg-traffic.conf

step_ok

step_begin "Восстановление системных сетевых параметров и APT-источников"
# Restore the clean EC2 default used before the panel enabled forwarding.
sysctl -w net.ipv4.ip_forward=0 >>"$LOG_FILE" 2>&1 || true

printf '[SG-AWG-Panel] Удаление PPA и исходных репозиториев, добавленных установщиком...\n' >>"$LOG_FILE"
rm -f /etc/apt/sources.list.d/sg-awg-ubuntu-deb-src.list

# Ubuntu 24.04 uses deb822 files. The installer added deb-src to official
# Ubuntu archive stanzas. On this dedicated clean EC2 we restore Types: deb.
python3 - <<'PY' >>"$LOG_FILE" 2>&1
from __future__ import annotations

import re
from pathlib import Path


def is_official_ubuntu_archive(value: str) -> bool:
    lowered = value.lower()
    return "ubuntu.com/ubuntu" in lowered or "ubuntu.com/ubuntu-ports" in lowered


for path in sorted(Path("/etc/apt/sources.list.d").glob("*.sources")):
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"(\n\s*\n)", text)
    changed = False
    for index in range(0, len(parts), 2):
        stanza = parts[index]
        types_match = re.search(r"(?m)^Types:\s*(.+)$", stanza)
        uris_match = re.search(r"(?m)^URIs:\s*(.+)$", stanza)
        if not types_match or not uris_match:
            continue
        if not is_official_ubuntu_archive(uris_match.group(1)):
            continue
        types = types_match.group(1).split()
        if "deb-src" not in types:
            continue
        types = [item for item in types if item != "deb-src"]
        if "deb" not in types:
            types.insert(0, "deb")
        replacement = "Types: " + " ".join(dict.fromkeys(types))
        stanza = stanza[: types_match.start()] + replacement + stanza[types_match.end() :]
        parts[index] = stanza
        changed = True
    if changed:
        path.write_text("".join(parts), encoding="utf-8")
PY

# Remove only source files that actually reference the Amnezia PPA.
while IFS= read -r -d '' source_file; do
  if grep -Eqi '(^|/)amnezia/ppa|ppa\.launchpad(content)?\.net/amnezia/ppa' "$source_file"; then
    rm -f "$source_file"
  fi
done < <(find /etc/apt/sources.list.d -maxdepth 1 -type f \
  \( -name '*.list' -o -name '*.sources' \) -print0 2>/dev/null)

find /etc/apt/trusted.gpg.d /etc/apt/keyrings -maxdepth 1 -type f \
  -iname '*amnezia*' -delete 2>/dev/null || true
rm -f /var/lib/apt/lists/*amnezia* 2>/dev/null || true

step_ok

stage 3 4 "Удаление пакетов и остаточных файлов"
step_begin "Завершение операций dpkg и удаление пакетов"
wait_for_apt
run_logged_allow_fail "Завершение незаконченных операций dpkg..." dpkg --configure -a

packages_to_purge=()
for package in \
  amneziawg \
  amneziawg-dkms \
  amneziawg-tools \
  nginx \
  nginx-common \
  nginx-core \
  certbot \
  python3-certbot \
  python3-certbot-nginx \
  python3-venv \
  python3-pip \
  nftables \
  rsync \
  dnsmasq \
  dnsmasq-base; do
  if dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null \
    | grep -q '^installed$'; then
    packages_to_purge+=("$package")
  fi
done

if ((${#packages_to_purge[@]})); then
  run_logged "Удаление пакетов панели, Nginx и AmneziaWG..." \
    env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a \
    apt-get -o Dpkg::Use-Pty=0 purge -y "${packages_to_purge[@]}"
fi

run_logged_allow_fail "Удаление больше не нужных зависимостей..." \
  env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a \
  apt-get -o Dpkg::Use-Pty=0 autoremove -y --purge

step_ok
step_begin "Удаление оставшихся DKMS, Nginx и Certbot-файлов"
rm -rf \
  /var/lib/dkms/amneziawg \
  /usr/src/amneziawg-* \
  /etc/nginx \
  /var/lib/nginx \
  /var/log/nginx \
  /var/cache/nginx \
  /etc/letsencrypt \
  /var/lib/letsencrypt \
  /var/log/letsencrypt \
  /var/www/html

rm -f \
  /etc/modules-load.d/amneziawg.conf \
  /etc/modprobe.d/amneziawg.conf

find /lib/modules -type f \
  \( -name 'amneziawg.ko' -o -name 'amneziawg.ko.xz' -o -name 'amneziawg.ko.zst' \) \
  -delete 2>/dev/null || true

depmod -a >>"$LOG_FILE" 2>&1 || true
modprobe -r amneziawg >>"$LOG_FILE" 2>&1 || true

run_logged_allow_fail "Обновление списка пакетов после очистки..." \
  apt-get -o Dpkg::Use-Pty=0 update -qq
step_ok

stage 4 4 "Финальная проверка"
step_begin "Проверка полного удаления"
problems=0

for path in \
  /opt/sg-awg-panel \
  /etc/sg-awg-panel \
  /var/lib/sg-awg-panel \
  /etc/amnezia/amneziawg \
  /etc/systemd/system/sg-awg-panel.service \
  /etc/systemd/system/sg-awg-server.service \
  /etc/systemd/system/sg-awg-backup.timer \
  /etc/systemd/system/sg-awg-recovery.service \
  /etc/systemd/system/sg-awg-traffic.service \
  /etc/systemd/system/sg-awg-traffic-schedule.timer \
  /etc/systemd/system/sg-awg-clients-maintenance.timer; do
  if [[ -e "$path" || -L "$path" ]]; then
    printf 'ОСТАЛОСЬ: %s\n' "$path" | tee -a "$LOG_FILE" >&2
    problems=1
  fi
done

if lsmod | awk '{print $1}' | grep -qx amneziawg; then
  printf 'ОСТАЛОСЬ: загружен модуль amneziawg\n' | tee -a "$LOG_FILE" >&2
  problems=1
fi

if dpkg-query -W -f='${db:Status-Status}\n' \
  amneziawg amneziawg-dkms amneziawg-tools 2>/dev/null \
  | grep -q '^installed$'; then
  printf 'ОСТАЛОСЬ: установлен пакет AmneziaWG\n' | tee -a "$LOG_FILE" >&2
  problems=1
fi

if systemctl list-unit-files --no-legend 2>/dev/null \
  | awk '{print $1}' | grep -Eq '^sg-awg-'; then
  printf 'ОСТАЛОСЬ: зарегистрированы службы sg-awg-*\n' | tee -a "$LOG_FILE" >&2
  problems=1
fi

if ss -H -lntp 2>/dev/null | grep -Eq ':18080\b'; then
  printf 'ОСТАЛОСЬ: процесс слушает backend TCP 18080\n' | tee -a "$LOG_FILE" >&2
  ss -H -lntp 2>/dev/null | grep -E ':18080\b' \
    | tee -a "$LOG_FILE" >&2 || true
  problems=1
fi

if grep -RqiE 'amnezia/ppa|ppa\.launchpad(content)?\.net/amnezia/ppa' \
  /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
  printf 'ОСТАЛОСЬ: источник пакетов Amnezia PPA\n' | tee -a "$LOG_FILE" >&2
  problems=1
fi

if (( problems != 0 )); then
  fail "очистка завершилась не полностью"
fi
step_ok

cat <<EOF_DONE

[SG-AWG-Panel] [OK] Полное удаление завершено.
[SG-AWG-Panel] [OK] SG-AWG-Panel, AmneziaWG, Nginx и Certbot удалены.
[SG-AWG-Panel] [OK] Backend TCP 18080 свободен.
[SG-AWG-Panel] Журнал сохранён: $LOG_FILE

Перезагрузите EC2 перед новой установкой:
  sudo reboot
EOF_DONE
