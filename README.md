# SG-AWG-Panel

Лёгкая самостоятельная web-панель для **AmneziaWG 2.0**.

Проект не использует Xray и не является модулем SG-Panel. Он управляет собственным AWG-сервером: интерфейсом `awg0`, клиентами, ключами, Routing, DNS, доступом к конфигам, резервными копиями и диагностикой.

Текущая версия: **v0.1.0-alpha4**.

## Что уже работает

- нативная установка без Docker на Ubuntu 22.04/24.04;
- AmneziaWG kernel module, `awg0`, IPv4 forwarding и NAT;
- одна кнопка **«Сохранить и запустить»**;
- отдельные страницы Overview, Server, Clients, Access, Routing, DNS, Diagnostics, Backups, Security и Settings;
- создание, включение, отключение, удаление и пересоздание ключей клиентов;
- `.conf`, QR-код и персональная ссылка на актуальный конфиг;
- полный или выборочный `AllowedIPs` для каждого клиента;
- изоляция клиентов друг от друга;
- handshake, RX/TX и реальная память процесса панели;
- автоматические и ручные резервные копии;
- восстановление из панели;
- смена пароля администратора;
- лёгкое обновление без `apt`, `dpkg` и перезапуска AWG.

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
- TCP `8080` — только со своего IP;
- UDP `585` — для AWG-клиентов.

## Чистая установка

```bash
sudo -i
curl -fsSL https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.1.0-alpha4/install-from-github.sh -o /root/install-sg-awg-panel.sh
bash /root/install-sg-awg-panel.sh
```

После установки откройте:

```text
http://PUBLIC_IP:8080
```

## Первый запуск

1. Откройте **Server**.
2. Проверьте публичный IPv4 и внешний интерфейс.
3. Оставьте для первого теста UDP `585`, сеть `10.77.0.0/24`, MTU `1280`.
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

## Обновление

При обновлении установленной панели системные пакеты не трогаются:

```bash
sudo SG_AWG_PANEL_VERSION=v0.1.0-alpha4 \
  bash /opt/sg-awg-panel/deploy/update-from-github.sh
```

## Документация

- [Карта панели](docs/PANEL.md)
- [Чистая установка](docs/INSTALLATION.md)
- [Server и первый запуск](docs/SERVER.md)
- [Клиенты и конфиги](docs/CLIENTS.md)
- [Персональные ссылки Access](docs/ACCESS.md)
- [Routing и AllowedIPs](docs/ROUTING.md)
- [DNS](docs/DNS.md)
- [Диагностика](docs/DIAGNOSTICS.md)
- [Резервные копии и обновление](docs/MAINTENANCE.md)
- [Безопасность](docs/SECURITY.md)
- [Удаление](docs/UNINSTALL.md)

## Ограничения Alpha 4

- только IPv4;
- один интерфейс `awg0`;
- панель пока работает по HTTP на порту `8080`;
- доменные и гео-правила пока не реализованы;
- Docker Compose будет дополнительным, а не основным способом установки.

Alpha-версия предназначена для отдельного тестового VPS.
