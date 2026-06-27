# Диагностика

Страница **Диагностика** показывает:

- состояние web-панели и AWG-сервера;
- наличие kernel module;
- существование интерфейса `awg0`;
- прослушивание UDP-порта;
- публичный IPv4 и внешний интерфейс;
- реальную RSS-память процесса web-панели;
- общий процент памяти Linux и load average;
- последние автоматические резервные копии;
- журналы `sg-awg-server` и `sg-awg-panel`.

## Основные команды

```bash
systemctl status sg-awg-panel --no-pager -l
systemctl status sg-awg-server --no-pager -l
journalctl -u sg-awg-panel -n 100 --no-pager
journalctl -u sg-awg-server -n 100 --no-pager
ip -br addr show awg0
ss -lunp | grep ':585'
awg show awg0
```

## Сервер active, но клиент не подключается

Проверьте:

1. UDP 585 открыт в Security Group;
2. endpoint в клиентском `.conf` содержит реальный публичный IP;
3. используется клиент с поддержкой AmneziaWG 2.0;
4. параметры `Jc`, `S1–S4`, `H1–H4` не удалены из файла;
5. клиент не был отключён или пересоздан в панели.

## Сайты не открываются после handshake

Проверьте forwarding и NAT:

```bash
sysctl net.ipv4.ip_forward
iptables -t nat -S POSTROUTING
ip route show default
```

`net.ipv4.ip_forward` должен быть равен `1`.
