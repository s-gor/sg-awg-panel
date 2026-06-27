# SG-AWG-Panel

Отдельная лёгкая панель только для **AmneziaWG 2.0**.

Этот репозиторий не является модулем SG-Panel и не содержит Xray, VLESS, WARP, Routing или подписки. Проект специально отделён, чтобы спокойно проверять AmneziaWG на отдельном слабом VPS.

Текущая версия: **v0.1.0-alpha2**.

## Что умеет Alpha 2

- устанавливает официальный пакет AmneziaWG без Docker;
- создаёт отдельный интерфейс `awg0`;
- настраивает IPv4 forwarding и NAT;
- управляет параметрами AWG 2.0: `Jc`, `Jmin`, `Jmax`, `S1–S4`, `H1–H4`, `I1–I5`;
- создаёт отдельные ключи и адрес для каждого клиента;
- выдаёт клиентский `.conf` и QR-код;
- включает, отключает и удаляет клиентов;
- показывает последний handshake, RX/TX, память и load average.

## Ограничения Alpha 2

- Ubuntu 22.04 или 24.04;
- только IPv4;
- один интерфейс `awg0`;
- полный туннель `0.0.0.0/0`;
- web-панель пока работает по HTTP на порту `8080`;
- порт панели необходимо открыть в Security Group только для своего IP.

## Новый EC2

Откройте:

- TCP `22` — только со своего IP;
- TCP `8080` — только со своего IP;
- UDP `585` — для клиентов AmneziaWG.

Чистая установка из GitHub:

```bash
sudo -i
curl -fsSL https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.1.0-alpha2/install-from-github.sh -o /root/install-sg-awg-panel.sh
bash /root/install-sg-awg-panel.sh
```

Установщик попросит пароль администратора панели. После завершения откройте:

```text
http://PUBLIC_IP:8080
```

Дальше:

1. Укажите публичный IPv4 EC2.
2. Проверьте UDP-порт `585` и внешний интерфейс, обычно `ens5`.
3. Нажмите **Сохранить конфигурацию**.
4. Запустите AmneziaWG.
5. Создайте клиента.
6. Скачайте `.conf` или отсканируйте QR-код.

Подробности: [docs/INSTALLATION.md](docs/INSTALLATION.md) и [docs/USAGE.md](docs/USAGE.md).

## Проверка служб

```bash
systemctl is-active sg-awg-panel
systemctl is-active sg-awg-server
sudo awg show awg0
```

## Обновление из клона

```bash
cd /root/sg-awg-panel
sudo bash install-or-upgrade.sh
```

## Удаление

```bash
sudo bash /opt/sg-awg-panel/deploy/uninstall.sh
```

Alpha-версия предназначена только для отдельного тестового сервера.
