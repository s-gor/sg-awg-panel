# Обновление и резервные копии

## Обновление

```bash
sudo env SG_AWG_PANEL_VERSION=v0.1.0-alpha5 \
  bash /opt/sg-awg-panel/deploy/update-from-github.sh
```

Updater Alpha 5:

- не запускает `apt update`;
- не ждёт `unattended-upgrades`;
- не переустанавливает kernel module;
- не перезапускает `sg-awg-server`;
- сохраняет базу, `web.env` и `awg0.conf`;
- обновляет только код, Python-пакет и web-службу.

## Автоматические копии

Установщик включает `sg-awg-backup.timer`:

```bash
systemctl status sg-awg-backup.timer --no-pager
systemctl list-timers sg-awg-backup.timer
```

Копия создаётся раз в сутки. `Persistent=true` означает, что пропущенный запуск выполняется после включения сервера. Время запуска немного случайно сдвигается, чтобы не создавать нагрузку ровно в полночь.

Хранятся последние 20 копий:

```text
/var/lib/sg-awg-panel/backups/<дата-причина>/panel.db
/var/lib/sg-awg-panel/backups/<дата-причина>/awg0.conf
/var/lib/sg-awg-panel/backups/<дата-причина>/metadata.json
```

## Ручная копия и восстановление

Откройте **Резервные копии**:

- **Создать сейчас** — новая копия состояния;
- **Восстановить** — возврат базы и `awg0.conf`.

Перед восстановлением панель создаёт отдельную страховочную копию текущего состояния.
