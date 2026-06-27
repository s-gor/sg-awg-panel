# SG-AWG-Panel

Лёгкая самостоятельная web-панель для **AmneziaWG 2.0**.

Проект не использует Xray и не является модулем SG-Panel. Он управляет собственным AWG-сервером: интерфейсом `awg0`, клиентами, ключами, Routing, DNS, доступом к конфигам, резервными копиями и диагностикой.

Текущая версия: **v0.1.0-alpha6**.

## Что уже работает

- нативная установка без Docker на Ubuntu 22.04/24.04;
- AmneziaWG kernel module, `awg0`, IPv4 forwarding и NAT;
- одна кнопка **«Сохранить и запустить»**;
- страницы Overview, Server, Clients, Access, Routing, DNS, Diagnostics, Backups, Security и Settings;
- создание, включение, отключение, удаление и пересоздание ключей клиентов;
- `.conf`, QR-код и персональная ссылка на актуальный конфиг;
- полный туннель, только сеть AWG или пользовательский `AllowedIPs` для каждого клиента;
- индивидуальные DNS и MTU клиента с наследованием серверных значений;
- изоляция клиентов друг от друга;
- handshake, RX/TX, uptime и реальная память процесса панели;
- автоматические ежедневные и ручные резервные копии;
- безопасный диагностический отчёт без ключей и токенов;
- защита входа от перебора пароля;
- лёгкое обновление без `apt`, `dpkg` и перезапуска рабочего AWG;
- необязательная установка HTTPS через Nginx и Let’s Encrypt.

Схема работы:

```text
AmneziaVPN / AmneziaWG client
          |
          | UDP 585 / AWG 2.0
          v
      VPS / awg0
          |
          | Linux Routing + NAT
          v
       Интернет
```

Внешний IP клиента будет принадлежать вашему VPS. Cloudflare WARP здесь не используется.

## Требования

- Ubuntu 22.04 или 24.04;
- публичный IPv4;
- TCP `22` — только со своего IP;
- TCP `8080` — только со своего IP, пока не настроен HTTPS;
- UDP `585` — для AWG-клиентов.

## Чистая установка

Под обычным пользователем вставьте одним блоком:

```bash
bash <<'BASH'
set -Eeuo pipefail
tmp="$(mktemp /tmp/sg-awg-install.XXXXXX.sh)"
trap 'rm -f "$tmp"' EXIT
curl -fsSL \
  https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.1.0-alpha6/install-from-github.sh \
  -o "$tmp"
sudo bash "$tmp"
BASH
```

После установки откройте:

```text
http://PUBLIC_IP:8080
```

## Первый запуск

1. Откройте **Server**.
2. Проверьте публичный IPv4 и внешний интерфейс или нажмите **«Определить»**.
3. Оставьте для первого теста UDP `585`, сеть `10.77.0.0/24`, MTU `1280` и проверенный профиль маскировки.
4. Нажмите **«Сохранить и запустить»**.
5. Откройте **Clients** и создайте устройство.
6. Скачайте `.conf` или используйте QR-код.
7. На Windows импортируйте `.conf` в **AmneziaVPN**.

Обычный WireGuard не понимает параметры AWG 2.0 и для этих конфигов не подходит.

## Проверка

```bash
systemctl is-active sg-awg-panel
systemctl is-active sg-awg-server
ip -br addr show awg0
ss -lunp | grep ':585'
sudo awg show awg0
```

На странице **Diagnostics** должны быть зелёными автозапуск, `awg0.conf`, IPv4 forwarding и NAT. После этого можно проверить восстановление после `reboot`.

## Обновление

```bash
sudo env SG_AWG_PANEL_VERSION=v0.1.0-alpha6 \
  bash /opt/sg-awg-panel/deploy/update-from-github.sh
```

Обновление сохраняет базу, `web.env`, `awg0.conf`, клиентов и ключи. Рабочий AWG-туннель не перезапускается.

## Необязательный HTTPS

Сначала направьте домен на публичный IP сервера и откройте TCP 80/443. Затем:

```bash
sudo bash /opt/sg-awg-panel/deploy/enable-https.sh \
  --domain awg.example.com \
  --email you@example.com
```

После успешной установки закройте публичный TCP 8080.

## Документация

- [Карта панели](docs/PANEL.md)
- [Чистая установка](docs/INSTALLATION.md)
- [Server и первый запуск](docs/SERVER.md)
- [Клиенты и конфиги](docs/CLIENTS.md)
- [Персональные ссылки Access](docs/ACCESS.md)
- [Routing и AllowedIPs](docs/ROUTING.md)
- [DNS](docs/DNS.md)
- [Диагностика и reboot](docs/DIAGNOSTICS.md)
- [Резервные копии и обновление](docs/MAINTENANCE.md)
- [Безопасность](docs/SECURITY.md)
- [HTTPS](docs/HTTPS.md)
- [Удаление](docs/UNINSTALL.md)

## Ограничения Alpha 6

- только IPv4;
- один интерфейс `awg0`;
- доменные и гео-правила пока не реализованы;
- Docker Compose будет дополнительным, а не основным способом установки.

Alpha-версия предназначена для отдельного тестового VPS.
