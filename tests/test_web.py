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


def overview():
    return {
        "service_state": "inactive",
        "installed": True,
        "module_loaded": True,
        "configured": False,
        "clients": [],
        "panel_rss_text": "12.0 MiB",
        "resources": {"memory_percent": 20, "load1": 0, "load5": 0, "load15": 0},
        "settings": {
            "endpoint_host": "", "listen_port": 585,
            "server_network": "10.77.0.0/24", "dns_servers": "1.1.1.1, 1.0.0.1",
            "external_interface": "", "mtu": 1280,
            "jc": 6, "jmin": 64, "jmax": 128,
            "s1": 48, "s2": 48, "s3": 32, "s4": 16,
            "h1": "", "h2": "", "h3": "", "h4": "",
            "i1": "", "i2": "", "i3": "", "i4": "", "i5": "",
        },
        "public_ipv4_detected": "203.0.113.10",
        "external_interface_detected": "ens5",
        "config_path": "/etc/amnezia/amneziawg/awg0.conf",
    }


def diagnostics_data():
    return {
        "service_state": "inactive", "panel_state": "active", "panel_pid": 1,
        "panel_rss_text": "1 MiB", "listen_port": 585,
        "udp": {"listening": False, "lines": [], "error": ""},
        "interface_present": False, "module_loaded": True, "installed": True,
        "public_ipv4": "203.0.113.10", "external_interface": "ens5",
        "config_path": "/tmp/awg0.conf",
        "resources": {"memory_percent": 10, "load1": 0, "load5": 0, "load15": 0},
        "backups": [], "server_logs": "empty", "panel_logs": "empty",
    }


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setenv("AWGPANEL_PASSWORD_HASH", generate_password_hash("correct-password"))
    monkeypatch.setenv("AWGPANEL_SECRET_KEY", "test-secret")
    monkeypatch.setattr(web, "get_awg_overview", overview)
    monkeypatch.setattr(web, "get_awg_diagnostics", diagnostics_data)
    app = web.create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_health_login_dashboard_and_diagnostics(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    response = client.get("/health")
    assert response.status_code == 200
    assert response.data == b"ok\n"

    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    login(client)
    response = client.get("/")
    assert response.status_code == 200
    assert "Сохранить и запустить" in response.get_data(as_text=True)
    assert "Память панели" in response.get_data(as_text=True)

    response = client.get("/diagnostics")
    assert response.status_code == 200
    assert "Автоматические резервные копии" in response.get_data(as_text=True)


def test_one_click_save_route(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(web, "configure_and_start_awg", lambda **values: (calls.append(values), "active"))
    login(client)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post(
        "/settings",
        data={
            "csrf_token": token,
            "endpoint_host": "203.0.113.10",
            "listen_port": "585",
            "server_network": "10.77.0.0/24",
            "dns_servers": "1.1.1.1, 1.0.0.1",
            "external_interface": "ens5",
            "mtu": "1280",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert calls and calls[0]["endpoint_host"] == "203.0.113.10"
