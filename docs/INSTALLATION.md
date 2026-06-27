# Чистая установка

## Требования

- отдельный тестовый VPS или EC2;
- Ubuntu 22.04 или 24.04;
- публичный IPv4;
- рекомендуется не менее 1 ГБ RAM для первой сборки DKMS;
- TCP 22 и 8080 только со своего IP;
- UDP 585 для клиентов.

## Установка одним блоком

```bash
bash <<'BASH'
set -Eeuo pipefail
tmp="$(mktemp /tmp/sg-awg-install.XXXXXX.sh)"
trap 'rm -f "$tmp"' EXIT
curl -fsSL \
  https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.1.0-alpha5/install-from-github.sh \
  -o "$tmp"
sudo bash "$tmp"
BASH
```

Установщик ждёт только реальные блокировки `apt/dpkg`. Постоянный процесс `unattended-upgrade-shutdown --wait-for-signal` не считается активным обновлением.

Последовательность:

1. проверка Ubuntu;
2. установка headers, DKMS и AmneziaWG;
3. загрузка kernel module;
4. включение IPv4 forwarding;
5. создание `sg-awg-server.service`;
6. установка web-панели и Python-пакета;
7. создание пароля администратора;
8. включение ежедневного backup timer.

## Проверка до настройки Server

```bash
systemctl is-active sg-awg-panel
systemctl is-active sg-awg-server
systemctl is-active sg-awg-backup.timer
command -v awg
command -v awg-quick
lsmod | grep amneziawg
```

Ожидается:

```text
active
inactive
active
/usr/bin/awg
/usr/bin/awg-quick
```

AWG-служба станет `active` после сохранения Server.
