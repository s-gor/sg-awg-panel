# Maintenance

## Управление через SSH

После установки доступна одна команда:

```bash
sudo sg-awg-panel
```

Меню содержит 14 действий без дублирующих «полной диагностики», старых ошибок и восстановления доступа. Основные пункты:

```text
 4. Сменить пароль администратора
 6. Проверить клиентов и подключения
 7. Проверить Cluster и SG-Node
 8. Проверить Cascade
 9. Создать резервную копию
10. Проверить резервную копию
11. Восстановить резервную копию
13. Проверить и установить обновление
14. Полностью удалить SG-AWG-Panel
```

Прямые команды:

```bash
sudo sg-awg-panel status
sudo sg-awg-panel password
sudo sg-awg-panel sessions
sudo sg-awg-panel clients
sudo sg-awg-panel cluster
sudo sg-awg-panel cascade
sudo sg-awg-panel backup
sudo sg-awg-panel backups
sudo sg-awg-panel verify-backup
sudo sg-awg-panel restore
sudo sg-awg-panel update
sudo sg-awg-panel uninstall
```

Аварийная команда `sudo sg-awg-panel repair-access` остаётся доступна напрямую, но не показывается в обычном меню.

## Резервные копии

Веб-панель и SSH используют один формат и один каталог. Копия содержит базу, клиентов и ключи, awg0, настройки Panel/Agent, управляемые Nginx/HTTPS-файлы, Traffic Rules и manifest. Она не копирует всю Ubuntu, пакеты, чужие конфигурации и systemd-журналы.

```bash
sudo sg-awg-panel backup
sudo sg-awg-panel backups
sudo sg-awg-panel verify-backup
sudo sg-awg-panel restore
```

После создания и перед восстановлением автоматически проверяются безопасные пути, обязательные файлы, SHA-256, совместимость версии, SQLite `integrity_check` и JSON. При ошибке восстановление блокируется.

## Обновление

### Из веб-панели

Откройте **Maintenance → Updates**, нажмите **«Проверить сейчас»**, затем **«Обновить до v0.7.0-RC6»**. RC5 сравнивает свою версию с GitHub main и должен определить RC6 как доступное обновление.

Один и тот же updater используется на Controller и SG-Node:

```bash
sudo bash 0.7.0-RC6-UPDATE-SG-AWG-PANEL.run
```

Из распакованного ZIP:

```bash
unzip 0.7.0-RC6-AWG-Panel.zip
cd 0.7.0-RC6-AWG-Panel
sudo bash update.sh
```

Из SSH-меню:

```bash
sudo sg-awg-panel update
```

Updater создаёт страховочную копию, сохраняет базу, Clients, ключи, Cluster,
Cascade, настройки и подключение Agent. При ошибке выполняется откат.

После обновления:

1. проверьте версию;
2. проверьте `sg-awg-panel.service`;
3. проверьте `sg-awg-server.service`;
4. проверьте Node Agent на подключённой Node;
5. подключите реального клиента;
6. проверьте Direct и Cascade.
