from __future__ import annotations

from datetime import datetime, timezone

import awgpanel.cascade as cascade
import awgpanel.db as db


CASCADE_CONFIG = """
[Interface]
Address = 10.254.71.2/32
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=

[Peer]
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
PresharedKey = CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = exit.example.com:585
PersistentKeepalive = 25
"""


def prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()
    with db.connect() as con:
        con.execute(
            """
            INSERT INTO awg_clients(
                id, name, address, private_key, public_key, preshared_key, system_role
            ) VALUES(41, 'sg-cascade-entry', '10.254.71.2/32', 'private', 'public', 'psk', ?)
            """,
            (cascade.CASCADE_SYSTEM_ROLE,),
        )


def test_cascade_link_is_persisted_until_expiry(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cascade,
        "create_exit_service_client",
        lambda **kwargs: {"client": {"id": 41, "name": "sg-cascade-entry"}, "config_text": CASCADE_CONFIG},
    )
    monkeypatch.setattr(
        cascade,
        "get_panel_settings",
        lambda: {"instance_name": "Virginia Exit"},
    )
    monkeypatch.setattr(cascade, "_local_country_code", lambda: "US")
    monkeypatch.setattr(
        cascade,
        "get_exit_service_client",
        lambda: {"id": 41, "name": "sg-cascade-entry", "config_text": CASCADE_CONFIG},
    )

    created = cascade.create_exit_enrollment(ttl_minutes=30)
    restored = cascade.get_active_exit_enrollment()

    assert restored is not None
    assert restored["link"] == created["link"]
    assert restored["endpoint"] == "exit.example.com:585"
    assert restored["exit_name"] == "Virginia Exit"
    assert datetime.fromisoformat(restored["expires_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)

    with db.connect() as con:
        row = con.execute(
            "SELECT exit_enrollment_link, exit_enrollment_client_id FROM cascade_settings WHERE id=1"
        ).fetchone()
    assert row["exit_enrollment_link"] == created["link"]
    assert int(row["exit_enrollment_client_id"]) == 41


def test_removing_cascade_service_clears_saved_link(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    with db.connect() as con:
        con.execute(
            "UPDATE cascade_settings SET exit_enrollment_link='secret-link', "
            "exit_enrollment_expires_at='2030-01-01T00:00:00Z', exit_enrollment_client_id=41 WHERE id=1"
        )
    monkeypatch.setattr(cascade, "delete_awg_service_clients", lambda role: [{"id": 41}])

    removed = cascade.remove_exit_service_client()
    assert removed == [{"id": 41}]
    with db.connect() as con:
        row = con.execute(
            "SELECT exit_enrollment_link, exit_enrollment_expires_at, exit_enrollment_client_id "
            "FROM cascade_settings WHERE id=1"
        ).fetchone()
    assert row["exit_enrollment_link"] == ""
    assert row["exit_enrollment_expires_at"] == ""
    assert row["exit_enrollment_client_id"] is None


def test_cascade_page_keeps_generated_link_visible_after_refresh(tmp_path, monkeypatch):
    import awgpanel.web as web
    from werkzeug.security import generate_password_hash

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setenv("AWGPANEL_PASSWORD_HASH", generate_password_hash("correct-password"))
    monkeypatch.setenv("AWGPANEL_SECRET_KEY", "rc5-build-fix-2-secret")
    monkeypatch.setattr(web, "reconcile_all_cascades", lambda: [])
    monkeypatch.setattr(web, "cascade_servers", lambda: [])
    monkeypatch.setattr(web, "list_cascade_links", lambda **kwargs: [])
    monkeypatch.setattr(web, "get_cascade_settings", lambda: {"enabled": 0})
    monkeypatch.setattr(web, "get_exit_service_client", lambda: {"id": 41, "name": "sg-cascade-entry"})
    monkeypatch.setattr(
        web,
        "get_active_exit_enrollment",
        lambda: {
            "link": "sg-awg-cascade://v1/persisted-after-refresh",
            "expires_at": "2030-01-01T00:30:00Z",
            "exit_country_code": "US",
            "exit_name": "Virginia Exit",
            "endpoint": "exit.example.com:585",
            "client": {"id": 41, "name": "sg-cascade-entry"},
        },
    )
    app = web.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "token"
    response = client.post(
        "/login",
        data={"password": "correct-password", "csrf_token": "token"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    refreshed = client.get("/cascade?mode=external")
    assert refreshed.status_code == 200
    assert "sg-awg-cascade://v1/persisted-after-refresh" in refreshed.get_data(as_text=True)
