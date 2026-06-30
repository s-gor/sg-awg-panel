from __future__ import annotations

from awgpanel import db


def test_beta4_database_is_migrated_to_beta8_without_losing_core_state(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()
    with db.connect() as con:
        con.execute("UPDATE panel_settings SET public_host='panel.example.com', public_port=62443 WHERE id=1")
        con.execute("UPDATE awg_settings SET endpoint_host='203.0.113.10', configured=1 WHERE id=1")
        # Recreate the Beta 4 table to exercise the one-time migration.
        con.execute("""
            CREATE TABLE server_protection_settings (
                id INTEGER PRIMARY KEY, block_smtp INTEGER, block_private_networks INTEGER,
                isolate_clients INTEGER
            )
        """)
        con.execute("INSERT INTO server_protection_settings VALUES (1,1,1,1)")
    db.init_db()
    with db.connect() as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='server_protection_settings'").fetchone() is None
        controls=dict(con.execute("SELECT * FROM traffic_controls_settings WHERE id=1").fetchone())
        panel=dict(con.execute("SELECT * FROM panel_settings WHERE id=1").fetchone())
        server=dict(con.execute("SELECT * FROM awg_settings WHERE id=1").fetchone())
    assert controls["allow_smtp25"] == 0
    assert controls["allow_private_networks"] == 0
    assert controls["allow_client_communication"] == 0
    assert panel["public_host"] == "panel.example.com"
    assert panel["public_port"] == 62443
    assert server["endpoint_host"] == "203.0.113.10"
