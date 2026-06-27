# Чистая установка

## 1. Требования

- отдельный тестовый EC2;
- Ubuntu 22.04 или 24.04;
- не менее 1 ГБ RAM рекомендуется для первой сборки DKMS;
- публичный IPv4;
- Security Group: TCP 22 и 8080 только с вашего IP, UDP 585 для клиентов.

SG-AWG-Panel не устанавливает Xray и не изменяет существующий SG-Panel.

## 2. Установка

```bash
sudo -i
curl -fsSL https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.1.0-alpha2/install-from-github.sh -o /root/install-sg-awg-panel.sh
bash /root/install-sg-awg-panel.sh
```

Установщик последовательно:

1. установит Python и web-панель;
2. создаст службу `sg-awg-panel`;
3. добавит PPA Amnezia;
4. установит пакет `amneziawg`, headers и DKMS-модуль;
5. включит `net.ipv4.ip_forward=1`;
6. создаст службу `sg-awg-server`.

## 3. Проверка

```bash
systemctl is-active sg-awg-panel
command -v awg
command -v awg-quick
lsmod | grep amneziawg
```

Ожидается:

```text
active
/usr/bin/awg
/usr/bin/awg-quick
```

Служба `sg-awg-server` станет active только после сохранения конфигурации в панели и нажатия **Запустить**.

## 4. Доступ к панели

```text
http://PUBLIC_IP:8080
```

Alpha 2 пока не настраивает HTTPS. Не открывайте TCP 8080 для всего интернета.
