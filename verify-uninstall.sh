#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only audit for a dedicated Ubuntu EC2 after the complete
# SG-AWG-Panel uninstall and reboot. This script does not delete anything.

[[ $EUID -eq 0 ]] || {
  echo '[SG-AWG-Panel Audit] Запустите через sudo.' >&2
  exit 2
}

if [[ ! -r /etc/os-release ]]; then
  echo '[SG-AWG-Panel Audit] Не удалось определить операционную систему.' >&2
  exit 2
fi
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || {
  echo '[SG-AWG-Panel Audit] Проверка предназначена только для Ubuntu.' >&2
  exit 2
}

FAILURES=0
WARNINGS=0
CHECKS=0

section() {
  printf '\n[SG-AWG-Panel Audit] %s\n' "$1"
}

ok() {
  CHECKS=$((CHECKS + 1))
  printf '[OK]   %s\n' "$1"
}

fail() {
  CHECKS=$((CHECKS + 1))
  FAILURES=$((FAILURES + 1))
  printf '[FAIL] %s\n' "$1" >&2
}

warn() {
  CHECKS=$((CHECKS + 1))
  WARNINGS=$((WARNINGS + 1))
  printf '[WARN] %s\n' "$1"
}

check_absent_path() {
  local path="$1"
  if [[ -e "$path" || -L "$path" ]]; then
    fail "Остался путь: $path"
  else
    ok "Путь удалён: $path"
  fi
}

check_package_absent() {
  local package="$1"
  local state
  state="$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)"
  if [[ "$state" == "installed" ]]; then
    fail "Пакет всё ещё установлен: $package"
  else
    ok "Пакет отсутствует: $package"
  fi
}

section '1/8. Службы, таймеры и процессы'

