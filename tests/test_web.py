from pathlib import Path

import awgpanel.db as db
import awgpanel.web as web
from werkzeug.security import generate_password_hash


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
    monkeypatch.setattr(web, "list_awg_clients", lambda: [])
    monkeypatch.setattr(web, "list_backups", lambda limit=10: [])
    monkeypatch.setattr(web, "check_for_updates", lambda force=False: {"current":"v0.1.0-alpha7","latest":"v0.1.0-alpha7","available":False,"checked_at":"now","error":""})
    monkeypatch.setattr(web, "get_update_status", lambda: {"state":"idle","log":""})
    app = web.create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_health_login_and_new_navigation(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    assert client.get("/health").data == b"ok\n"
    assert client.get("/").status_code == 302
    login(client)

    response = client.get("/")
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "ПАМЯТЬ ПАНЕЛИ" in text
    assert "Routing" in text
    assert "Доступ" in text

    response = client.get("/server")
    assert response.status_code == 200
    assert "Сохранить и запустить" in response.get_data(as_text=True)

    response = client.get("/diagnostics")
    assert response.status_code == 200
    assert "NGINX" in response.get_data(as_text=True)


def test_one_click_server_save(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(web, "configure_and_start_awg", lambda **values: (calls.append(values), "active"))
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
    assert calls and calls[0]["endpoint_host"] == "203.0.113.10"


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
    assert "Windows-PC-awg.conf" in response.headers["Content-Disposition"]
    assert recorded == [7]


def test_routing_page_is_separate(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    response = client.get("/routing")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Изолировать клиентов" in text
    assert "AllowedIPs" in text


def test_all_main_pages_render(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    for path in ("/clients", "/access", "/routing", "/dns", "/backups", "/security", "/settings"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_diagnostic_report_download(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(web, "build_diagnostic_report", lambda: "safe diagnostic report\n")
    login(client)
    response = client.get("/diagnostics/report")
    assert response.status_code == 200
    assert response.data == b"safe diagnostic report\n"
    assert "sg-awg-panel-diagnostics.txt" in response.headers["Content-Disposition"]


def test_ui_uses_sg_panel_geometry_and_muted_blue_accent():
    css = (Path(__file__).resolve().parents[1] / "awgpanel" / "static" / "app.css").read_text()
    assert "--bg: #15191f" in css
    assert "--accent: #7b8fa4" in css
    assert "grid-template-columns: 258px minmax(0, 1fr)" in css
    assert "padding: 32px 40px 30px" in css
    assert "font-size: clamp(29px, 3vw, 38px)" in css
    assert "--content-width" not in css
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
    assert "DNS клиента" in response.get_data(as_text=True)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/clients/3/edit",
        data={
            "csrf_token": token,
            "name": "Laptop 2",
            "comment": "new",
            "dns_servers": "9.9.9.9",
            "mtu": "1360",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert recorded[0][0] == 3
    assert recorded[0][1]["dns_servers"] == "9.9.9.9"
