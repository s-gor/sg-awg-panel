# Чистая установка

## Поддерживаемые системы

- Ubuntu 22.04 LTS;
- Ubuntu 24.04 LTS;
- x86_64.

## Порты

Первоначально:

- TCP 22 — только ваш IP;
- TCP 8080 — только ваш IP;
- UDP 585 — клиенты AWG.

Для HTTPS позже откройте TCP 80 и выбранный HTTPS-порт.

## Установка

```bash
bash <<'BASH'
set -Eeuo pipefail
tmp="$(mktemp /tmp/sg-awg-install.XXXXXX.sh)"
trap 'rm -f "$tmp"' EXIT
curl -fsSL \
  https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.1.0-alpha7/install-from-github.sh \
  -o "$tmp"
sudo bash "$tmp"
BASH
```

Установщик:

1. проверяет Ubuntu до изменения системы;
2. ждёт только реальные locks `apt/dpkg`;
3. устанавливает AmneziaWG и kernel module;
4. создаёт Python virtualenv и SQLite;
5. привязывает backend к `127.0.0.1:18080`;
6. устанавливает Nginx на публичный TCP 8080;
7. включает backup timer и recovery service;
8. не запускает AWG до создания `awg0.conf`.

## Проверка

```bash
systemctl is-active sg-awg-panel
systemctl is-active nginx
systemctl is-active sg-awg-recovery
systemctl is-active sg-awg-server
ss -ltnp | grep -E ':8080|:18080'
```

Ожидается:

- панель, Nginx и recovery — `active`;
- AWG до настройки может быть `inactive`;
- `18080` слушается только на loopback.
