#!/usr/bin/env bash
set -Eeuo pipefail

PURGE_AWG=0
[[ "${1:-}" == "--purge-amneziawg" ]] && PURGE_AWG=1
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }


stage() {
  printf '\n[SG-AWG-Panel] Этап %s/%s: %s\n' "$1" "$2" "$3"
}
step_begin() {
  STEP_LABEL="$1"
  STEP_STARTED=$SECONDS
  printf '[SG-AWG-Panel] [..] %s...\n' "$STEP_LABEL"
}
step_ok() {
  printf '[SG-AWG-Panel] [OK] %s... (%s сек)\n' "$STEP_LABEL" "$((SECONDS - STEP_STARTED))"
}

printf 'This removes SG-AWG-Panel, SG-Node connection state, all clients, keys, backups and AWG configuration.\n'
(( PURGE_AWG == 1 )) && printf 'The AmneziaWG package and PPA will also be removed.\n'
read -r -p "Type DELETE SG-AWG-PANEL: " answer
[[ "$answer" == "DELETE SG-AWG-PANEL" ]] || { echo "Cancelled"; exit 1; }

stage 1 4 "Остановка компонентов"
step_begin "Остановка служб, таймеров и AWG-интерфейсов"
if command -v awg-quick >/dev/null 2>&1; then
  [[ -f /etc/amnezia/amneziawg/sgcascade.conf ]] && awg-quick down /etc/amnezia/amneziawg/sgcascade.conf >/dev/null 2>&1 || true
  [[ -f /etc/amnezia/amneziawg/awg0.conf ]] && awg-quick down /etc/amnezia/amneziawg/awg0.conf >/dev/null 2>&1 || true
fi
systemctl disable --now sg-awg-node-agent.service 2>/dev/null || true
systemctl disable --now sg-awg-traffic-schedule.timer sg-awg-clients-maintenance.timer 2>/dev/null || true
systemctl disable --now sg-awg-traffic-schedule.service sg-awg-clients-maintenance.service 2>/dev/null || true
systemctl disable --now sg-awg-traffic.service 2>/dev/null || true
systemctl disable --now sg-awg-panel.service 2>/dev/null || true
systemctl disable --now sg-awg-backup.timer 2>/dev/null || true
systemctl disable --now sg-awg-recovery.service 2>/dev/null || true
systemctl disable --now sg-awg-server.service 2>/dev/null || true

# Remove stale managed interfaces even when a failed rollback already deleted
# their configuration files before awg-quick down could run.
if command -v ip >/dev/null 2>&1; then
  for interface in sgcascade awg0 $(ip -o link show 2>/dev/null | awk -F': ' '$2 ~ /^sgo[0-9]+(@.*)?$/ {sub(/@.*/, "", $2); print $2}'); do
    [[ -n "$interface" ]] || continue
    ip link show "$interface" >/dev/null 2>&1 || continue
    ip link delete "$interface" >/dev/null 2>&1 || true
  done
fi
step_ok

stage 2 4 "Удаление системной интеграции"
step_begin "Удаление systemd-служб и конфигурации Nginx"
rm -f /etc/systemd/system/sg-awg-node-agent.service \
  /etc/systemd/system/sg-awg-panel.service /etc/systemd/system/sg-awg-server.service \
  /etc/systemd/system/sg-awg-backup.service /etc/systemd/system/sg-awg-backup.timer \
  /etc/systemd/system/sg-awg-recovery.service /etc/systemd/system/sg-awg-traffic.service \
  /etc/systemd/system/sg-awg-traffic-schedule.service /etc/systemd/system/sg-awg-traffic-schedule.timer \
  /etc/systemd/system/sg-awg-clients-maintenance.service /etc/systemd/system/sg-awg-clients-maintenance.timer
rm -f /etc/sysctl.d/90-sg-awg-panel.conf /etc/dnsmasq.d/sg-awg-traffic.conf
systemctl restart dnsmasq.service >/dev/null 2>&1 || true
rm -f \
  /etc/nginx/sites-enabled/sg-awg-panel \
  /etc/nginx/sites-available/sg-awg-panel \
  /etc/nginx/sites-enabled/sg-awg-panel.conf \
  /etc/nginx/sites-available/sg-awg-panel.conf \
  /etc/nginx/sites-enabled/sg-awg-placeholder.conf \
  /etc/nginx/sites-available/sg-awg-placeholder.conf
rm -rf /var/www/sg-awg-panel-acme /var/www/sg-awg-acme /var/www/sg-awg-placeholder \
  /var/www/sg-awg-update
nginx -t >/dev/null 2>&1 && systemctl reload nginx 2>/dev/null || true
step_ok

stage 3 4 "Удаление данных и сетевых правил"
step_begin "Удаление Traffic Rules, файлов панели и данных"
nft delete table inet sg_awg_traffic >/dev/null 2>&1 || true
nft delete table ip sg_awg_traffic_nat >/dev/null 2>&1 || true
nft delete table inet sg_awg_node_filter >/dev/null 2>&1 || true
nft delete table ip sg_awg_node_nat >/dev/null 2>&1 || true
nft delete table inet sg_awg_node_cascade >/dev/null 2>&1 || true
nft delete table ip sg_awg_node_cascade_nat >/dev/null 2>&1 || true
while ip rule del priority 13050 >/dev/null 2>&1; do :; done
ip route flush table 23000 >/dev/null 2>&1 || true
for id in $(seq 1 32); do
  while ip rule del priority "$((12100 + id))" >/dev/null 2>&1; do :; done
  ip route flush table "$((21000 + id))" >/dev/null 2>&1 || true
  ip link delete "sgo${id}" >/dev/null 2>&1 || true
done
rm -rf /opt/sg-awg-panel /etc/sg-awg-panel /var/lib/sg-awg-panel \
  /opt/sg-awg-node /etc/sg-awg-node /var/lib/sg-awg-node /etc/amnezia/amneziawg
rm -f /tmp/sg-awg-node.* /tmp/sg-awg-node-enroll.*
rm -f /run/lock/sg-awg-panel-traffic.lock
step_ok

stage 4 4 "Финальная проверка"
step_begin "Обновление systemd и проверка удаления"
find /etc/systemd/system -type l -name 'sg-awg-*' -delete 2>/dev/null || true
rm -rf \
  /etc/systemd/system/sg-awg-*.service.d \
  /etc/systemd/system/sg-awg-*.timer.d

systemctl daemon-reload
systemctl reset-failed >/dev/null 2>&1 || true
sysctl --system >/dev/null 2>&1 || true

step_ok

if (( PURGE_AWG == 1 )); then
  DEBIAN_FRONTEND=noninteractive apt-get purge -y amneziawg || true
  DEBIAN_FRONTEND=noninteractive apt-get autoremove -y --purge || true
  rm -f /etc/apt/sources.list.d/*amnezia*.list /etc/apt/sources.list.d/*amnezia*.sources
  apt-get update || true
  echo "[SG-AWG-Panel] [OK] SG-AWG-Panel и пакеты AmneziaWG удалены."
else
  echo "[SG-AWG-Panel] [OK] Данные, службы и следы подключения SG-Node удалены. Пакет AmneziaWG оставлен установленным."
fi

rm -f /usr/local/sbin/sg-awg-panel
