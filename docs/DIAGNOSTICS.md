# Диагностика

## Базовая проверка

```bash
systemctl is-active sg-awg-panel.service
systemctl is-active sg-awg-server.service
systemctl is-active sg-awg-traffic.service
systemctl is-active nginx.service
curl -fsS http://127.0.0.1:18080/health
```

## Проверка AWG

```bash
sudo awg show
ip address show awg0
```

## Журналы

```bash
sudo journalctl -u sg-awg-panel.service -n 100 --no-pager
sudo journalctl -u sg-awg-server.service -n 100 --no-pager
sudo journalctl -u sg-awg-traffic.service -n 100 --no-pager
sudo journalctl -u nginx.service -n 100 --no-pager
```

## Если правило блокировки не срабатывает

1. Убедитесь, что правило включено.
2. Проверьте выбранных клиентов.
3. Проверьте домен либо IPv4-сеть.
4. Проверьте протокол и порты.
5. Проверьте порядок правил.
6. Проверьте состояние `sg-awg-traffic.service`.
7. Посмотрите журнал этой службы.

## Если клиент потерял весь доступ

1. Отключите последнее добавленное правило.
2. Проверьте, не задано ли правило для любого назначения.
3. Проверьте, не применено ли правило ко всем клиентам.
4. Снова примените конфигурацию и проверьте клиента.
