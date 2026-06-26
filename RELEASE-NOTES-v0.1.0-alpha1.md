# SG-AWG-Panel v0.1.0 Alpha 1

Первый выпуск отдельного проекта только для AmneziaWG 2.0.

## Главное

- новый самостоятельный репозиторий `s-gor/sg-awg-panel`;
- не зависит от SG-Panel и не содержит Xray, VLESS, WARP или Routing;
- отдельные пути, база и службы:
  - `/opt/sg-awg-panel`;
  - `/etc/sg-awg-panel`;
  - `/var/lib/sg-awg-panel`;
  - `sg-awg-panel.service`;
  - `sg-awg-server.service`;
- установка официального пакета AmneziaWG без Docker;
- настройка AWG 2.0 и интерфейса `awg0`;
- создание клиентов, `.conf` и QR-кодов;
- включение, отключение и удаление клиентов;
- handshake, RX/TX, память и load average.

## Ограничения

- только отдельный тестовый сервер;
- Ubuntu 22.04/24.04;
- только IPv4;
- web-панель по HTTP на порту 8080;
- TCP 8080 следует разрешить только со своего IP;
- на реальном EC2 версия ещё не проверена.
