# Maintenance

## Управление через SSH

После установки доступна одна команда:

```bash
sudo sg-awg-panel
```

Она открывает нумерованное меню для состояния служб, диагностики, пароля,
сессий, Clients, Cluster, Cascade, backup/restore, обновления и uninstall.

Ключевые пункты:

```text
 6. Сменить пароль администратора
 9. Проверить клиентов и подключения
10. Проверить Cluster и SG-Node
11. Проверить Cascade
16. Проверить и установить обновление
17. Полностью удалить SG-AWG-Panel
```

Команды без меню:

```bash
sudo sg-awg-panel status
sudo sg-awg-panel password
sudo sg-awg-panel sessions
sudo sg-awg-panel repair-access
sudo sg-awg-panel clients
sudo sg-awg-panel cluster
sudo sg-awg-panel cascade
sudo sg-awg-panel errors
sudo sg-awg-panel diagnostics
sudo sg-awg-panel backup
sudo sg-awg-panel backups
sudo sg-awg-panel restore
sudo sg-awg-panel update
sudo sg-awg-panel uninstall
```

`password`, `sessions` и `repair-access` завершают старые браузерные сессии.
Пароль вводится скрыто и не попадает в историю shell.

## Резервные копии

Перед восстановлением:

1. убедитесь, что выбран нужный архив;
2. сохраните текущие клиентские конфигурации;
3. после восстановления проверьте панель, AWG Server и реального клиента.

Создать копию:

```bash
sudo sg-awg-panel backup
```

Показать копии:

```bash
sudo sg-awg-panel backups
```

Восстановить:

```bash
sudo sg-awg-panel restore
```

## Обновление

Один и тот же updater используется на Controller и SG-Node:

```bash
sudo bash 0.7.0-RC4-UPDATE-SG-AWG-PANEL.run
```

Из распакованного ZIP:

```bash
unzip 0.7.0-RC4-AWG-Panel.zip
cd 0.7.0-RC4-AWG-Panel
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
