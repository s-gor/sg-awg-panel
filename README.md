# SG-AWG-Panel

**Веб-панель для установки и управления AmneziaWG 2.0 на одном или нескольких Ubuntu-серверах.**

![Version](https://img.shields.io/badge/version-v0.7.0--RC3-3b82f6)
![Ubuntu](https://img.shields.io/badge/Ubuntu-22.04%20%7C%2024.04-E95420?logo=ubuntu&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![AmneziaWG](https://img.shields.io/badge/AmneziaWG-2.0-2563EB)

SG-AWG-Panel разворачивает нативный AmneziaWG 2.0, создаёт клиентов, управляет правилами трафика, дополнительными SG-Node и межсерверным Cascade — без Xray, VLESS, Reality и сторонней VPN-панели.

```text
Обычный маршрут: Client → AWG Server → Internet
Cascade:        Client → Entry SG-AWG → Exit SG-AWG → Internet
Cluster:        Controller → SG-Node Agent → AmneziaWG runtime
```

> Текущая версия: `v0.7.0-RC3` — Release Candidate.

## Возможности

### AWG Server

- установка AmneziaWG и системных компонентов;
- Endpoint, UDP-порт, внутренняя сеть, DNS, MTU и параметры маскировки;
- запуск, остановка, перезапуск и диагностика сервера;
- просмотр фактических системных файлов в режиме read-only.

### Clients

- отдельные ключи и VPN-адрес для каждого устройства;
- QR-код, `.conf`, копирование и защищённая ссылка;
- включение, отключение, срок действия и удаление;
- выбор Controller или SG-Node при создании клиента;
- единый список клиентов всех серверов;
- отображение сервера подключения, маршрута, активности и трафика.

### Cluster и SG-Node

- один Controller и несколько SG-Node;
- схема «установить Node → добавить имя → выполнить одну команду»;
- одноразовый enrollment token;
- heartbeat, адрес, версия Agent, службы и ресурсы;
- безопасные удалённые задания без произвольного shell;
- синхронизация managed peers в реальном `awg0`;
- защита от повторной выдачи уже занятого VPN-адреса;
- сохранение локальных peers SG-Node при синхронизации Controller.

### Cascade

- маршрут `Client → Entry → Exit → Internet`;
- сценарий Controller + SG-Node;
- сценарий двух самостоятельных SG-AWG-Panel;
- служебный доступ Exit и одноразовая ссылка;
- назначение Cascade выбранным клиентам;
- проверка серверного выхода и активного маршрута клиента;
- переключение Direct ↔ Cascade без ручного редактирования сети;
- безопасное отключение и полный двухсторонний сброс.

### Traffic Rules

- блокировка доменов, IPv4 и CIDR;
- TCP/UDP, порты и диапазоны;
- правила для всех или выбранных клиентов;
- порядок, включение, отключение и применение правил.

### Security

- пароль администратора;
- IP allowlist;
- активные сессии и принудительное завершение;
- журнал успешных, ошибочных и заблокированных входов;
- CSRF-защита;
- HTTP или HTTPS через Nginx и Let’s Encrypt.

### Maintenance

- диагностика и журналы;
- резервное копирование и восстановление;
- безопасное обновление с backup и rollback;
- полный uninstall;
- отдельная read-only проверка очистки.

### Help

В панель встроена пошаговая справка с полнотекстовым поиском: Clients, AWG Server, Traffic Rules, Cluster, SG-Node, Cascade, Security, Backup, Update, диагностика и удаление.

## Требования

- Ubuntu Server 22.04 LTS или 24.04 LTS;
- архитектура amd64;
- отдельный сервер или EC2;
- root или sudo;
- TCP 22 для SSH;
- свободный TCP-порт панели;
- UDP 585 для AmneziaWG по умолчанию;
- для HTTPS: TCP 80, TCP 443 и выбранный порт панели.

> Установщик и полный uninstall рассчитаны на выделенный сервер, где AmneziaWG, Nginx и Certbot не обслуживают другие проекты.

## Быстрая установка Controller

Одна команда, без `unzip`:

```bash
curl -fL -o /tmp/sg-awg-panel.run https://github.com/s-gor/sg-awg-panel/releases/download/v0.7.0-RC3/0.7.0-RC3-INSTALL-SG-AWG-PANEL.run && sudo bash /tmp/sg-awg-panel.run
```

Установщик запрашивает имя сервера, пароль администратора и публичный TCP-порт панели. Технический вывод сохраняется в журнале, а на экране показываются этапы, зелёная вертушка и понятный итог.

Журнал:

```text
/var/log/sg-awg-panel-install.log
```

После установки откройте:

```text
http://PUBLIC_IP:ПОРТ_ПАНЕЛИ
```

## Подготовка SG-Node

```bash
curl -fL -o /tmp/sg-awg-node.run https://github.com/s-gor/sg-awg-panel/releases/download/v0.7.0-RC3/0.7.0-RC3-INSTALL-SG-AWG-NODE.run && sudo bash /tmp/sg-awg-node.run
```

Затем:

1. откройте **Cluster** на Controller;
2. укажите имя SG-Node;
3. нажмите **Добавить и показать команду**;
4. выполните показанную команду на сервере Node;
5. дождитесь статуса **В сети**.

## Установка из ZIP

```bash
unzip 0.7.0-RC3-AWG-Panel.zip
cd 0.7.0-RC3-AWG-Panel
sudo bash install.sh
```

## Обновление

Распакуйте новую версию в отдельную папку и запустите:

```bash
sudo bash update.sh
```

Обновлятор создаёт резервную копию, сохраняет клиентов, ключи, Cluster, Cascade и настройки, проверяет новую версию и выполняет автоматический откат при ошибке.

## Включение HTTPS

1. направьте DNS-запись `A` на публичный IP сервера;
2. разрешите TCP 80, TCP 443 и порт панели;
3. откройте **Security → Panel Access**;
4. выберите **HTTPS + Let’s Encrypt**;
5. укажите домен и примените настройки.

## Основные разделы

| Раздел | Назначение |
|---|---|
| **System** | ресурсы, службы, журналы и диагностика |
| **Clients** | клиенты Controller и SG-Node |
| **AWG Server** | конфигурация AmneziaWG |
| **Network** | Traffic Rules, DNS и AWG Outbounds |
| **Cascade** | сервер подключения, сервер выхода и маршруты клиентов |
| **Cluster** | Controller, SG-Node, Agent, метрики и задания |
| **Security** | доступ, HTTPS, пароль, allowlist и сессии |
| **Maintenance** | backup, restore и update |
| **Help** | встроенная пошаговая справка |

## Быстрая проверка

```bash
systemctl is-active sg-awg-panel.service
systemctl is-active sg-awg-server.service
systemctl is-active sg-awg-traffic.service
systemctl is-active nginx.service
curl -fsS http://127.0.0.1:18080/health
```

## Полное удаление

```bash
curl -fsSL https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.7.0-RC3/uninstall.sh | sudo bash
```

Подтверждение:

```text
DELETE SG-AWG-PANEL COMPLETELY
```

После удаления:

```bash
sudo reboot
```

Проверка очистки:

```bash
curl -fsSL https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.7.0-RC3/verify-uninstall.sh | sudo bash
```

## Документация

- [Руководство пользователя](docs/USER-GUIDE.md)
- [Установка](docs/INSTALLATION.md)
- [Clients](docs/CLIENTS.md)
- [AWG Server](docs/SERVER.md)
- [Traffic Rules](docs/TRAFFIC-RULES.md)
- [Cascade](docs/CASCADE.md)
- [Cluster и SG-Node](docs/MULTI-NODE.md)
- [Help](docs/HELP.md)
- [Security](docs/SECURITY.md)
- [Maintenance](docs/MAINTENANCE.md)
- [Диагностика](docs/DIAGNOSTICS.md)
- [Удаление](docs/UNINSTALL.md)

## Что нового в 0.7.0-RC3

Публичная `v0.1.0-rc4` была односерверной панелью с Clients, Traffic Rules, Security и Maintenance. В `0.7.0-RC3` добавлены Cluster и SG-Node, единое управление клиентами нескольких серверов, два режима Cascade, встроенная Help, самодостаточные `.run`-установщики, полноценный updater и усиленный uninstall.

Главное исправление RC3 — Controller учитывает фактические VPN-адреса peers на SG-Node и больше не выдаёт занятую `/32` другому клиенту.

Полный список изменений: [CHANGELOG.md](CHANGELOG.md).

## Ответственность

Используйте проект в соответствии с законодательством вашей страны и правилами провайдера. Перед установкой на сервер с другими сервисами изучите действия installer, updater и uninstall.
