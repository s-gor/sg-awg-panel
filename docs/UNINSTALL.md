# Полное удаление

Полный uninstall предназначен для отдельного сервера, где AmneziaWG, Nginx и Certbot не обслуживают другие проекты.

```bash
curl -fsSL https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.1.0-rc4/uninstall.sh | sudo bash
```

Подтверждение:

```text
DELETE SG-AWG-PANEL COMPLETELY
```

После завершения:

```bash
sudo reboot
```

Read-only аудит:

```bash
curl -fsSL https://raw.githubusercontent.com/s-gor/sg-awg-panel/v0.1.0-rc4/verify-uninstall.sh | sudo bash
```

Аудит не удаляет файлы и не заменяет uninstall.
