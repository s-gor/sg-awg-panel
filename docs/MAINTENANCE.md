# Обновление и резервные копии

## Обновление

```bash
sudo SG_AWG_PANEL_VERSION=v0.1.0-alpha4 \
  bash /opt/sg-awg-panel/deploy/update-from-github.sh
```

Updater Alpha 4:

- не запускает `apt update`;
- не ждёт `unattended-upgrades`;
- не переустанавливает kernel module;
- не перезапускает `sg-awg-server`;
- сохраняет базу, `web.env` и `awg0.conf`;
- обновляет только код, Python-зависимости и web-службу.

## Автоматические копии

Перед изменением сервера, клиента или Routing сохраняются:

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
