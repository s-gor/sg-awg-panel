from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict
from werkzeug.security import generate_password_hash

import awgpanel.core as core
import awgpanel.db as db
import awgpanel.json_editors as json_editors
import awgpanel.web as web
from awgpanel.errors import AWGPanelError


def prepare_core(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setattr(core, "AWG_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(core, "AWG_CONFIG_PATH", tmp_path / "config" / "awg0.conf")
    monkeypatch.setattr(core, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(core, "_require_root", lambda: None)
    monkeypatch.setattr(core, "awg_service_state", lambda: "inactive")
    monkeypatch.setattr(core, "_command_path", lambda name: None)
    monkeypatch.setattr(core, "_reload_if_active", lambda: None)
    monkeypatch.setattr(core, "_reload_egress_if_available", lambda: None)
    db.init_db()


def configure_with_client(tmp_path: Path, monkeypatch, *, expires_at=None):
    prepare_core(tmp_path, monkeypatch)
    keys = iter([
        ("server-private", "server-public"),
        ("client-private", "client-public"),
        ("second-private", "second-public"),
    ])
    monkeypatch.setattr(core, "_keypair", lambda: next(keys))
    monkeypatch.setattr(core, "_psk", lambda: "shared-key")
    core.configure_awg(endpoint_host="203.0.113.10", external_interface="ens5")
    return core.add_awg_client("Phone", expires_at=expires_at)


def test_expiry_normalization_and_lifecycle_states():
    assert core.normalize_client_expiry(None) is None
    assert core.normalize_client_expiry("") is None
    assert core.normalize_client_expiry("2030-01-02T03:04:05Z") == "2030-01-02 03:04:05"

    now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
    active = {"enabled": 1, "expires_at": "2030-02-01 12:00:00"}
    soon = {"enabled": 1, "expires_at": "2030-01-03 12:00:00"}
    expired = {"enabled": 1, "expires_at": "2029-12-31 12:00:00"}
    disabled = {"enabled": 0, "expires_at": "2030-02-01 12:00:00"}

    assert core.client_lifecycle(active, now=now)["lifecycle_status"] == "active"
    assert core.client_lifecycle(soon, now=now)["lifecycle_status"] == "expiring"
    assert core.client_lifecycle(expired, now=now)["lifecycle_status"] == "expired"
    state = core.client_lifecycle(disabled, now=now)
    assert state["lifecycle_status"] == "disabled"
    assert state["effective_enabled"] is False


def test_alpha17_database_migration_adds_expiry_without_losing_client(tmp_path, monkeypatch):
    path = tmp_path / "panel.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE awg_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1,
            address TEXT NOT NULL UNIQUE,
            private_key TEXT NOT NULL,
            public_key TEXT NOT NULL UNIQUE,
            preshared_key TEXT NOT NULL,
            comment TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO awg_clients
            (name, address, private_key, public_key, preshared_key, comment)
        VALUES ('Existing phone', '10.77.0.2/32', 'private', 'public', 'psk', 'kept');
        """
    )
    con.commit()
    con.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    with db.connect() as migrated:
        columns = {row["name"] for row in migrated.execute("PRAGMA table_info(awg_clients)")}
        row = migrated.execute("SELECT * FROM awg_clients WHERE name='Existing phone'").fetchone()
    assert "expires_at" in columns
    assert row["comment"] == "kept"
    assert row["expires_at"] is None


def test_expired_client_is_removed_from_server_config_and_public_access(tmp_path, monkeypatch):
    expired = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    client = configure_with_client(tmp_path, monkeypatch, expires_at=expired)
    server_config = core.render_awg_server_config()
    assert "client-public" not in server_config
    assert core.list_awg_clients(enabled_only=True) == []
    with pytest.raises(AWGPanelError, match="Срок действия клиента истёк"):
        core.render_awg_client_config(client["id"])
    with pytest.raises(AWGPanelError, match="недействительна или отключена"):
        core.find_client_by_access_token(client["access_token"])


def test_manual_disable_and_expiry_are_independent(tmp_path, monkeypatch):
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    client = configure_with_client(tmp_path, monkeypatch, expires_at=future)
    core.set_awg_client_enabled(client["id"], False)
    core.bulk_update_awg_clients([client["id"]], action="extend_7")
    row = core.find_awg_client(client["id"])
    assert row["enabled"] == 0
    assert core.client_lifecycle(row)["effective_enabled"] is False

    core.set_awg_client_enabled(client["id"], True)
    core.bulk_update_awg_clients([client["id"]], action="clear_expiry")
    row = core.find_awg_client(client["id"])
    assert row["enabled"] == 1
    assert row["expires_at"] is None
    assert core.client_lifecycle(row)["effective_enabled"] is True


def test_bulk_extension_uses_current_future_expiry_as_base(tmp_path, monkeypatch):
    future = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=20)
    client = configure_with_client(tmp_path, monkeypatch, expires_at=future)
    core.bulk_update_awg_clients([client["id"]], action="extend_7")
    row = core.find_awg_client(client["id"])
    assert core.normalize_client_expiry(future + timedelta(days=7)) == row["expires_at"]


def test_expiry_tick_reloads_only_when_effective_server_config_changes(tmp_path, monkeypatch):
    client = configure_with_client(tmp_path, monkeypatch)
    assert "client-public" in core.AWG_CONFIG_PATH.read_text(encoding="utf-8")
    with db.connect() as con:
        con.execute(
            "UPDATE awg_clients SET expires_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"), client["id"]),
        )
    calls: list[str] = []
    monkeypatch.setattr(core, "_reload_if_active", lambda: calls.append("awg"))
    monkeypatch.setattr(core, "_reload_egress_if_available", lambda: calls.append("traffic"))

    result = core.client_expiry_tick()
    assert result["changed"] is True
    assert result["expired"] == 1
    assert calls == ["awg", "traffic"]
    assert "client-public" not in core.AWG_CONFIG_PATH.read_text(encoding="utf-8")

    calls.clear()
    result = core.client_expiry_tick()
    assert result["changed"] is False
    assert calls == []


def test_client_json_v2_roundtrip_includes_expiry(tmp_path, monkeypatch):
    expires = "2031-05-06T07:08:09Z"
    client = configure_with_client(tmp_path, monkeypatch, expires_at=expires)
    document = json.loads(json_editors.client_json_document(client, core.get_awg_settings()))
    assert document["_sgAwgPanel"]["format"] == "client-v4"
    assert document["client"]["expiresAt"] == expires
    parsed = json_editors.parse_client_json_document(
        json.dumps(document, ensure_ascii=False), expected_id=client["id"]
    )
    assert parsed["expires_at"] == expires


def test_period_extension_form_uses_existing_future_date():
    form = MultiDict({"expiration_mode": "period", "duration_days": "7"})
    result = web._expiry_from_form(form, current_expiry="2050-01-01 00:00:00")
    assert core.normalize_client_expiry(result) == "2050-01-08 00:00:00"


def test_calendar_and_maintenance_assets_are_present():
    root = Path(__file__).resolve().parents[1]
    clients = (root / "awgpanel/templates/clients.html").read_text(encoding="utf-8")
    edit = (root / "awgpanel/templates/client_edit.html").read_text(encoding="utf-8")
    timer = (root / "deploy/install-client-maintenance.sh").read_text(encoding="utf-8")
    install = (root / "install-or-upgrade.sh").read_text(encoding="utf-8")
    update = (root / "deploy/update-from-github.sh").read_text(encoding="utf-8")
    uninstall = (root / "uninstall.sh").read_text(encoding="utf-8")
    updater = (root / "deploy/update-from-github.sh").read_text(encoding="utf-8")

    assert 'type="datetime-local"' in clients
    assert 'type="datetime-local"' in edit
    assert "Истекают скоро" in clients
    assert "clients-modern-table" in clients
    assert not (root / "awgpanel/templates/access.html").exists()
    assert "Конфигурация" in clients
    assert "OnUnitActiveSec=60s" in timer
    assert "Persistent=true" in timer
    assert "clients-tick" in timer
    assert "install-client-maintenance.sh" in install
    assert "install-client-maintenance.sh" in update
    assert "sg-awg-clients-maintenance.timer" in uninstall
    assert updater.count("sg-awg-clients-maintenance.service") >= 2
    assert updater.count("sg-awg-clients-maintenance.timer") >= 3


def test_client_pages_render_expiry_controls(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setenv("AWGPANEL_PASSWORD_HASH", generate_password_hash("correct-password"))
    monkeypatch.setenv("AWGPANEL_SECRET_KEY", "test-secret")
    lifecycle = core.client_lifecycle({"enabled": 1, "expires_at": "2050-01-01 00:00:00"})
    row = {
        "id": 3,
        "name": "Laptop",
        "enabled": 1,
        "comment": "work",
        "address": "10.77.0.2/32",
        "public_key": "pub",
        "dns_servers": "",
        "mtu": None,
        "expires_at": "2050-01-01 00:00:00",
        "egress_mode": "awg_gateway",
        "latest_handshake": 0,
        "latest_handshake_text": "—",
        "rx": 0,
        "tx": 0,
        "rx_text": "0 B",
        "tx_text": "0 B",
        "online": False,
        **lifecycle,
    }
    overview = {
        "service_state": "active",
        "installed": True,
        "module_loaded": True,
        "configured": True,
        "clients": [row],
        "active_clients": 0,
        "panel_rss_text": "12 MiB",
        "total_rx_text": "0 B",
        "total_tx_text": "0 B",
        "latest_backup": None,
        "resources": {"memory_percent": 20, "load1": 0, "load5": 0, "load15": 0},
        "settings": {
            "endpoint_host": "203.0.113.10", "listen_port": 585, "interface_name": "awg0",
            "server_network": "10.77.0.0/24", "dns_servers": "1.1.1.1", "external_interface": "ens5",
            "mtu": 1280, "isolate_clients": 1, "jc": 6, "jmin": 64, "jmax": 128,
            "s1": 48, "s2": 48, "s3": 32, "s4": 16,
            "h1": "", "h2": "", "h3": "", "h4": "", "i1": "", "i2": "", "i3": "", "i4": "", "i5": "",
        },
        "public_ipv4_detected": "", "external_interface_detected": "ens5",
        "config_path": "/tmp/awg0.conf",
    }
    monkeypatch.setattr(web, "get_awg_overview", lambda: overview)
    monkeypatch.setattr(web, "get_awg_settings", lambda: overview["settings"])
    monkeypatch.setattr(web, "find_awg_client", lambda client_id: row)
    monkeypatch.setattr(web, "list_outbounds", lambda: [])
    monkeypatch.setattr(web, "list_backups", lambda limit=10: [])
    monkeypatch.setattr(web, "check_for_updates", lambda force=False: {"current":"v0","latest":"v0","available":False,"checked_at":"now","error":""})
    monkeypatch.setattr(web, "get_update_status", lambda: {"state":"idle","log":""})
    monkeypatch.setattr(web, "validate_web_session", lambda token, touch=True: {"id": 1} if token == "session" else None)
    app = web.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["session_token"] = "session"
        session["csrf_token"] = "token"

    response = client.get("/clients")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert 'type="datetime-local"' in text
    assert "Истекают скоро" in text
    assert "Добавить клиента" in text
    assert "JSON клиента" in text

    response = client.get("/clients/3/edit")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Продлить на период" in text
    assert "data-current-utc=\"2050-01-01T00:00:00Z\"" in text


def test_beta2_client_json_uses_automatic_dns_without_per_client_values(tmp_path, monkeypatch):
    client = configure_with_client(tmp_path, monkeypatch)
    document = json.loads(json_editors.client_json_document(client, core.get_awg_settings()))
    assert document["_sgAwgPanel"]["format"] == "client-v4"
    assert document["client"]["dnsMode"] == "automatic"
    assert "dnsServers" not in document["client"]
    assert "inheritServerDns" not in document["client"]
    parsed = json_editors.parse_client_json_document(
        json.dumps(document, ensure_ascii=False), expected_id=client["id"]
    )
    assert parsed["dns_servers"] == ""
