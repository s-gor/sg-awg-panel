# HTTPS, домен и публичный порт

Backend SG-AWG-Panel всегда слушает только:

```text
127.0.0.1:18080
```

Снаружи панель публикует Nginx. Благодаря этому Python-процесс не доступен напрямую из интернета.

## Настройка через панель

Откройте **Settings → Публичный доступ**.

Поля:

- **Режим** — HTTP или HTTPS;
- **Домен панели** — например `awg.example.com`;
- **Публичный порт** — любой свободный TCP-порт;
- **E-mail Let's Encrypt** — обязателен для HTTPS.

Нажмите **«Применить доступ»**. Панель:

1. устанавливает Nginx, если он отсутствует;
2. сохраняет backend `127.0.0.1:18080`;
3. создаёт конфигурацию Nginx;
4. для HTTPS получает сертификат через webroot;
5. проверяет `nginx -t`;
6. выполняет graceful reload Nginx;
7. перезапускает backend с правильным режимом cookie.

## Требования для HTTPS

До нажатия кнопки:

1. A-запись домена должна указывать на публичный IPv4 сервера;
2. TCP `80` должен быть открыт для HTTP-01 Let's Encrypt;
3. выбранный HTTPS-порт должен быть открыт в cloud firewall.

Для стандартного адреса без номера порта используйте `443`.

## Ручной вариант

```bash
sudo bash /opt/sg-awg-panel/deploy/configure-panel-access.sh \
  --scheme https \
  --domain awg.example.com \
  --email admin@example.com \
  --port 443
```

Для HTTP на другом порту:

```bash
sudo bash /opt/sg-awg-panel/deploy/configure-panel-access.sh \
  --scheme http \
  --port 8088
```

## Проверка

```bash
systemctl is-active nginx
nginx -t
ss -ltnp | grep -E ':80|:443|:18080'
```

Backend `18080` должен отображаться только на `127.0.0.1` или `::1`.
