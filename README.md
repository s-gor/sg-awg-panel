# SG-AWG-Panel

Отдельная лёгкая web-панель только для **AmneziaWG 2.0**.

Проект не является модулем SG-Panel и не содержит Xray, VLESS, WARP или подписки. Он предназначен для самостоятельного AWG-сервера на отдельном VPS.

Текущая версия: **v0.1.0-alpha3**.

## Что уже работает

- нативная установка AmneziaWG без Docker;
- интерфейс `awg0`, IPv4 forwarding и NAT;
- параметры AWG 2.0: `Jc`, `Jmin`, `Jmax`, `S1–S4`, `H1–H4`, `I1–I5`;
- автоматическое определение публичного IPv4 и внешнего интерфейса;
- одна кнопка **«Сохранить и запустить»**;
- создание, включение, отключение, удаление и пересоздание ключей клиентов;
- клиентский `.conf` и QR-код;
- handshake и RX/TX;
- автоматическая резервная копия перед каждым изменением;
- отдельная страница диагностики с портом, интерфейсом, памятью и журналами;
- ожидание освобождения `apt/dpkg` при установке на новом Ubuntu;
- отдельные скрипты обновления и удаления.

Рабочая схема:

```text
AmneziaVPN / AmneziaWG client
          |
          | UDP 585 / AWG 2.0
          v
      EC2 / awg0
          |
          | NAT
          v
       Интернет
```

Внешний IP клиента будет принадлежать вашему VPS. Cloudflare WARP здесь не используется.

## Ограничения Alpha 3

- Ubuntu 22.04 или 24.04;
- только IPv4;
- один интерфейс `awg0`;
- полный туннель `0.0.0.0/0`;
- панель пока работает по HTTP на порту `8080`;
- TCP `8080` необходимо разрешать только со своего IP.

## Security Group нового EC2

Откройте:

- TCP `22` — только со своего IP;
- TCP `8080` — только со своего IP;
- UDP `585` — для AWG-клиентов.

## Чистая установка

```bash
sudo -i
curl -fsSL https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.1.0-alpha3/install-from-github.sh -o /root/install-sg-awg-panel.sh
bash /root/install-sg-awg-panel.sh
```

Установщик ждёт завершения фонового обновления Ubuntu, устанавливает пакет и kernel module AmneziaWG, затем web-панель.

После завершения откройте:

```text
http://PUBLIC_IP:8080
```

## Первый запуск

1. Проверьте автоматически найденный публичный IPv4 и внешний интерфейс.
2. Оставьте порт `585`, сеть `10.77.0.0/24` и MTU `1280`.
3. Нажмите **«Сохранить и запустить»**.
4. Создайте клиента.
5. На Windows скачайте `.conf` и импортируйте его в **AmneziaVPN**.
6. На телефоне можно использовать QR-код в клиенте с поддержкой AWG 2.0.

Обычный WireGuard не понимает параметры `Jc`, `S1–S4` и `H1–H4` и для этого профиля не подходит.

## Проверка

```bash
systemctl is-active sg-awg-panel
systemctl is-active sg-awg-server
ip -br addr show awg0
ss -lunp | grep ':585'
sudo awg show awg0
```

Ожидается web-служба `active`, AWG-служба `active`, адрес `10.77.0.1/24` на `awg0` и UDP-порт `585`.

## Обновление

```bash
sudo bash /opt/sg-awg-panel/deploy/update-from-github.sh
```

Для выбора версии:

```bash
sudo SG_AWG_PANEL_VERSION=v0.1.0-alpha3 \
  bash /opt/sg-awg-panel/deploy/update-from-github.sh
```

## Удаление

Только панель, настройки и клиенты:

```bash
sudo bash /opt/sg-awg-panel/deploy/uninstall.sh
```

Также удалить пакет AmneziaWG и PPA:

```bash
sudo bash /opt/sg-awg-panel/deploy/uninstall.sh --purge-amneziawg
```

## Документация

- [Чистая установка](docs/INSTALLATION.md)
- [Настройка сервера и клиентов](docs/USAGE.md)
- [Диагностика](docs/DIAGNOSTICS.md)
- [Обновление и резервные копии](docs/MAINTENANCE.md)
- [Безопасность](docs/SECURITY.md)
- [Удаление](docs/UNINSTALL.md)

Alpha-версия предназначена для отдельного тестового сервера.
