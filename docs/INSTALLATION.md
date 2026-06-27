# Чистая установка

## 1. Требования

- отдельный тестовый VPS или EC2;
- Ubuntu 22.04 или 24.04;
- публичный IPv4;
- рекомендуется не менее 1 ГБ RAM для первой сборки DKMS;
- TCP 22 и 8080 только со своего IP;
- UDP 585 для клиентов.

SG-AWG-Panel не устанавливает Xray и не изменяет SG-Panel.

## 2. Установка

```bash
sudo -i
curl -fsSL https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.1.0-alpha3/install-from-github.sh -o /root/install-sg-awg-panel.sh
bash /root/install-sg-awg-panel.sh
```

На новом Ubuntu фоновое обновление может временно удерживать `dpkg`. Alpha 3 сама ждёт освобождения блокировки до 15 минут. Lock-файлы вручную удалять не нужно.

Установщик последовательно:

1. проверяет Ubuntu до изменения системы;
2. ждёт окончания фоновых обновлений;
3. устанавливает headers, DKMS и пакет AmneziaWG;
4. загружает kernel module `amneziawg`;
5. включает IPv4 forwarding;
6. создаёт `sg-awg-server.service`;
7. устанавливает web-панель и `sg-awg-panel.service`;
8. создаёт пароль администратора.

## 3. Проверка

```bash
systemctl is-active sg-awg-panel
systemctl is-active sg-awg-server
command -v awg
command -v awg-quick
lsmod | grep amneziawg
```

До первой настройки ожидается:

```text
active
inactive
/usr/bin/awg
/usr/bin/awg-quick
```

`sg-awg-server` станет `active` после кнопки **«Сохранить и запустить»**.

## 4. Доступ

```text
http://PUBLIC_IP:8080
```

TCP 8080 не открывайте для всего интернета. В Alpha 3 HTTPS ещё не настраивается.
