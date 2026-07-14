import json
from pathlib import Path

import awgpanel.db as db
import awgpanel.web as web
import awgpanel.traffic_rules as traffic_rules
from werkzeug.security import check_password_hash, generate_password_hash


def login(client):
    with client.session_transaction() as session:
        session["csrf_token"] = "token"
    response = client.post(
        "/login",
        data={"password": "correct-password", "csrf_token": "token"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def settings():
    return {
        "configured": 0,
        "endpoint_host": "", "listen_port": 585, "interface_name": "awg0",
        "server_network": "10.77.0.0/24", "dns_servers": "1.1.1.1, 1.0.0.1",
        "external_interface": "", "mtu": 1280, "isolate_clients": 1,
        "jc": 6, "jmin": 64, "jmax": 128,
        "s1": 48, "s2": 48, "s3": 32, "s4": 16,
        "h1": "", "h2": "", "h3": "", "h4": "",
        "i1": "", "i2": "", "i3": "", "i4": "", "i5": "",
    }


def overview():
    return {
        "service_state": "inactive", "installed": True, "module_loaded": True,
        "configured": False, "clients": [], "active_clients": 0,
        "panel_rss_text": "12.0 MiB", "total_rx_text": "0 B", "total_tx_text": "0 B",
        "latest_backup": None,
        "resources": {"memory_percent": 20, "load1": 0, "load5": 0, "load15": 0},
        "settings": settings(), "public_ipv4_detected": "203.0.113.10",
        "external_interface_detected": "ens5",
        "config_path": "/etc/amnezia/amneziawg/awg0.conf",
    }


def diagnostics_data():
    return {
        "service_state": "inactive", "panel_state": "active", "panel_pid": 1,
        "panel_rss_text": "1 MiB", "listen_port": 585,
        "panel_enabled": True, "awg_enabled": True, "nginx_enabled": True,
        "recovery_enabled": True, "nginx_state": "active", "recovery_state": "active",
        "panel_uptime": "10 мин", "awg_uptime": "—",
        "ip_forward": True, "nat_rule": False, "boot_ready": True,
        "config_exists": True, "backend_port": 18080,
        "backend": {"listening": True, "loopback_only": True, "lines": []},
        "udp": {"listening": False, "lines": [], "error": ""},
        "interface_present": False, "module_loaded": True, "installed": True,
        "public_ipv4": "203.0.113.10", "external_interface": "ens5",
        "config_path": "/tmp/awg0.conf",
        "resources": {"memory_percent": 10, "load1": 0, "load5": 0, "load15": 0},
        "backups": [], "server_logs": "empty", "panel_logs": "empty",
        "nginx_logs": "empty", "recovery_logs": "empty",
    }


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setenv("AWGPANEL_PASSWORD_HASH", generate_password_hash("correct-password"))
    monkeypatch.setenv("AWGPANEL_SECRET_KEY", "test-secret")
    monkeypatch.setattr(web, "get_awg_overview", overview)
    monkeypatch.setattr(web, "get_awg_diagnostics", diagnostics_data)
    monkeypatch.setattr(web, "get_awg_settings", settings)
    monkeypatch.setattr(web, "list_awg_clients", lambda *args, **kwargs: [])
    monkeypatch.setattr(web, "list_backups", lambda limit=10: [])
    monkeypatch.setattr(web, "check_for_updates", lambda force=False: {"current":"v0.1.0-alpha8","latest":"v0.1.0-alpha8","available":False,"checked_at":"now","error":""})
    monkeypatch.setattr(web, "get_update_status", lambda: {"state":"idle","log":""})
    app = web.create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_one_click_server_save(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        web,
        "start_operation_job",
        lambda **values: (calls.append(values) or {"token": "a" * 43}),
    )
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/server",
        data={
            "csrf_token": token, "endpoint_host": "203.0.113.10",
            "listen_port": "585", "server_network": "10.77.0.0/24",
            "dns_servers": "1.1.1.1, 1.0.0.1", "external_interface": "ens5",
            "mtu": "1280",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/operations/" + "a" * 43)
    assert calls and calls[0]["kind"] == "server_config"
    assert calls[0]["payload"]["values"]["endpoint_host"] == "203.0.113.10"


def test_public_access_link_returns_current_config(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    row = {"id": 7, "name": "Windows PC"}
    recorded = []
    monkeypatch.setattr(web, "find_client_by_access_token", lambda token: row if token == "good" else None)
    monkeypatch.setattr(web, "render_awg_client_config", lambda client_id: "[Interface]\nAddress = 10.77.0.2/32\n")
    monkeypatch.setattr(web, "record_client_access", lambda client_id: recorded.append(client_id))
    response = client.get("/a/good")
    assert response.status_code == 200
    assert b"Address = 10.77.0.2/32" in response.data
    assert "Скачать .conf" in response.get_data(as_text=True)
    assert "Копировать конфигурацию" in response.get_data(as_text=True)
    download = client.get("/a/good/download")
    assert download.status_code == 200
    assert "SG-AWG-Windows-PC.conf" in download.headers["Content-Disposition"]
    assert recorded == [7, 7]


def test_diagnostic_report_download(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(web, "build_diagnostic_report", lambda: "safe diagnostic report\n")
    login(client)
    response = client.get("/diagnostics/report")
    assert response.status_code == 200
    assert response.data == b"safe diagnostic report\n"
    assert "sg-awg-panel-diagnostics.txt" in response.headers["Content-Disposition"]


def test_ui_uses_unified_readable_geometry_and_muted_blue_accent():
    css = (Path(__file__).resolve().parents[1] / "awgpanel" / "static" / "app.css").read_text()
    assert "--bg: #15191f" in css
    assert "--accent: #7b8fa4" in css
    assert "grid-template-columns: 258px minmax(0, 1fr)" in css
    assert "--content-width: 100%" in css
    assert "font-size: clamp(24px, 1.65vw, 28px)" in css
    assert ".ui-standard-page .page-stack" in css
    assert "table td { font-size: 14.5px; }" in css
    assert "input, textarea, select { font-size: 16px; }" in css
    assert "#5aa99d" not in css


def test_client_edit_page_and_save(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    row = {
        "id": 3,
        "name": "Laptop",
        "comment": "work",
        "dns_servers": "",
        "mtu": None,
    }
    monkeypatch.setattr(web, "find_awg_client", lambda client_id: dict(row))
    recorded = []
    monkeypatch.setattr(
        web,
        "update_awg_client_settings",
        lambda client_id, **values: recorded.append((client_id, values)) or {**row, **values},
    )
    login(client)
    response = client.get("/clients/3/edit")
    assert response.status_code == 200
    edit_text = response.get_data(as_text=True)
    assert "DNS клиента" not in edit_text
    assert "DNS настраивается автоматически" in edit_text
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/clients/3/edit",
        data={
            "csrf_token": token,
            "name": "Laptop 2",
            "comment": "new",
            "mtu": "1360",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert recorded[0][0] == 3
    assert recorded[0][1]["dns_servers"] == ""


def test_security_owns_https_domain_and_dynamic_port(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    response = client.get("/security")
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Доступ к панели" in text
    assert "49152–65535" in text
    assert "manage_placeholder" in text
    assert "E-mail Let's Encrypt" not in text
    assert client.get("/settings").status_code == 302
    assert client.get("/access").status_code == 302
    navigation = client.get("/").get_data(as_text=True)
    assert ">Настройки<" not in navigation



def test_password_change_accepts_eight_and_rejects_seven(tmp_path, monkeypatch):
    env_file = tmp_path / "web.env"
    monkeypatch.setenv("AWGPANEL_ENV_FILE", str(env_file))
    client = make_client(tmp_path, monkeypatch)
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]

    rejected = client.post(
        "/security/password",
        data={
            "csrf_token": token,
            "current_password": "correct-password",
            "new_password": "1234567",
            "new_password_2": "1234567",
        },
        follow_redirects=False,
    )
    assert rejected.status_code == 302
    assert not env_file.exists()

    accepted = client.post(
        "/security/password",
        data={
            "csrf_token": token,
            "current_password": "correct-password",
            "new_password": "12345678",
            "new_password_2": "12345678",
        },
        follow_redirects=False,
    )
    assert accepted.status_code == 302
    assert "password_changed=1" in accepted.headers["Location"]
    stored = next(
        line.split("=", 1)[1]
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("AWGPANEL_PASSWORD_HASH=")
    )
    assert check_password_hash(stored, "12345678")


def test_outbound_add_route_uses_csrf_and_service_layer(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    recorded = []
    monkeypatch.setattr(
        web,
        "create_outbound",
        lambda name, config_text: recorded.append((name, config_text)) or {"name": name},
    )
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/traffic-rules/outbounds",
        data={"csrf_token": token, "name": "Germany", "config_text": "[Interface]"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert recorded == [("Germany", "[Interface]")]


def test_client_egress_route_passes_selected_outbound(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    recorded = []
    monkeypatch.setattr(
        web,
        "set_client_egress",
        lambda client_id, mode, outbound_id: recorded.append((client_id, mode, outbound_id))
        or {"name": "Phone", "egress_mode": mode},
    )
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/traffic-rules/egress/7",
        data={"csrf_token": token, "egress_mode": "outbound", "outbound_id": "2"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert recorded == [(7, "outbound", 2)]



def test_global_access_switch_controls_public_links(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    row = {"id": 7, "name": "Phone"}
    monkeypatch.setattr(web, "find_client_by_access_token", lambda token: row)
    monkeypatch.setattr(web, "render_awg_client_config", lambda client_id: "[Interface]\n")
    monkeypatch.setattr(web, "record_client_access", lambda client_id: None)

    with db.connect() as con:
        con.execute("UPDATE panel_settings SET access_enabled=0 WHERE id=1")
    assert client.get("/a/secret").status_code == 404

    with db.connect() as con:
        con.execute("UPDATE panel_settings SET access_enabled=1, access_profile_title='Family VPN' WHERE id=1")
    response = client.get("/a/secret")
    assert response.status_code == 200
    assert "Скачать .conf" in response.get_data(as_text=True)
    download = client.get("/a/secret/download")
    assert "Family-VPN-Phone.conf" in download.headers["Content-Disposition"]


def test_access_settings_route_calls_service_layer(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    recorded = []
    monkeypatch.setattr(
        web,
        "configure_access_links",
        lambda **values: recorded.append(values) or {"access_enabled": 1},
    )
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/access/settings",
        data={
            "csrf_token": token,
            "access_enabled": "1",
            "access_profile_title": "Home AWG",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert recorded == [{"enabled": True, "profile_title": "Home AWG"}]


def test_alpha15_templates_do_not_copy_xray_terms():
    root = Path(__file__).resolve().parents[1]
    clients = (root / "awgpanel" / "templates" / "clients.html").read_text(encoding="utf-8")
    config = (root / "awgpanel" / "templates" / "client_config.html").read_text(encoding="utf-8")
    combined = clients + config
    assert "VLESS" not in combined
    assert "UUID" not in combined
    assert "Xray" not in combined
    assert "Конфигурация / QR" not in combined
    assert "Конфигурация" in combined
    assert "QR-код" in combined
    assert "Персональная подписка" in combined



def test_client_json_page_uses_same_client_model(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    row = {
        "id": 3, "name": "Laptop", "enabled": 1, "comment": "work",
        "address": "10.77.0.2/32", "private_key": "secret",
        "public_key": "public", "preshared_key": "psk", "dns_servers": "",
        "mtu": None, "access_enabled": 1, "allowed_ips": "0.0.0.0/0",
        "excluded_ips": "", "advertised_networks": "", "include_server_lan": 0,
        "egress_mode": "awg_gateway", "outbound_id": None,
    }
    monkeypatch.setattr(web, "find_awg_client", lambda client_id: dict(row))
    login(client)
    response = client.get("/clients/3/json")
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "JSON клиента Laptop" in text
    assert "&quot;$KEEP&quot;" in text or "$KEEP" in text


def test_server_json_validate_does_not_apply(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    values = settings()
    values["endpoint_host"] = "203.0.113.10"
    values["external_interface"] = "ens5"
    monkeypatch.setattr(web, "parse_server_json_document", lambda source: (dict(values), False))
    monkeypatch.setattr(
        web,
        "configure_and_start_awg",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not apply")),
    )
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/server/json",
        data={"csrf_token": token, "json_config": "{}", "action": "validate"},
    )
    assert response.status_code == 200
    assert "Изменения не применены" in response.get_data(as_text=True)


def test_new_outbound_json_validate_does_not_create(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        web,
        "parse_outbound_json_document",
        lambda source: ("Europe", "[Interface]\n", True),
    )
    monkeypatch.setattr(web, "validate_outbound_config_runtime", lambda config: {"ok": True})
    monkeypatch.setattr(
        web,
        "create_outbound",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not create")),
    )
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/outbounds/json/new",
        data={"csrf_token": token, "json_config": "{}", "action": "validate"},
    )
    assert response.status_code == 200
    assert "Профиль не создан" in response.get_data(as_text=True)


def test_config_validate_does_not_apply(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(web, "panel_config_document", lambda: "{}\n")
    monkeypatch.setattr(web, "generated_configs", lambda: {})
    recorded = []
    monkeypatch.setattr(web, "validate_panel_config_document", lambda source: recorded.append(source) or {"traffic_policy_rules": [], "outbounds": []})
    monkeypatch.setattr(
        web,
        "apply_panel_config_document",
        lambda source: (_ for _ in ()).throw(AssertionError("must not apply")),
    )
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/config",
        data={"csrf_token": token, "json_config": '{"ok":true}', "action": "validate"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert recorded == ['{"ok":true}']
    assert 'config-current-workspace is-editing' in text
    assert 'value="{&quot;ok&quot;:true}"' not in text
    assert '{&#34;ok&#34;:true}' in text
    assert "Изменения не применены" in text


def test_config_generated_section_is_explicitly_read_only(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(web, "panel_config_document", lambda: "{}\n")
    monkeypatch.setattr(
        web,
        "generated_configs",
        lambda: {
            "nginx": {
                "title": "Nginx",
                "filename": "nginx.conf",
                "content": "listen 62443;\n",
            }
        },
    )
    login(client)
    text = client.get("/config").get_data(as_text=True)
    assert "Активные конфигурации" in text
    assert "Только чтение, копирование и скачивание" in text
    assert 'data-copy-target="system-config-nginx"' in text
    assert "Сохранить и применить" in text


def test_security_access_route_starts_progress_job(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    recorded = []
    job_token = "a" * 32
    monkeypatch.setattr(
        web,
        "start_panel_access_job",
        lambda **values: recorded.append(values) or {
            "token": job_token, "target_url": "http://SERVER_IP:62444",
        },
    )
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/security/access",
        data={
            "csrf_token": token,
            "public_scheme": "http",
            "public_host": "",
            "public_port": "62444",
            "manage_placeholder": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert recorded == [{
        "scheme": "http", "public_host": "", "public_port": "62444",
        "manage_placeholder": "1",
    }]
    assert response.headers["Location"].endswith(f"/security/access/jobs/{job_token}")


def test_panel_access_progress_status_and_complete(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    job_token = "b" * 32
    job = {
        "state": "success",
        "message": "HTTPS готов",
        "targetUrl": "https://panel.example:62443",
        "log": "certificate ready",
    }
    monkeypatch.setattr(web, "get_panel_access_job", lambda token: job if token == job_token else None)
    login(client)

    progress = client.get(f"/security/access/jobs/{job_token}")
    assert progress.status_code == 200
    text = progress.get_data(as_text=True)
    assert "Настройка публичного доступа" in text
    assert "Живой терминал Certbot и Nginx" in text
    assert "Полный вывод Certbot и Nginx" in text
    assert "Проверить сейчас" in text
    assert "Открыть страницу входа" in text

    status = client.get(f"/security/access/jobs/{job_token}/status")
    assert status.status_code == 200
    assert status.get_json()["state"] == "success"

    probe = client.get(f"/security/access/jobs/{job_token}/probe.gif")
    assert probe.status_code == 200
    assert probe.mimetype == "image/gif"

    complete = client.get(f"/security/access/jobs/{job_token}/complete", follow_redirects=False)
    assert complete.status_code == 302
    assert "access_changed=1" in complete.headers["Location"]


def test_alpha21_simple_rule_form_has_form_and_json_without_empty_profile(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    text = client.get("/traffic-rules/new").get_data(as_text=True)
    assert "Назначение" in text
    assert "Действие" in text
    assert "Клиенты" in text
    assert "Открыть JSON" in text
    assert 'value="awg_gateway"' not in text
    assert "Outbound-профиль" not in text
    json_text = client.get("/traffic-rules/new?view=json").get_data(as_text=True)
    assert "TRAFFIC-RULE-V1" in json_text
    assert '&#34;action&#34;: &#34;block&#34;' in json_text


def test_alpha21_simple_domain_rule_enables_dns_redirect(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(web, "mutate_traffic_and_apply", lambda callback: callback())
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/traffic-rules/new",
        data={
            "csrf_token": token,
            "editor": "form",
            "target_type": "domain",
            "targets": "Example.COM",
            "action_mode": "block",
            "client_scope": "all",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    saved = traffic_rules.list_traffic_rules(include_system=False)
    assert len(saved) == 1
    assert saved[0]["inline_domains"] == "example.com"
    assert saved[0]["action_mode"] == "block"
    dns = traffic_rules.get_dns_traffic_settings()
    assert dns["mode"] == "redirect"
    assert dns["advertise_to_clients"] == 1


def test_alpha16_traffic_rules_json_save_uses_atomic_wrapper(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(web, "rules_json_document", lambda: "{}\n")
    monkeypatch.setattr(web, "parse_rules_json_document", lambda source: [])
    calls = []

    def atomic(callback):
        calls.append("atomic")
        return callback()

    monkeypatch.setattr(web, "mutate_traffic_and_apply", atomic)
    monkeypatch.setattr(web, "replace_rules_document", lambda rules: calls.append(rules))
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    monkeypatch.setattr(web, "validate_egress_runtime", lambda **kwargs: {"nft": "ok"})
    checked = client.post(
        "/traffic-rules/json",
        data={"csrf_token": token, "json_config": "{}", "action": "validate"},
        follow_redirects=False,
    )
    assert checked.status_code == 200
    import re
    match = re.search(r'name="validation_token" value="([^"]+)"', checked.get_data(as_text=True))
    assert match
    response = client.post(
        "/traffic-rules/json",
        data={"csrf_token": token, "json_config": "{}", "action": "save", "validation_token": match.group(1)},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert calls == ["atomic", []]


def test_expired_client_config_redirects_without_internal_error(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    row = {
        "id": 9,
        "name": "Expired phone",
        "enabled": 1,
        "expires_at": "2020-01-01 00:00:00",
    }
    monkeypatch.setattr(web, "find_awg_client", lambda client_id: row)
    monkeypatch.setattr(
        web,
        "render_awg_client_config",
        lambda client_id: (_ for _ in ()).throw(web.AWGPanelError("Срок действия клиента истёк")),
    )
    login(client)
    response = client.get("/clients/9/config", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/clients/9/edit")


def test_clients_bulk_route_applies_one_action(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        web,
        "bulk_update_awg_clients",
        lambda ids, *, action, expires_at=None: calls.append((ids, action, expires_at)) or len(ids),
    )
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/clients/bulk",
        data={
            "csrf_token": token,
            "client_ids": ["2", "5"],
            "bulk_action": "extend_30",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert calls == [([2, 5], "extend_30", None)]


def test_client_config_and_access_pages_render_clean_titles(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    row = {
        "id": 4,
        "name": "Phone",
        "enabled": 1,
        "expires_at": "2050-01-01 00:00:00",
        "access_token": "secret",
        "access_enabled": 1,
        "access_downloads": 0,
        "access_last_at": None,
        "address": "10.77.0.2/32",
    }
    monkeypatch.setattr(web, "find_awg_client", lambda client_id: row)
    monkeypatch.setattr(web, "render_awg_client_config", lambda client_id: "[Interface]\nAddress = 10.77.0.2/32\n")
    monkeypatch.setattr(web, "list_awg_clients", lambda *args, **kwargs: [row])
    login(client)

    response = client.get("/clients/4/config")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "<title>Конфигурация клиента — SG-AWG-Panel</title>" in text
    assert "Скачать .conf" in text
    assert "Копировать конфигурацию" in text
    assert "QR-код" in text
    assert "Персональная подписка" in text

    response = client.get("/access")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/clients")
    assert "Защищённая ссылка" in text


def test_rc3_access_is_not_a_main_page_and_clients_own_global_switch(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    base = client.get("/").get_data(as_text=True)
    assert 'href="/access"' not in base
    clients_page = client.get("/clients").get_data(as_text=True)
    security = client.get("/security").get_data(as_text=True)
    assert "Выдача конфигураций" in clients_page
    assert "Защищённые ссылки клиентов" in clients_page
    assert "Защищённые ссылки клиентов" not in security
    assert client.get("/access").status_code == 302


def test_beta3_security_exposes_placeholder_and_nginx_controls(tmp_path, monkeypatch):
    placeholder = tmp_path / "index.html"
    monkeypatch.setattr(web, "read_placeholder_html", lambda: "<html>ok</html>")
    monkeypatch.setattr(web, "save_placeholder_html", lambda value: placeholder)
    monkeypatch.setattr(web, "reset_placeholder_html", lambda: placeholder)
    client = make_client(tmp_path, monkeypatch)
    login(client)
    text = client.get("/security").get_data(as_text=True)
    assert "Изменить заглушку" in text
    assert "System Files" in text
    page = client.get("/security/placeholder")
    assert page.status_code == 200
    assert "Полный HTML" in page.get_data(as_text=True) or "Простая страница" in page.get_data(as_text=True)


def test_beta3_update_page_replaces_dashboard_redirect(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    monkeypatch.setattr(web, "start_panel_update", lambda version: {"version": version, "unit": "sg-awg-panel-update.service"})
    with client.session_transaction() as state:
        token = state["csrf_token"]
    response = client.post("/settings/update/start", data={"csrf_token": token, "version": "v0.1.0-beta4"})
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Устанавливается v0.1.0-beta4" in text
    assert "/sg-awg-update/status.json" in text
    assert "/login?updated=1" in text


def test_rc3_lands_on_system_resources_and_uses_clean_navigation(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    response = client.get("/", follow_redirects=True)
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Оперативная память" in text
    assert "System" in text
    assert "Network" in text
    assert "Maintenance" in text
    assert "Overview" not in text
    assert "Защита сервера" not in text
    assert "Routing" not in text


def test_beta8_traffic_rules_page_contains_only_real_actions(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    text = client.get("/traffic-rules").get_data(as_text=True)
    assert "Block и Outbound" in text
    assert "Добавить правило" in text
    assert "Вредоносные" not in text
    assert "Реклама и трекеры" not in text
    assert "BitTorrent" not in text
    assert "AWG-Gateway" not in text


def test_beta8_main_pages_render(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    for path in ("/clients", "/server", "/network", "/network?tab=outbounds", "/outbounds", "/traffic-rules", "/security", "/maintenance", "/maintenance?tab=updates", "/backups", "/updates", "/system", "/config"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_beta8_clients_workspace_contains_json_and_details(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    text = client.get("/clients").get_data(as_text=True)
    assert "clients-modern-table" in text
    assert "Current JSON" not in text
    assert "Network" in text


def test_beta9_protected_link_keeps_public_port(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    row = {
        "id": 9, "name": "Phone", "enabled": 1, "expires_at": None,
        "access_token": "secret-token", "access_enabled": 1,
        "access_downloads": 0, "access_last_at": None,
    }
    monkeypatch.setattr(web, "find_awg_client", lambda client_id: row)
    monkeypatch.setattr(web, "render_awg_client_config", lambda client_id: "[Interface]\nAddress = 10.77.0.9/32\n")
    base_url = "http://63.177.95.84:62443"
    with client.session_transaction(base_url=base_url) as session:
        session["csrf_token"] = "token"
    response = client.post(
        "/login",
        base_url=base_url,
        data={"password": "correct-password", "csrf_token": "token"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    response = client.get(
        "/clients/9/config",
        base_url=base_url,
    )
    assert response.status_code == 200
    assert 'value="http://63.177.95.84:62443/a/secret-token"' in response.get_data(as_text=True)


def test_authenticated_page_renders_when_awg_settings_is_sqlite_row(tmp_path, monkeypatch):
    import sqlite3

    client = make_client(tmp_path, monkeypatch)
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT 0 AS configured").fetchone()
    monkeypatch.setattr(web, "get_awg_settings", lambda: row)
    login(client)
    response = client.get("/system?tab=resources")
    assert response.status_code == 200
    assert "Оперативная память" in response.get_data(as_text=True)


def test_rc3_combined_sections_render_expected_tabs(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    network = client.get("/network").get_data(as_text=True)
    maintenance = client.get("/maintenance").get_data(as_text=True)
    server = client.get("/server").get_data(as_text=True)
    config = client.get("/config").get_data(as_text=True)
    assert "Разделы Network" in network
    assert "Traffic Rules" in network and "Outbounds" in network
    assert "Разделы Maintenance" in maintenance
    assert "Backups" in maintenance and "Updates" in maintenance
    assert "Разделы AWG Server" in server
    assert "Server" in server and ">JSON</a>" in server
    assert "Разделы AWG Server" in config
    assert "Full JSON" in config and "System Files" in config


def test_rc3_candidate3_traffic_json_rejects_unknown_fields_before_apply(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    document = {
        "_sgAwgPanel": {"format": "traffic-rules-v1"},
        "rules": [],
        "madeUpSetting": True,
    }
    response = client.post(
        "/traffic-rules/json",
        data={"csrf_token": token, "json_config": json.dumps(document), "action": "validate"},
    )
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "неизвестные поля" in text
    assert "madeUpSetting" in text
    assert "ПРОВЕРЕНО" not in text


def test_rc3_candidate3_traffic_json_rejects_duplicate_priorities(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    def rule(name, domain):
        return {
            "priority": 100, "name": name, "enabled": True, "clientIds": [],
            "listId": None, "inlineDomains": [domain], "inlineCIDRs": [],
            "protocol": "any", "ports": "", "invert": False, "schedule": "",
            "action": "block", "outboundId": None,
        }
    document = {
        "_sgAwgPanel": {"format": "traffic-rules-v1"},
        "rules": [rule("First", "one.example"), rule("Second", "two.example")],
    }
    response = client.post(
        "/traffic-rules/json",
        data={"csrf_token": token, "json_config": json.dumps(document), "action": "validate"},
    )
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "дубликат приоритета 100" in text
    assert "ПРОВЕРЕНО" not in text


def test_rc3_candidate3_access_events_stream_log_and_terminal_status(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    token = "s" * 32
    monkeypatch.setattr(
        web,
        "get_panel_access_job",
        lambda value: {
            "state": "success",
            "message": "HTTPS готов",
            "targetUrl": "https://vpn.example:62443",
            "log": "Requesting a certificate\nCertificate is saved at: /tmp/fullchain.pem\n",
        } if value == token else None,
    )
    ticks = iter((1.0, 2.0, 3.0, 4.0, 5.0))
    monkeypatch.setattr(web.time, "monotonic", lambda: next(ticks, 10.0))
    monkeypatch.setattr(web.time, "sleep", lambda _: None)
    response = client.get(f"/security/access/jobs/{token}/events")
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert response.headers["X-Accel-Buffering"] == "no"
    assert "event: log" in text
    assert "Certificate is saved at" in text
    assert "event: status" in text
    assert "event: end" in text


def test_rc3_candidate4_rule_json_ajax_reports_line_and_column(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/traffic-rules/validate-json",
        data={"csrf_token": token, "rule_id": "", "json_config": '{"rule": {'},
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data["ok"] is False
    assert data["kind"] == "syntax"
    assert data["path"] == "$"
    assert data["line"] == 1
    assert data["column"] >= 1


def test_rc3_candidate4_rule_json_ajax_reports_exact_path(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    monkeypatch.setattr(
        web,
        "parse_traffic_rule_json_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("rule.action: неизвестное действие abrakadabra")
        ),
    )
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/traffic-rules/validate-json",
        data={"csrf_token": token, "rule_id": "", "json_config": "{}"},
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data["path"] == "rule.action"
    assert "abrakadabra" in data["reason"]


def test_rc3_candidate4_access_progress_can_resume_on_new_origin_without_login(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    token = "z" * 32
    job = {
        "state": "running",
        "message": "Certbot выполняется",
        "targetUrl": "https://vpn.example:62443",
        "log": "Requesting a certificate\n",
    }
    monkeypatch.setattr(web, "get_panel_access_job", lambda value: job if value == token else None)
    progress = client.get(f"/security/access/jobs/{token}")
    assert progress.status_code == 200
    text = progress.get_data(as_text=True)
    assert "migrateToTargetProgress" in text
    assert "alive.gif" in text
    alive = client.get(f"/security/access/jobs/{token}/alive.gif")
    assert alive.status_code == 200
    assert alive.mimetype == "image/gif"


def _valid_new_rule_json(name="Validated rule"):
    return json.dumps({
        "_sgAwgPanel": {"format": "traffic-rule-v1", "id": None},
        "rule": {
            "id": None,
            "priority": 10,
            "name": name,
            "enabled": True,
            "clientIds": [],
            "listId": None,
            "inlineDomains": ["example.com"],
            "inlineCIDRs": [],
            "protocol": "any",
            "ports": "",
            "invert": False,
            "schedule": "",
            "action": "block",
            "outboundId": None,
        },
    }, ensure_ascii=False)


def test_rule_json_validation_returns_exact_syntax_location_and_no_ticket(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(web, "rules_json_document", lambda: '{"_sgAwgPanel":{"format":"traffic-rules-v1"},"rules":[]}')
    login(client)
    with client.session_transaction() as state:
        csrf = state["csrf_token"]
    response = client.post(
        "/traffic-rules/validate-json",
        data={
            "csrf_token": csrf,
            "json_config": '{"_sgAwgPanel":{"format":"traffic-rule-v1"},"rule": { nonsense }}',
            "rule_id": "",
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["kind"] == "syntax"
    assert payload["line"] == 1
    assert payload["column"] > 1
    assert payload["path"] == "$"
    assert "validationToken" not in payload


def test_rule_json_save_requires_one_time_ticket_for_exact_text(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    empty_rules = '{"_sgAwgPanel":{"format":"traffic-rules-v1"},"rules":[]}'
    monkeypatch.setattr(web, "rules_json_document", lambda: empty_rules)
    monkeypatch.setattr(web, "validate_egress_runtime", lambda **kwargs: {"ok": True})
    saved = []
    monkeypatch.setattr(web, "save_traffic_rule", lambda rule_id, values: saved.append(dict(values)) or {"id": 1, **values})
    monkeypatch.setattr(web, "ensure_rule_dns_control", lambda *args, **kwargs: None)
    monkeypatch.setattr(web, "mutate_traffic_and_apply", lambda mutation: mutation())
    login(client)
    with client.session_transaction() as state:
        csrf = state["csrf_token"]

    source = _valid_new_rule_json()

    # A visually enabled or manually crafted Save request cannot bypass validation.
    rejected = client.post(
        "/traffic-rules/new?view=json",
        data={
            "csrf_token": csrf,
            "editor": "json",
            "json_config": source,
            "validation_token": "forged",
            "action": "save",
        },
        follow_redirects=False,
    )
    assert rejected.status_code == 200
    assert not saved
    assert "JSON изменён после проверки" in rejected.get_data(as_text=True) or "Сначала выполните проверку" in rejected.get_data(as_text=True)

    validated = client.post(
        "/traffic-rules/validate-json",
        data={"csrf_token": csrf, "json_config": source, "rule_id": ""},
        headers={"X-Requested-With": "fetch"},
    )
    assert validated.status_code == 200
    token = validated.get_json()["validationToken"]
    assert token

    # The ticket is bound to the exact bytes that were checked.
    modified = _valid_new_rule_json("Changed after validation")
    stale = client.post(
        "/traffic-rules/new?view=json",
        data={
            "csrf_token": csrf,
            "editor": "json",
            "json_config": modified,
            "validation_token": token,
            "action": "save",
        },
        follow_redirects=False,
    )
    assert stale.status_code == 200
    assert not saved
    assert "JSON изменён после проверки" in stale.get_data(as_text=True)

    # A fresh ticket for the exact text permits one save only.
    validated = client.post(
        "/traffic-rules/validate-json",
        data={"csrf_token": csrf, "json_config": source, "rule_id": ""},
        headers={"X-Requested-With": "fetch"},
    )
    token = validated.get_json()["validationToken"]
    applied = client.post(
        "/traffic-rules/new?view=json",
        data={
            "csrf_token": csrf,
            "editor": "json",
            "json_config": source,
            "validation_token": token,
            "action": "save",
        },
        follow_redirects=False,
    )
    assert applied.status_code == 302
    assert len(saved) == 1

    replay = client.post(
        "/traffic-rules/new?view=json",
        data={
            "csrf_token": csrf,
            "editor": "json",
            "json_config": source,
            "validation_token": token,
            "action": "save",
        },
        follow_redirects=False,
    )
    assert replay.status_code == 200
    assert len(saved) == 1
    assert "Сначала выполните проверку" in replay.get_data(as_text=True)


def test_rule_json_validation_works_with_plain_form_submit(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    empty_rules = '{"_sgAwgPanel":{"format":"traffic-rules-v1"},"rules":[]}'
    monkeypatch.setattr(web, "rules_json_document", lambda: empty_rules)
    monkeypatch.setattr(web, "validate_egress_runtime", lambda **kwargs: {"ok": True})
    login(client)
    with client.session_transaction() as state:
        csrf = state["csrf_token"]

    broken = client.post(
        "/traffic-rules/new?view=json",
        data={
            "csrf_token": csrf,
            "editor": "json",
            "json_config": '{"rule": { nonsense }}',
            "action": "validate",
        },
    )
    text = broken.get_data(as_text=True)
    assert broken.status_code == 200
    assert "JSON не прошёл проверку" in text
    assert "строка 1" in text
    assert "столбец" in text
    assert 'id="save-rule-json"' in text
    assert 'id="save-rule-json"' in text
    assert 'disabled' in text

    source = _valid_new_rule_json()
    valid = client.post(
        "/traffic-rules/new?view=json",
        data={
            "csrf_token": csrf,
            "editor": "json",
            "json_config": source,
            "action": "validate",
        },
    )
    text = valid.get_data(as_text=True)
    assert valid.status_code == 200
    assert "ПРОВЕРЕНО" in text
    assert 'name="validation_token"' in text
    assert 'id="save-rule-json" type="submit" name="action" value="save" disabled' not in text


def test_rc3_candidate7_client_json_has_inline_errors_and_one_time_server_ticket(tmp_path, monkeypatch):
    import re

    client = make_client(tmp_path, monkeypatch)
    row = {
        "id": 3, "name": "Laptop", "enabled": 1, "comment": "work",
        "address": "10.77.0.2/32", "private_key": "secret",
        "public_key": "public", "preshared_key": "psk", "dns_servers": "",
        "mtu": None, "access_enabled": 1, "allowed_ips": "0.0.0.0/0",
        "excluded_ips": "", "advertised_networks": "", "include_server_lan": 0,
        "egress_mode": "awg_gateway", "outbound_id": None,
    }
    monkeypatch.setattr(web, "find_awg_client", lambda client_id: dict(row))
    monkeypatch.setattr(web, "validate_awg_client_document", lambda client_id, values: values)
    saved = []
    monkeypatch.setattr(
        web,
        "update_awg_client_document",
        lambda client_id, values: saved.append((client_id, values)) or values,
    )
    login(client)
    with client.session_transaction() as state:
        csrf = state["csrf_token"]

    broken = client.post(
        "/clients/3/json",
        data={"csrf_token": csrf, "json_config": '{"client": { nonsense }}', "action": "validate"},
    )
    broken_text = broken.get_data(as_text=True)
    assert broken.status_code == 200
    assert "JSON не прошёл проверку" in broken_text
    assert "строка 1" in broken_text and "столбец" in broken_text
    assert "Сохранить и применить JSON" in broken_text
    assert "disabled" in broken_text
    assert saved == []

    bypass = client.post(
        "/clients/3/json",
        data={"csrf_token": csrf, "json_config": '{"client": { nonsense }}', "action": "save"},
    )
    assert bypass.status_code == 200
    assert "Сначала выполните проверку текущего JSON" in bypass.get_data(as_text=True)
    assert saved == []

    valid_source = web.client_json_document(row, settings())
    checked = client.post(
        "/clients/3/json",
        data={"csrf_token": csrf, "json_config": valid_source, "action": "validate"},
    )
    checked_text = checked.get_data(as_text=True)
    assert "JSON прошёл проверку" in checked_text
    ticket = re.search(r'name="validation_token" value="([^"]+)"', checked_text)
    assert ticket

    changed_source = valid_source.replace('"name": "Laptop"', '"name": "Laptop X"')
    changed = client.post(
        "/clients/3/json",
        data={
            "csrf_token": csrf,
            "json_config": changed_source,
            "action": "save",
            "validation_token": ticket.group(1),
        },
    )
    assert changed.status_code == 200
    assert "JSON изменён после проверки" in changed.get_data(as_text=True)
    assert saved == []

    checked_again = client.post(
        "/clients/3/json",
        data={"csrf_token": csrf, "json_config": changed_source, "action": "validate"},
    )
    ticket2 = re.search(
        r'name="validation_token" value="([^"]+)"', checked_again.get_data(as_text=True)
    )
    assert ticket2
    applied = client.post(
        "/clients/3/json",
        data={
            "csrf_token": csrf,
            "json_config": changed_source,
            "action": "save",
            "validation_token": ticket2.group(1),
        },
        follow_redirects=False,
    )
    assert applied.status_code == 302
    assert saved and saved[0][0] == 3
    assert saved[0][1]["name"] == "Laptop X"


def test_rc3_candidate7_every_editable_json_page_uses_shared_validation_widget():
    root = Path(__file__).resolve().parents[1] / "awgpanel" / "templates"
    for name in (
        "client_json.html", "server_json.html", "outbound_json.html",
        "traffic_rules_json.html", "traffic_rule_edit.html", "section_json.html",
        "config.html",
    ):
        text = (root / name).read_text(encoding="utf-8")
        assert "data-json-editor-form" in text, name
        assert "data-json-editor-input" in text, name
        assert "data-json-save" in text, name
        assert "_json_validation.html" in text, name
        assert "_json_editor_script.html" in text, name


def test_rc3_candidate7_waitress_is_configured_to_flush_sse_immediately():
    root = Path(__file__).resolve().parents[1]
    service = (root / "deploy" / "install-service.sh").read_text(encoding="utf-8")
    web_source = (root / "awgpanel" / "web.py").read_text(encoding="utf-8")
    assert "--send-bytes=1" in service
    assert "--outbuf-high-watermark=1" in service
    assert 'yield "retry: 750\\n:" + (" " * 2048)' in web_source
    assert 'response.headers["X-Accel-Buffering"] = "no"' in web_source
    assert 'response.headers["Connection"]' not in web_source
