# HTTPS и домен панели

HTTPS не нужен для самого туннеля AmneziaWG, но нужен для безопасного постоянного доступа к web-панели и ссылкам Access.

## До запуска

1. Направьте A-запись домена на публичный IPv4 сервера.
2. Откройте TCP 80 и 443.
3. Убедитесь, что домен уже разрешается в правильный IP.

## Установка

```bash
sudo bash /opt/sg-awg-panel/deploy/enable-https.sh \
  --domain awg.example.com \
  --email you@example.com
```

Скрипт:

- устанавливает Nginx, Certbot и модуль Certbot для Nginx;
- переводит панель на локальный `127.0.0.1:8080`;
- включает доверие к proxy headers и Secure cookies;
- создаёт reverse proxy;
- получает сертификат Let’s Encrypt;
- включает перенаправление HTTP → HTTPS.

После успешной установки откройте:

```text
https://awg.example.com
```

Затем удалите публичное правило TCP 8080 из Security Group или firewall. UDP-порт AWG остаётся без изменений.
