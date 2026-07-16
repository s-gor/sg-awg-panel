from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_beta9_uses_valid_iptables_nat_chain_everywhere():
    core = (ROOT / "awgpanel/core.py").read_text(encoding="utf-8")
    assert "POSTTRAFFIC" not in core
    assert "-A POSTROUTING" in core
    assert "-D POSTROUTING" in core
    assert '"POSTROUTING"' in core


def test_beta9_uses_valid_nftables_hooks():
    source = (ROOT / "awgpanel/outbounds.py").read_text(encoding="utf-8")
    assert "hook pretraffic" not in source
    assert "hook posttraffic" not in source
    assert "hook prerouting" in source
    assert "hook postrouting" in source


def test_beta9_uninstallers_have_no_malformed_systemd_paths():
    for relative in ("uninstall.sh", "deploy/uninstall.sh"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "/etc/systemd/system//etc/systemd/system" not in text
        assert "/etc/systemd/system/\\\n" not in text
        assert "sg-awg-clients-maintenance.timer" in text


def test_beta9_clean_install_and_update_require_traffic_service():
    install = (ROOT / "install-or-upgrade.sh").read_text(encoding="utf-8")
    first = (ROOT / "deploy/first-install.sh").read_text(encoding="utf-8")
    update = (ROOT / "deploy/update-from-github.sh").read_text(encoding="utf-8")
    assert 'install_fail "служба Traffic Rules не запустилась"' in install
    assert 'run_logged "Проверка Traffic Rules..."' in first
    assert "systemctl restart sg-awg-traffic.service" in update
    assert "systemctl is-active --quiet sg-awg-traffic.service" in update
    assert "temporarily unavailable" not in update


def test_beta9_navigation_and_diagnostics_are_user_facing():
    base = (ROOT / "awgpanel/templates/base.html").read_text(encoding="utf-8")
    system = (ROOT / "awgpanel/templates/system.html").read_text(encoding="utf-8")
    order = ["System", "Clients", "AWG Server", "Network", "Security", "Maintenance"]
    positions = [base.index(f"<b>{name}</b>") for name in order]
    assert positions == sorted(positions)
    assert "<b>Config</b>" not in base
    assert "<b>Traffic Rules</b>" not in base
    assert "<b>Outbounds</b>" not in base
    assert "<b>Backups</b>" not in base
    assert "<b>Updates</b>" not in base
    assert "СИСТЕМА В НОРМЕ" in (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")
    assert "NAT MASQUERADE" not in system
    assert "MASQUERADE" not in system
    assert "Доступ клиентов в интернет" in (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")


def test_beta9_global_status_accepts_sqlite_rows_and_resource_dials_exist():
    web = (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")
    system = (ROOT / "awgpanel/templates/system.html").read_text(encoding="utf-8")
    css = (ROOT / "awgpanel/static/app.css").read_text(encoding="utf-8")
    assert "settings = dict(get_awg_settings())" in web
    assert "settings.get(\"configured\"" in web
    assert "beta9-memory-dial" in system
    assert "SG-AWG-Panel" in system
    assert "Пиковая память панели" in system
    assert "Ядро Linux и сеть" in system or "segment.label" in system
    assert "conic-gradient" in css
    assert "--memory-panel" in css


def test_beta9_updater_waits_quietly_and_reports_preserved_tunnel():
    updater = (ROOT / "deploy/update-from-github.sh").read_text(encoding="utf-8")
    assert "2>/dev/null" in updater
    assert "Existing AWG configuration and tunnel state were preserved" in updater
    assert "fresh unconfigured server" not in updater


def test_candidate3_clarity_and_live_resource_updates():
    clients = (ROOT / "awgpanel/templates/clients.html").read_text(encoding="utf-8")
    security = (ROOT / "awgpanel/templates/security.html").read_text(encoding="utf-8")
    backups = (ROOT / "awgpanel/templates/backups.html").read_text(encoding="utf-8")
    system = (ROOT / "awgpanel/templates/system.html").read_text(encoding="utf-8")
    web = (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")
    assert "add-client-dialog" in clients
    assert "add-client-panel" not in clients
    assert "beta9-compact-action" in clients
    assert "Выдача конфигураций" in clients
    assert "Сейчас используется HTTP" in clients
    assert "Защищённые ссылки клиентов" not in security
    assert "Технические параметры веб-сервера" in security
    assert "event.display_type" in security
    assert "Техническое имя" in backups
    assert "resources.json" in web
    assert "setInterval(refreshResources, 10000)" in system
    assert "ОС и остальные процессы" in (ROOT / "awgpanel/core.py").read_text(encoding="utf-8")


def test_rc1_resource_endpoint_is_lightweight_and_no_store():
    web = (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")
    assert "def system_resources_json" in web
    assert "get_system_resources()" in web
    assert 'response.headers["Cache-Control"] = "no-store"' in web
    assert "-sgawg070rc6" in web


def test_rc3_system_information_architecture_and_removal_progress():
    system = (ROOT / "awgpanel/templates/system.html").read_text(encoding="utf-8")
    web = (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")
    uninstall = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
    access_job = (ROOT / "deploy/run-panel-access-job.sh").read_text(encoding="utf-8")
    access_progress = (ROOT / "awgpanel/templates/access_progress.html").read_text(encoding="utf-8")
    assert system.index("Resources") < system.index("Status &amp; Services") < system.index("Logs &amp; Diagnostics")
    assert "tab='status'" not in system
    assert "tab='services'" not in system
    assert "tab='logs'" not in system
    assert 'request.args.get("tab", "resources")' in web
    assert '"status": "status-services"' in web
    assert '"diagnostics": "logs-diagnostics"' in web
    assert 'Этап 1/4' not in uninstall  # stages are rendered dynamically
    assert 'stage 1 4 "Остановка компонентов"' in uninstall
    assert '[OK] %s... (%s сек)' in uninstall
    assert "spinner_loop()" in uninstall
    assert "STEP_SPINNER_PID" in uninstall
    assert "local frames='|/-\\\\'" in uninstall
    assert "[SG-AWG-Panel] [%s] %s... (%s сек)" in uninstall
    assert "5 сертификатов за последние 7 дней" in access_job
    assert "Это не ошибка SG-AWG-Panel, DNS или Nginx" in access_job
    assert "Временный лимит Let’s Encrypt" in access_progress
    assert "retry-countdown" in access_progress


def test_rc3_candidate2_consolidated_sections_and_system_landing():
    base = (ROOT / "awgpanel/templates/base.html").read_text(encoding="utf-8")
    server = (ROOT / "awgpanel/templates/server.html").read_text(encoding="utf-8")
    config = (ROOT / "awgpanel/templates/config.html").read_text(encoding="utf-8")
    rules = (ROOT / "awgpanel/templates/traffic_rules.html").read_text(encoding="utf-8")
    outbounds = (ROOT / "awgpanel/templates/outbounds.html").read_text(encoding="utf-8")
    backups = (ROOT / "awgpanel/templates/backups.html").read_text(encoding="utf-8")
    updates = (ROOT / "awgpanel/templates/updates.html").read_text(encoding="utf-8")
    web = (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")
    assert base.index("<b>System</b>") < base.index("<b>Clients</b>") < base.index("<b>AWG Server</b>") < base.index("<b>Network</b>") < base.index("<b>Security</b>") < base.index("<b>Maintenance</b>")
    assert "Server</a>" in server and ">JSON</a>" in server
    assert "Разделы AWG Server" in config
    assert "Full JSON" in config and "System Files" in config
    assert "Section JSON" not in config and "Активные конфигурации" in config
    assert "Разделы Network" in rules and "Разделы Network" in outbounds
    assert "Разделы Maintenance" in backups and "Разделы Maintenance" in updates
    assert web.count('return redirect(url_for("system_page"))') >= 3
    assert '-sgawg070rc6' in web


def test_rc3_candidate3_brand_favicon_is_hexagon_with_sg_only():
    favicon = (ROOT / "awgpanel/static/favicon.svg").read_text(encoding="utf-8")
    base = (ROOT / "awgpanel/templates/base.html").read_text(encoding="utf-8")
    login = (ROOT / "awgpanel/templates/login.html").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for name in ("favicon-32.png", "favicon-64.png", "favicon.ico", "apple-touch-icon.png"):
        assert (ROOT / "awgpanel/static" / name).is_file()
    assert ">SG</text>" in favicon
    assert ">SVG</text>" not in favicon
    assert 'aria-label="SG"' in base
    assert ">SG<" in base
    assert ">SG<" in login
    assert '"static/*"' in pyproject


def test_rc3_candidate3_certbot_terminal_uses_sse_and_unbuffered_nginx():
    progress = (ROOT / "awgpanel/templates/access_progress.html").read_text(encoding="utf-8")
    web = (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")
    runner = (ROOT / "deploy/run-panel-access-job.sh").read_text(encoding="utf-8")
    nginx = (ROOT / "deploy/configure-panel-access.sh").read_text(encoding="utf-8")
    assert "new EventSource" in progress
    assert "event: log" in web and 'content_type="text/event-stream' in web
    assert 'response.headers["X-Accel-Buffering"] = "no"' in web
    assert "PYTHONUNBUFFERED=1" in runner and "stdbuf -oL -eL" in runner
    assert "proxy_buffering off;" in nginx
    assert "proxy_cache off;" in nginx
    assert "proxy_read_timeout 3600s;" in nginx


def test_rc3_candidate3_generated_config_is_a_json_and_system_config_hub():
    config = (ROOT / "awgpanel/templates/config.html").read_text(encoding="utf-8")
    manager = (ROOT / "awgpanel/config_manager.py").read_text(encoding="utf-8")
    assert "Full JSON" in config
    assert "System Files" in config
    assert "Section JSON" not in config
    assert "Активные конфигурации" in config
    for label in ("server", "clients", "network", "security", "maintenance"):
        assert f'"{label}":' in manager
    assert "section_json_download" not in config
    assert "generated_config_download" in config


def test_rc3_candidate3_json_save_requires_validation_of_current_text():
    web = (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")
    full = (ROOT / "awgpanel/templates/config.html").read_text(encoding="utf-8")
    rules = (ROOT / "awgpanel/templates/traffic_rules_json.html").read_text(encoding="utf-8")
    outbound = (ROOT / "awgpanel/templates/outbound_json.html").read_text(encoding="utf-8")
    assert "issue_json_validation_ticket" in web
    assert "require_validated_json_submission" in web
    assert "validate_json_submission" in web
    assert "validate_panel_config_document" in web
    assert "validate_egress_runtime" in web
    assert "validate_outbound_config_runtime" in web
    assert 'id="save-full-json"' in full and "disabled" in full
    assert 'id="save-rules-json"' in rules and "disabled" in rules
    assert 'id="save-outbound-json"' in outbound and "disabled" in outbound


def test_rc3_candidate3_network_editor_is_compact_and_has_real_dry_run_copy():
    editor = (ROOT / "awgpanel/templates/traffic_rule_edit.html").read_text(encoding="utf-8")
    egress = (ROOT / "awgpanel/egress.py").read_text(encoding="utf-8")
    rules = (ROOT / "awgpanel/traffic_rules.py").read_text(encoding="utf-8")
    assert "network-rule-grid" in editor
    assert "simple-choice-grid" not in editor
    assert "nft -c -f" in editor
    assert '[_command("nft"), "-c", "-f"' in egress
    assert 'awg_quick, "strip"' in egress
    assert "неизвестные поля" in rules
    assert "дубликат приоритета" in rules


def test_rc3_candidate6_rule_json_validation_has_no_javascript_dependency():
    web = (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")
    editor = (ROOT / "awgpanel/templates/traffic_rule_edit.html").read_text(encoding="utf-8")
    assert "issue_json_validation_ticket" in web
    assert "require_json_validation_ticket" in web
    assert '_json_validation.html' in editor
    assert 'data-json-editor-form' in editor
    assert 'id="validate-rule-json" type="submit" name="action" value="validate"' in editor
    assert "fetch(endpoint" not in editor
    assert "Сохранение заблокировано" in editor
    assert "_json_validation.html" in editor


def test_rc3_candidate5_https_progress_migrates_with_no_cors_fetch():
    progress = (ROOT / "awgpanel/templates/access_progress.html").read_text(encoding="utf-8")
    assert "mode: 'no-cors'" in progress
    assert "window.location.replace(targetUrl + progressPath" in progress
    assert "window.setInterval(async()=>" in progress
    assert "HTTPS настроен. Переносим терминал" in progress