mapfile -t unit_files < <(
  systemctl list-unit-files --no-legend 'sg-awg-*' 2>/dev/null \
    | awk 'NF {print $1}'
)
if ((${#unit_files[@]})); then
  fail "Остались зарегистрированные unit-файлы: ${unit_files[*]}"
else
  ok 'Unit-файлы sg-awg-* отсутствуют'
fi

mapfile -t loaded_units < <(
  systemctl list-units --all --no-legend 'sg-awg-*' 2>/dev/null \
    | awk 'NF {print $1}'
)
if ((${#loaded_units[@]})); then
  fail "Остались загруженные units: ${loaded_units[*]}"
else
  ok 'Загруженные units sg-awg-* отсутствуют'
fi

mapfile -t panel_processes < <(
  ps -eo pid=,args= \
    | awk '/\/opt\/sg-awg-panel|\/opt\/sg-awg-node|python(3)? -m awgpanel|waitress-serve.*awgpanel/ && $0 !~ /awk/ {print}'
)
if ((${#panel_processes[@]})); then
  fail 'Остались процессы SG-AWG-Panel:'
  printf '       %s\n' "${panel_processes[@]}" >&2
else
  ok 'Процессы SG-AWG-Panel отсутствуют'
fi

section '2/8. Файлы панели, данные и резервные копии'

for path in \
  /opt/sg-awg-panel \
  /etc/sg-awg-panel \
  /var/lib/sg-awg-panel \
  /opt/sg-awg-node \
  /etc/sg-awg-node \
  /var/lib/sg-awg-node \
  /usr/local/lib/sg-awg-panel \
  /etc/systemd/system/sg-awg-node-agent.service \
  /root/sg-awg-panel-backups \
  /etc/amnezia/amneziawg \
  /var/www/sg-awg-panel-acme \
  /var/www/sg-awg-acme \
  /var/www/sg-awg-placeholder \
  /var/www/sg-awg-update \
  /etc/sysctl.d/90-sg-awg-panel.conf \
  /etc/dnsmasq.d/sg-awg-traffic.conf \
  /run/lock/sg-awg-panel-traffic.lock; do
  check_absent_path "$path"
done

section '3/8. Nginx, Certbot и сертификаты'

for path in \
  /etc/nginx \
  /var/lib/nginx \
  /var/cache/nginx \
  /etc/letsencrypt \
  /var/lib/letsencrypt; do
  check_absent_path "$path"
done

if pgrep -x nginx >/dev/null 2>&1; then
  fail 'Процесс Nginx всё ещё запущен'
else
  ok 'Процесс Nginx отсутствует'
fi

for package in nginx nginx-common nginx-core certbot python3-certbot python3-certbot-nginx; do
  check_package_absent "$package"
done

section '4/8. AmneziaWG и сетевые интерфейсы'

if ip link show awg0 >/dev/null 2>&1; then
  fail 'Интерфейс awg0 всё ещё существует'
else
  ok 'Интерфейс awg0 отсутствует'
fi

if ip link show sgcascade >/dev/null 2>&1; then
  fail 'Интерфейс sgcascade всё ещё существует'
else
  ok 'Интерфейс sgcascade отсутствует'
fi

mapfile -t outbound_interfaces < <(
  ip -o link show 2>/dev/null \
    | awk -F': ' '$2 ~ /^sgo[0-9]+(@.*)?$/ {sub(/@.*/, "", $2); print $2}'
)
if ((${#outbound_interfaces[@]})); then
  fail "Остались outbound-интерфейсы: ${outbound_interfaces[*]}"
else
  ok 'Интерфейсы sgo* отсутствуют'
fi

if lsmod | awk '{print $1}' | grep -qx amneziawg; then
  fail 'Модуль ядра amneziawg всё ещё загружен'
else
  ok 'Модуль ядра amneziawg не загружен'
fi

for package in amneziawg amneziawg-dkms amneziawg-tools; do
  check_package_absent "$package"
done

if find /lib/modules -type f \
    \( -name 'amneziawg.ko' -o -name 'amneziawg.ko.xz' -o -name 'amneziawg.ko.zst' \) \
    -print -quit 2>/dev/null | grep -q .; then
  fail 'В /lib/modules остался модуль amneziawg'
else
  ok 'Файлы модуля amneziawg отсутствуют'
fi

section '5/8. Traffic Rules, nftables и policy routing'

if command -v nft >/dev/null 2>&1; then
  nft_ruleset="$(nft list ruleset 2>/dev/null || true)"
  if grep -Eq 'table (inet|ip) sg_awg_traffic(_nat)?' <<<"$nft_ruleset"; then
    fail 'Остались nftables-таблицы SG-AWG-Panel'
  else
    ok 'nftables-таблицы SG-AWG-Panel отсутствуют'
  fi
  if grep -Eq 'table (inet|ip) sg_awg_node_(filter|nat|cascade|cascade_nat)' <<<"$nft_ruleset"; then
    fail 'Остались nftables-таблицы SG-Node'
  else
    ok 'nftables-таблицы SG-Node отсутствуют'
  fi
else
  ok 'Команда nft отсутствует вместе с удалённым пакетом nftables'
fi

priority_left=()
for priority in $(seq 12101 12132); do
  if ip rule show 2>/dev/null | grep -Eq "(^|[[:space:]])${priority}:"; then
    priority_left+=("$priority")
  fi
done
if ((${#priority_left[@]})); then
  fail "Остались policy rules с приоритетами: ${priority_left[*]}"
else
  ok 'Policy rules SG-AWG-Panel отсутствуют'
fi

if ip rule show 2>/dev/null | grep -Eq '(^|[[:space:]])13050:'; then
  fail 'Остался policy rule SG-Node с приоритетом 13050'
else
  ok 'Policy rule SG-Node 13050 отсутствует'
fi

route_tables_left=()
for table in $(seq 21001 21032); do
  if ip route show table "$table" 2>/dev/null | grep -q .; then
    route_tables_left+=("$table")
  fi
done
if ((${#route_tables_left[@]})); then
  fail "Остались таблицы маршрутизации: ${route_tables_left[*]}"
else
  ok 'Таблицы маршрутизации SG-AWG-Panel пусты'
fi

if ip route show table 23000 2>/dev/null | grep -q .; then
  fail 'Таблица маршрутизации SG-Node 23000 не пуста'
else
  ok 'Таблица маршрутизации SG-Node 23000 пуста'
fi

section '6/8. Порты и сетевые параметры'

if ss -H -lntp 2>/dev/null | grep -Eq ':18080\b'; then
  fail 'Backend TCP 18080 всё ещё занят'
  ss -H -lntp 2>/dev/null | grep -E ':18080\b' >&2 || true
else
  ok 'Backend TCP 18080 свободен'
fi

if ss -H -lntup 2>/dev/null | grep -Ei 'sg-awg-panel|awgpanel|amneziawg|waitress-serve'; then
  fail 'Остался сетевой listener SG-AWG-Panel/AmneziaWG'
  ss -H -lntup 2>/dev/null \
    | grep -Ei 'sg-awg-panel|awgpanel|amneziawg|waitress-serve' >&2 || true
else
  ok 'Listeners SG-AWG-Panel/AmneziaWG отсутствуют'
fi

ip_forward="$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo unknown)"
if [[ "$ip_forward" == "0" ]]; then
  ok 'net.ipv4.ip_forward восстановлен в 0'
elif [[ "$ip_forward" == "unknown" ]]; then
  warn 'Не удалось прочитать net.ipv4.ip_forward'
else
  fail "net.ipv4.ip_forward остался равен $ip_forward"
fi

section '7/8. APT, PPA и пакеты установщика'

if grep -RqiE '(^|/)amnezia/ppa|ppa\.launchpad(content)?\.net/amnezia/ppa' \
    /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
  fail 'Остался источник пакетов Amnezia PPA'
else
  ok 'Amnezia PPA отсутствует'
fi

if find /etc/apt/trusted.gpg.d /etc/apt/keyrings -maxdepth 1 -type f \
    -iname '*amnezia*' -print -quit 2>/dev/null | grep -q .; then
  fail 'Остался ключ APT Amnezia'
else
  ok 'Ключи APT Amnezia отсутствуют'
fi

for package in python3-venv python3-pip nftables rsync dnsmasq dnsmasq-base; do
  check_package_absent "$package"
done

# The installer temporarily adds deb-src to official Ubuntu deb822 stanzas.
# A clean removal must restore those stanzas to binary packages only.
if python3 - <<'PY'
from __future__ import annotations

import re
from pathlib import Path

bad: list[str] = []
for path in sorted(Path('/etc/apt/sources.list.d').glob('*.sources')):
    text = path.read_text(encoding='utf-8', errors='replace')
    for stanza in re.split(r'\n\s*\n', text):
        types = re.search(r'(?m)^Types:\s*(.+)$', stanza)
        uris = re.search(r'(?m)^URIs:\s*(.+)$', stanza)
        if not types or not uris:
            continue
        uri_value = uris.group(1).lower()
        if 'ubuntu.com/ubuntu' not in uri_value and 'ubuntu.com/ubuntu-ports' not in uri_value:
            continue
        if 'deb-src' in types.group(1).split():
            bad.append(str(path))
if bad:
    print('\n'.join(sorted(set(bad))))
    raise SystemExit(1)
PY
then
  ok 'Официальные Ubuntu sources восстановлены без добавленного deb-src'
else
  fail 'В официальных Ubuntu sources остался добавленный deb-src'
fi

section '8/8. Итоговое состояние systemd'

system_state="$(systemctl is-system-running 2>/dev/null || true)"
case "$system_state" in
  running)
    ok 'systemd сообщает: running'
    ;;
  degraded)
    failed_units="$(systemctl --failed --no-legend 2>/dev/null || true)"
    if grep -q 'sg-awg-' <<<"$failed_units"; then
      fail 'systemd degraded из-за оставшихся sg-awg-* units'
    else
      warn 'systemd сообщает degraded; оставшихся sg-awg-* units не найдено'
      [[ -n "$failed_units" ]] && printf '%s\n' "$failed_units"
    fi
    ;;
  *)
    warn "systemd сообщает: ${system_state:-unknown}"
    ;;
esac

printf '\n[SG-AWG-Panel Audit] Проверок: %d; ошибок: %d; предупреждений: %d.\n' \
  "$CHECKS" "$FAILURES" "$WARNINGS"

if (( FAILURES > 0 )); then
  echo '[SG-AWG-Panel Audit] Удаление НЕ прошло полную проверку.' >&2
  exit 1
fi

echo '[SG-AWG-Panel Audit] [OK] Следов SG-AWG-Panel, SG-Node Agent и подключений Cluster не найдено.'
exit 0
