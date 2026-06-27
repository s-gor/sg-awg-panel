# Удаление

Удалить web-панель, службы, базу, клиентов, ключи, резервные копии и `awg0.conf`:

```bash
sudo bash /opt/sg-awg-panel/deploy/uninstall.sh
```

Нужно ввести:

```text
DELETE SG-AWG-PANEL
```

Пакет AmneziaWG и PPA при этом остаются установленными.

Чтобы удалить также пакет AmneziaWG и PPA:

```bash
sudo bash /opt/sg-awg-panel/deploy/uninstall.sh --purge-amneziawg
```
