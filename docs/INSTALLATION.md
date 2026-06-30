# Установка SG-AWG-Panel

## Требования

- Ubuntu Server 22.04 LTS или 24.04 LTS;
- архитектура amd64;
- отдельный сервер или EC2;
- root или sudo;
- открытый UDP-порт AmneziaWG;
- отдельный TCP-порт панели.

## Установка

```bash
curl -fsSL https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.1.0-rc4/install.sh | sudo bash
```

Укажите пароль администратора и публичный TCP-порт панели.

Рекомендуемый порт:

```text
62443
```

После завершения откройте:

```text
http://PUBLIC_IP:ВЫБРАННЫЙ_ПОРТ
```

Журнал установки:

```text
/var/log/sg-awg-panel-install.log
```

Установщик рассчитан на отдельный сервер. Не используйте полный uninstall на машине, где Nginx, Certbot или AmneziaWG обслуживают другие проекты.
