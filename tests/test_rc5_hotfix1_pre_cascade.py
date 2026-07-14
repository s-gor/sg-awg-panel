from __future__ import annotations

import json
from pathlib import Path

import awgpanel.core as core
import awgpanel.db as db
import awgpanel.web as web
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]


def _make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setenv("AWGPANEL_PASSWORD_HASH", generate_password_hash("correct-password"))
    monkeypatch.setenv("AWGPANEL_SECRET_KEY", "hotfix-test-secret")
    monkeypatch.setattr(web, "get_awg_settings", lambda: {
        "configured": 1, "endpoint_host": "203.0.113.10", "listen_port": 585,
        "interface_name": "awg0", "server_network": "10.77.0.0/24",
        "dns_servers": "1.1.1.1, 1.0.0.1", "external_interface": "ens5",
        "mtu": 1280, "isolate_clients": 1, "jc": 6, "jmin": 64, "jmax": 128,
        "s1": 48, "s2": 48, "s3": 32, "s4": 16,
        "h1": "", "h2": "", "h3": "", "h4": "",
        "i1": "", "i2": "", "i3": "", "i4": "", "i5": "",
    })
    monkeypatch.setattr(web, "list_awg_clients", lambda *args, **kwargs: [])
    app = web.create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_hotfix_schema_and_default_instance_name(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()
    with db.connect() as con:
        row = con.execute("SELECT instance_name FROM panel_settings WHERE id=1").fetchone()
    assert row["instance_name"] == "SG-AWG-Panel"


def test_config_contains_stable_profile_name_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()
    with db.connect() as con:
        con.execute("UPDATE panel_settings SET instance_name='Frankfurt Entry'")
        con.execute(
            "UPDATE awg_settings SET configured=1, endpoint_host='203.0.113.10', "
            "private_key='server-private', public_key='server-public', "
            "h1='1', h2='2', h3='3', h4='4' WHERE id=1"
        )
        con.execute(
            "INSERT INTO awg_clients "
            "(name,address,private_key,public_key,preshared_key,access_token) "
            "VALUES ('Sergey Laptop','10.77.0.2/32','client-private','client-public','psk','token')"
        )
    text = core.render_awg_client_config(1)
    assert text.startswith("# Name = Frankfurt Entry/Sergey Laptop\n")
    assert "# Client = Sergey Laptop" in text
    assert "# Source = SG-AWG-Panel" in text


def test_public_subscription_qr_and_managed_profile(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    row = {
        "id": 7, "name": "Phone", "updated_at": "2026-07-11 09:00:00",
        "access_token": "good", "access_enabled": 1,
    }
    monkeypatch.setattr(web, "find_client_by_access_token", lambda token: row if token == "good" else None)
    monkeypatch.setattr(web, "render_awg_client_config", lambda client_id: "# Name = Phone\n[Interface]\nAddress = 10.77.0.2/32\n")
    monkeypatch.setattr(web, "record_client_access", lambda client_id: None)

    subscription = client.get("/s/good")
    assert subscription.status_code == 200
    assert subscription.mimetype == "text/plain"
    assert subscription.headers["X-SG-Profile-Name"] == "Phone"
    assert subscription.headers["X-SG-Profile-Type"] == "amneziawg"
    assert subscription.headers["Cache-Control"] == "no-store"

    managed = client.get("/s/good/managed.json")
    payload = json.loads(managed.get_data(as_text=True))
    assert payload["schema"] == "sg-client-managed-profile-v1"
    assert payload["profile"]["name"] == "Phone"
    assert payload["profile"]["protocol"] == "amneziawg"

    qr = client.get("/a/good/qr.svg")
    assert qr.status_code == 200
    assert qr.mimetype == "image/svg+xml"
    assert b"<svg" in qr.data


def test_navigation_header_footer_and_layout_fixes_are_present():
    base = (ROOT / "awgpanel/templates/base.html").read_text(encoding="utf-8")
    css = (ROOT / "awgpanel/static/app.css").read_text(encoding="utf-8")
    assert base.index("<b>System</b>") < base.index("<b>Cluster</b>") < base.index("<b>Cascade</b>")
    assert base.index("<b>Cascade</b>") < base.index("<b>Clients</b>") < base.index("<b>AWG Server</b>")
    assert "layout_identity_label" in base
    assert "UI {{ layout_ui_build }}" in base
    assert "overflow-x: hidden" in css
    assert ".client-import-grid-three" in css
    assert "grid-template-columns: 1fr !important" in css


def test_installers_collect_and_persist_instance_name():
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    common = (ROOT / "deploy/install-common.sh").read_text(encoding="utf-8")
    upgrade = (ROOT / "install-or-upgrade.sh").read_text(encoding="utf-8")
    assert "Имя этого сервера" in install
    assert "prompt_instance_name" in common
    assert "AWGPANEL_INSTANCE_NAME" in upgrade
    assert "INSTANCE_NAME_SYNC" in upgrade


def test_cascade_implementation_was_not_replaced_by_hotfix():
    cascade = (ROOT / "awgpanel/cascade.py").read_text(encoding="utf-8")
    notes = (ROOT / "RELEASE-NOTES-RC5-HOTFIX1.md")
    assert "def configure_cascade" in cascade
    assert notes.exists()
    assert "Логика Cascade не изменялась" in notes.read_text(encoding="utf-8")
