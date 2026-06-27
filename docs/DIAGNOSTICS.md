# Diagnostics

Страница показывает:

- состояние web-панели и AWG-сервера;
- наличие kernel module и интерфейса `awg0`;
- прослушивание UDP-порта;
- публичный IPv4 и внешний интерфейс;
- реальную RSS-память процесса панели;
- общий процент памяти Linux и load average;
- журналы `sg-awg-server` и `sg-awg-panel`.

Команды ручной проверки:

```bash
systemctl status sg-awg-panel --no-pager -l
systemctl status sg-awg-server --no-pager -l
journalctl -u sg-awg-panel -n 100 --no-pager
journalctl -u sg-awg-server -n 100 --no-pager
ip -br addr show awg0
ss -lunp | grep ':585'
awg show awg0
```

Если handshake есть, но сайты не открываются:

```bash
sysctl net.ipv4.ip_forward
iptables -t nat -S POSTROUTING
ip route show default
```

`net.ipv4.ip_forward` должен быть равен `1`.
