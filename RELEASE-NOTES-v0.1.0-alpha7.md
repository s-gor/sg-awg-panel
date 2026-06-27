# SG-AWG-Panel v0.1.0 Alpha 7

Alpha 7 посвящена безопасной эксплуатации панели. Сетевая логика AmneziaWG и рабочий туннель не менялись.

## Публичный доступ

- Nginx стал единственной публичной точкой входа.
- Python backend всегда слушает только `127.0.0.1:18080`.
- В Settings можно выбрать HTTP или HTTPS, домен и публичный TCP-порт.
- Для HTTPS сертификат Let’s Encrypt выпускается через webroot.
- При первом обновлении с Alpha 6 Nginx будет установлен автоматически, если его ещё нет.

## Безопасность

- Смена пароля завершает все активные сессии.
- Добавлены серверные активные сессии и принудительное завершение.
- Добавлен IP allowlist административной части панели.
- Панель не позволяет сохранить allowlist без текущего IP.
- Добавлен журнал успешных, ошибочных и заблокированных входов.
- Сохраняется защита от перебора: пять ошибок за пятнадцать минут.

## Резервные копии

- Расписание: каждый час, каждые 6 часов, ежедневно, еженедельно или выключено.
- Настраиваемый срок хранения: от 1 до 365 копий.
- systemd timer использует `Persistent=true`.

## Обновления

- Проверка новых тегов GitHub из Settings.
- Обновление запускается отдельной systemd-задачей.
- Перед обновлением сохраняются проект, SQLite, `web.env`, Nginx и systemd units.
- После обновления проверяются backend, Nginx и службы.
- При ошибке предыдущая рабочая версия восстанавливается автоматически.
- Рабочий AWG-туннель при обычном обновлении не перезапускается.

## Восстановление после reboot

- Добавлена `sg-awg-recovery.service`.
- После загрузки проверяются IPv4 forwarding, kernel module, панель, Nginx и AWG.
- Diagnostics показывает loopback backend, Nginx и готовность recovery service.

## Проверки

- 37 automated tests passed;
- Python syntax passed;
- embedded Python in shell installers compiled;
- Bash syntax passed;
- editable Python package installation passed;
- Markdown links and code fences passed;
- ZIP integrity passed;
- executable permissions preserved.

Alpha 7 ещё не проверена на реальном EC2.
