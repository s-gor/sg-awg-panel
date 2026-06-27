# Обновление и резервные копии

## Обновление из GitHub

```bash
sudo bash /opt/sg-awg-panel/deploy/update-from-github.sh
```

Скрипт загружает указанный тег, сохраняет существующую базу и `web.env`, обновляет код и перезапускает web-панель.

Выбор версии:

```bash
sudo SG_AWG_PANEL_VERSION=v0.1.0-alpha3 \
  bash /opt/sg-awg-panel/deploy/update-from-github.sh
```

## Автоматические копии конфигурации

Перед изменением сервера или клиента панель сохраняет:

```text
/var/lib/sg-awg-panel/backups/<дата-причина>/panel.db
/var/lib/sg-awg-panel/backups/<дата-причина>/awg0.conf
/var/lib/sg-awg-panel/backups/<дата-причина>/metadata.json
```

По умолчанию хранится 20 последних копий. При ошибке применения панель автоматически возвращает предыдущую базу, конфигурацию и состояние службы.

## Копия перед обновлением кода

Установщик также сохраняет:

```text
/root/sg-awg-panel-backups/<дата>/
```

Это отдельная копия базы и `web.env`, созданная перед обновлением программы.
