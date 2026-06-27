# SG-AWG-Panel v0.1.0 Alpha 8

Alpha 8 исправляет архитектуру публичного доступа к панели.

## Правильная схема Nginx

```text
TCP 80      ACME-проверка Let’s Encrypt и HTTP-заглушка
TCP 443     обычная HTTPS-заглушка
TCP 62443   HTTPS SG-AWG-Panel по умолчанию
TCP 18080   backend только на 127.0.0.1
UDP 585     AmneziaWG
```

Панель больше никогда не размещается на TCP 443.

## Что изменено

- отдельный выбираемый TCP-порт панели;
- рекомендованный порт SG-AWG-Panel — 62443;
- backend всегда слушает только `127.0.0.1:18080`;
- удалено обязательное поле e-mail Let’s Encrypt;
- один сертификат домена используется для заглушки 443 и отдельного порта панели;
- отдельные Nginx-конфигурации:
  - `sg-awg-panel.conf`;
  - `sg-awg-placeholder.conf`;
- чужие Nginx-сайты и default site не удаляются;
- проверяется, не занят ли выбранный порт;
- при ошибке Nginx и `web.env` автоматически восстанавливаются;
- можно отключить управление заглушкой и использовать сертификат другого проекта;
- база Alpha 7 мигрируется без потери клиентов и ключей;
- рабочий AWG-туннель при обновлении не перезапускается.

## Совместное размещение

Если другой проект уже обслуживает тот же домен на 443 и сертификат существует:

- отключите управление заглушкой;
- выберите отдельный свободный порт, например 62443;
- SG-AWG-Panel использует существующий сертификат и не меняет чужую конфигурацию.

## Проверки

- 43 automated tests passed;
- Python syntax passed;
- embedded Python in shell scripts compiled;
- Bash syntax passed;
- editable Python package installation passed;
- Markdown local links and code fences passed;
- ZIP integrity passed;
- executable permissions preserved.

Alpha 8 ещё не проверена на реальном EC2.
