# Diagnostics и проверка после reboot

Страница показывает:

- состояние и uptime web-панели и AWG-сервера;
- включён ли автозапуск обеих служб;
- наличие kernel module и интерфейса `awg0`;
- прослушивание UDP-порта;
- существование рабочего `awg0.conf`;
- IPv4 forwarding и активное правило NAT MASQUERADE;
- публичный IPv4 и внешний интерфейс;
- реальную RSS-память процесса панели;
- общий процент памяти Linux и load average;
- журналы `sg-awg-server` и `sg-awg-panel`.

Кнопка **«Скачать отчёт»** создаёт текстовый диагностический файл. Закрытые ключи, PSK и токены Access в нём скрываются.

## Проверка постоянной работы

До перезагрузки убедитесь, что на странице зелёные:

```text
Web-панель включена в systemd
AWG-сервер включён в systemd
Рабочий awg0.conf существует
IPv4 forwarding включён
NAT MASQUERADE установлен
Kernel module загружен
```

Затем:

```bash
sudo reboot
```

После повторного подключения:

```bash
systemctl is-active sg-awg-panel
systemctl is-active sg-awg-server
ip -br addr show awg0
ss -lunp | grep ':585'
```

Ожидается `active`, интерфейс `awg0` с адресом сервера и UDP listener на порту AWG.

## Ручные команды

```bash
systemctl status sg-awg-panel --no-pager -l
systemctl status sg-awg-server --no-pager -l
journalctl -u sg-awg-panel -n 100 --no-pager
journalctl -u sg-awg-server -n 100 --no-pager
ip -br addr show awg0
ss -lunp | grep ':585'
sudo awg show awg0
```

Если handshake есть, но сайты не открываются:

```bash
sysctl net.ipv4.ip_forward
iptables -t nat -S POSTROUTING
ip route show default
```

`net.ipv4.ip_forward` должен быть равен `1`.
