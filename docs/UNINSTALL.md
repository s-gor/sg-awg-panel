# Удаление

Удалить панель, службы, базу, клиентов, ключи, резервные копии и `awg0.conf`:

```bash
sudo bash /opt/sg-awg-panel/deploy/uninstall.sh
```

Нужно ввести:

```text
DELETE SG-AWG-PANEL
```

Чтобы удалить также пакет AmneziaWG и PPA:

```bash
sudo bash /opt/sg-awg-panel/deploy/uninstall.sh --purge-amneziawg
```
