# Диагностика и восстановление после reboot

Страница **Diagnostics** проверяет:

- `sg-awg-panel`;
- `nginx`;
- `sg-awg-server`;
- `sg-awg-recovery`;
- backend `127.0.0.1:18080`;
- UDP-порт AWG;
- `awg0.conf`;
- kernel module;
- IPv4 forwarding;
- NAT MASQUERADE;
- память и uptime;
- журналы служб.

## Recovery service

`sg-awg-recovery.service` запускается после `network-online.target` и проверяет:

- `net.ipv4.ip_forward=1`;
- загрузку модуля `amneziawg`;
- запуск web-панели;
- запуск Nginx;
- запуск AWG, если существует `awg0.conf`.

Проверка автозапуска:

```bash
systemctl is-enabled sg-awg-panel
systemctl is-enabled nginx
systemctl is-enabled sg-awg-server
systemctl is-enabled sg-awg-recovery
```

## Тест перезагрузки

```bash
sudo reboot
```

После повторного входа:

```bash
systemctl is-active sg-awg-panel nginx sg-awg-server sg-awg-recovery
ip -br addr show awg0
sudo awg show awg0
```

Клиент должен подключиться без повторного сохранения конфигурации.

## Диагностический отчёт

Кнопка **«Скачать отчёт»** собирает состояния и журналы. PrivateKey, PresharedKey и токены Access автоматически скрываются.
