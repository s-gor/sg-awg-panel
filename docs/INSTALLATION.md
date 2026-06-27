# Чистая установка

## Требования

- отдельный тестовый VPS или EC2;
- Ubuntu 22.04 или 24.04;
- публичный IPv4;
- рекомендуется не менее 1 ГБ RAM для первой сборки DKMS;
- TCP 22 и 8080 только со своего IP;
- UDP 585 для клиентов.

## Установка

```bash
sudo -i
curl -fsSL https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.1.0-alpha4/install-from-github.sh -o /root/install-sg-awg-panel.sh
bash /root/install-sg-awg-panel.sh
```

Установщик ждёт только реальные блокировки `apt/dpkg`. Постоянный процесс `unattended-upgrade-shutdown --wait-for-signal` не считается активным обновлением и больше не вызывает бесконечного ожидания.

Последовательность:

1. проверка Ubuntu;
2. установка headers, DKMS и AmneziaWG;
3. загрузка kernel module;
4. включение IPv4 forwarding;
5. создание `sg-awg-server.service`;
6. установка web-панели;
7. создание пароля администратора.

## Проверка до настройки Server

```bash
systemctl is-active sg-awg-panel
systemctl is-active sg-awg-server
command -v awg
command -v awg-quick
lsmod | grep amneziawg
```

Ожидается:

```text
active
inactive
/usr/bin/awg
/usr/bin/awg-quick
```

AWG-служба станет `active` после сохранения Server.
