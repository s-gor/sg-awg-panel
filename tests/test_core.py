from pathlib import Path

import awgpanel.core as core
import awgpanel.db as db


def prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setattr(core, "AWG_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(core, "AWG_CONFIG_PATH", tmp_path / "config" / "awg0.conf")
    monkeypatch.setattr(core, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(core, "_require_root", lambda: None)
    monkeypatch.setattr(core, "awg_service_state", lambda: "inactive")
    db.init_db()


def test_database_defaults(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    settings = core.get_awg_settings()
    assert settings["interface_name"] == "awg0"
    assert settings["listen_port"] == 585
    assert core.list_awg_clients() == []


def test_settings_validation_generates_config_and_backup(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(core, "_keypair", lambda: ("server-private", "server-public"))
    monkeypatch.setattr(core, "_command_path", lambda name: None)
    settings = core.configure_awg(
        endpoint_host="203.0.113.10",
        listen_port=585,
        server_network="10.77.0.0/24",
        dns_servers="1.1.1.1, 1.0.0.1",
        mtu=1280,
        external_interface="ens5",
    )
    assert settings["configured"] == 1
    text = core.AWG_CONFIG_PATH.read_text()
    assert "ListenPort = 585" in text
    assert "PrivateKey = server-private" in text
    assert "PostUp = iptables" in text
    assert core.list_backups()


def test_valid_temporary_config_name(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(core, "_keypair", lambda: ("server-private", "server-public"))
    calls = []

    def fake_command(name):
        return f"/usr/bin/{name}" if name == "awg-quick" else None

    def fake_run(args, **kwargs):
        calls.append(args)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(core, "_command_path", fake_command)
    monkeypatch.setattr(core, "_run", fake_run)
    core.configure_awg(endpoint_host="203.0.113.10", external_interface="ens5")
    strip_call = next(args for args in calls if len(args) > 1 and args[1] == "strip")
    assert strip_call[-1].endswith("awgtest0.conf")
    assert not strip_call[-1].endswith(".conf.tmp")


def test_add_regenerate_and_render_client(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    keys = iter([
        ("server-private", "server-public"),
        ("client-private", "client-public"),
        ("new-private", "new-public"),
    ])
    psks = iter(["shared-key", "new-shared-key"])
    monkeypatch.setattr(core, "_keypair", lambda: next(keys))
    monkeypatch.setattr(core, "_psk", lambda: next(psks))
    monkeypatch.setattr(core, "_command_path", lambda name: None)
    monkeypatch.setattr(core, "_reload_if_active", lambda: None)
    core.configure_awg(
        endpoint_host="vpn.example.com",
        listen_port=585,
        server_network="10.77.0.0/24",
        dns_servers="1.1.1.1",
        mtu=1280,
        external_interface="ens5",
    )
    client = core.add_awg_client("Phone")
    assert client["address"] == "10.77.0.2/32"
    text = core.render_awg_client_config(client["id"])
    assert "PrivateKey = client-private" in text
    assert "PublicKey = server-public" in text
    assert "Endpoint = vpn.example.com:585" in text
    assert "AllowedIPs = 0.0.0.0/0" in text

    regenerated = core.regenerate_awg_client(client["id"])
    assert regenerated["private_key"] == "new-private"
    assert regenerated["public_key"] == "new-public"
    assert regenerated["address"] == "10.77.0.2/32"


def test_configure_and_start_uses_detected_values(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(core, "_keypair", lambda: ("private", "public"))
    monkeypatch.setattr(core, "_command_path", lambda name: None)
    monkeypatch.setattr(core, "detect_public_ipv4", lambda **kwargs: "198.51.100.25")
    monkeypatch.setattr(core, "detect_external_interface", lambda: "ens5")
    monkeypatch.setattr(core, "_service_action", lambda action: "active")
    settings, state = core.configure_and_start_awg(endpoint_host="", external_interface="")
    assert settings["endpoint_host"] == "198.51.100.25"
    assert settings["external_interface"] == "ens5"
    assert state == "active"


def test_invalid_overlapping_headers_rolls_back(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(core, "_keypair", lambda: ("private", "public"))
    try:
        core.configure_awg(
            endpoint_host="203.0.113.10",
            external_interface="ens5",
            h1="100-200",
            h2="150-250",
            h3="300-400",
            h4="500-600",
        )
    except ValueError as exc:
        assert "не должны пересекаться" in str(exc)
    else:
        raise AssertionError("overlapping ranges were accepted")
    assert core.get_awg_settings()["configured"] == 0


def test_client_access_and_traffic_fields_are_created(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    keys = iter([("server-private", "server-public"), ("client-private", "client-public")])
    monkeypatch.setattr(core, "_keypair", lambda: next(keys))
    monkeypatch.setattr(core, "_psk", lambda: "shared-key")
    monkeypatch.setattr(core, "_command_path", lambda name: None)
    monkeypatch.setattr(core, "_reload_if_active", lambda: None)
    core.configure_awg(endpoint_host="203.0.113.10", external_interface="ens5")
    client = core.add_awg_client("Laptop")
    assert client["allowed_ips"] == "0.0.0.0/0"
    assert client["access_token"]
    assert client["access_enabled"] == 1

    updated = core.update_awg_client_traffic(client["id"], "10.0.0.0/8, 192.168.1.0/24")
    assert updated["allowed_ips"] == "10.0.0.0/8, 192.168.1.0/24"
    text = core.render_awg_client_config(client["id"])
    assert "AllowedIPs = 10.0.0.0/8, 192.168.1.0/24" in text


def test_client_isolation_rule_is_rendered(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(core, "_keypair", lambda: ("server-private", "server-public"))
    monkeypatch.setattr(core, "_command_path", lambda name: None)
    core.configure_awg(endpoint_host="203.0.113.10", external_interface="ens5", isolate_clients=1)
    text = core.render_awg_server_config()
    assert "-I FORWARD 1 -i awg0 -o awg0 -j DROP" in text

    monkeypatch.setattr(core, "_reload_if_active", lambda: None)
    core.update_traffic_settings(isolate_clients=False)
    text = core.render_awg_server_config()
    assert "-i awg0 -o awg0 -j DROP" not in text


def test_access_token_lookup_and_counter(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    keys = iter([("server-private", "server-public"), ("client-private", "client-public")])
    monkeypatch.setattr(core, "_keypair", lambda: next(keys))
    monkeypatch.setattr(core, "_psk", lambda: "shared-key")
    monkeypatch.setattr(core, "_command_path", lambda name: None)
    monkeypatch.setattr(core, "_reload_if_active", lambda: None)
    core.configure_awg(endpoint_host="203.0.113.10", external_interface="ens5")
    client = core.add_awg_client("Phone")
    found = core.find_client_by_access_token(client["access_token"])
    assert found["id"] == client["id"]
    core.record_client_access(client["id"])
    assert core.find_awg_client(client["id"])["access_downloads"] == 1


def test_dns_update_changes_gateway_upstream_but_client_uses_gateway(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    keys = iter([("server-private", "server-public"), ("client-private", "client-public")])
    monkeypatch.setattr(core, "_keypair", lambda: next(keys))
    monkeypatch.setattr(core, "_psk", lambda: "shared-key")
    monkeypatch.setattr(core, "_command_path", lambda name: None)
    monkeypatch.setattr(core, "_reload_if_active", lambda: None)
    core.configure_awg(endpoint_host="203.0.113.10", external_interface="ens5")
    client = core.add_awg_client("Tablet")
    settings = core.update_dns_servers("9.9.9.9, 149.112.112.112")
    assert settings["dns_servers"] == "9.9.9.9, 149.112.112.112"
    assert "DNS = 10.77.0.1" in core.render_awg_client_config(client["id"])


def test_alpha3_database_migrates_without_losing_client(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "panel.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE awg_settings (
            id INTEGER PRIMARY KEY, configured INTEGER NOT NULL DEFAULT 0,
            interface_name TEXT NOT NULL DEFAULT 'awg0', endpoint_host TEXT NOT NULL DEFAULT '',
            listen_port INTEGER NOT NULL DEFAULT 585, server_network TEXT NOT NULL DEFAULT '10.77.0.0/24',
            dns_servers TEXT NOT NULL DEFAULT '1.1.1.1, 1.0.0.1', mtu INTEGER NOT NULL DEFAULT 1280,
            external_interface TEXT NOT NULL DEFAULT '', private_key TEXT NOT NULL DEFAULT '',
            public_key TEXT NOT NULL DEFAULT '', jc INTEGER NOT NULL DEFAULT 6,
            jmin INTEGER NOT NULL DEFAULT 64, jmax INTEGER NOT NULL DEFAULT 128,
            s1 INTEGER NOT NULL DEFAULT 48, s2 INTEGER NOT NULL DEFAULT 48,
            s3 INTEGER NOT NULL DEFAULT 32, s4 INTEGER NOT NULL DEFAULT 16,
            h1 TEXT NOT NULL DEFAULT '', h2 TEXT NOT NULL DEFAULT '',
            h3 TEXT NOT NULL DEFAULT '', h4 TEXT NOT NULL DEFAULT '',
            i1 TEXT NOT NULL DEFAULT '', i2 TEXT NOT NULL DEFAULT '', i3 TEXT NOT NULL DEFAULT '',
            i4 TEXT NOT NULL DEFAULT '', i5 TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE awg_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1, address TEXT NOT NULL UNIQUE,
            private_key TEXT NOT NULL, public_key TEXT NOT NULL UNIQUE,
            preshared_key TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO awg_settings(id) VALUES(1);
        INSERT INTO awg_clients(name,address,private_key,public_key,preshared_key)
        VALUES('Old client','10.77.0.2/32','private','public','psk');
        """
    )
    con.commit()
    con.close()

    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    with db.connect() as migrated:
        client = migrated.execute("SELECT * FROM awg_clients").fetchone()
        settings = migrated.execute("SELECT * FROM awg_settings WHERE id=1").fetchone()
    assert client["name"] == "Old client"
    assert client["allowed_ips"] == "0.0.0.0/0"
    assert client["access_token"]
    assert settings["isolate_clients"] == 1


def test_diagnostic_report_redacts_keys(monkeypatch):
    monkeypatch.setattr(core, "get_awg_diagnostics", lambda: {
        "service_state": "active", "awg_enabled": True, "awg_uptime": "1 ч",
        "panel_state": "active", "panel_enabled": True, "panel_uptime": "2 ч",
        "panel_rss_text": "44.0 MiB", "module_loaded": True, "installed": True,
        "interface_present": True, "listen_port": 585,
        "udp": {"listening": True}, "ip_forward": True, "nat_rule": True,
        "boot_ready": True, "public_ipv4": "203.0.113.10",
        "external_interface": "ens5", "config_exists": True,
        "config_path": "/etc/amnezia/amneziawg/awg0.conf", "backups": [],
        "resources": {"memory_percent": 40, "load1": 0.1, "load5": 0.2, "load15": 0.3},
        "server_logs": "PrivateKey = secret-value",
        "panel_logs": "GET /a/supersecrettoken123456789 HTTP/1.1",
    })
    report = core.build_diagnostic_report()
    assert "secret-value" not in report
    assert "supersecrettoken" not in report
    assert "[REDACTED]" in report


def test_client_dns_is_automatic_and_mtu_can_override_server(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    keys = iter([("server-private", "server-public"), ("client-private", "client-public")])
    monkeypatch.setattr(core, "_keypair", lambda: next(keys))
    monkeypatch.setattr(core, "_psk", lambda: "shared-key")
    monkeypatch.setattr(core, "_command_path", lambda name: None)
    monkeypatch.setattr(core, "_reload_if_active", lambda: None)
    core.configure_awg(
        endpoint_host="203.0.113.10",
        external_interface="ens5",
        dns_servers="1.1.1.1",
        mtu=1280,
    )
    client = core.add_awg_client("Laptop")
    updated = core.update_awg_client_settings(
        client["id"],
        name="Laptop",
        comment="Work device",
        dns_servers="9.9.9.9, 149.112.112.112",
        mtu="1360",
    )
    assert updated["comment"] == "Work device"
    assert updated["dns_servers"] == ""
    assert updated["mtu"] == 1360
    text = core.render_awg_client_config(client["id"])
    assert "DNS = 10.77.0.1" in text
    assert "MTU = 1360" in text


def test_client_empty_dns_and_mtu_inherit_server(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    keys = iter([("server-private", "server-public"), ("client-private", "client-public")])
    monkeypatch.setattr(core, "_keypair", lambda: next(keys))
    monkeypatch.setattr(core, "_psk", lambda: "shared-key")
    monkeypatch.setattr(core, "_command_path", lambda name: None)
    monkeypatch.setattr(core, "_reload_if_active", lambda: None)
    core.configure_awg(
        endpoint_host="203.0.113.10",
        external_interface="ens5",
        dns_servers="1.0.0.1",
        mtu=1280,
    )
    client = core.add_awg_client("Phone")
    core.update_awg_client_settings(
        client["id"], name="Phone", comment="", dns_servers="", mtu=""
    )
    text = core.render_awg_client_config(client["id"])
    assert "DNS = 10.77.0.1" in text
    assert "MTU = 1280" in text


def test_panel_security_defaults_and_backend_loopback(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    panel = core.get_panel_settings()
    assert panel["backend_address"] == "127.0.0.1"
    assert panel["backend_port"] == 18080
    assert panel["public_port"] == 62443
    assert panel["manage_placeholder"] == 1
    assert panel["backup_schedule"] == "daily"


def test_ip_allowlist_validation_prevents_lockout(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    assert core.normalize_ip_allowlist("203.0.113.10, 198.51.100.0/24") == (
        "203.0.113.10/32, 198.51.100.0/24"
    )
    updated = core.update_ip_allowlist(
        "203.0.113.10, 198.51.100.0/24", current_ip="203.0.113.10"
    )
    assert core.ip_is_allowed("198.51.100.20", updated["ip_allowlist"])
    try:
        core.update_ip_allowlist("192.0.2.0/24", current_ip="203.0.113.10")
    except ValueError as exc:
        assert "не входит" in str(exc)
    else:
        raise AssertionError("allowlist accepted a configuration that locks out current IP")


def test_server_side_sessions_can_be_revoked(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    token = "session-token"
    core.create_web_session(token, ip_address="203.0.113.10", user_agent="pytest")
    assert core.validate_web_session(token, touch=False) is not None
    sessions = core.list_web_sessions(current_token=token)
    assert len(sessions) == 1 and sessions[0]["current"] is True
    core.revoke_web_session(sessions[0]["token_hash"])
    assert core.validate_web_session(token, touch=False) is None


def test_update_version_ordering():
    assert core._version_key("v0.1.0-alpha8") > core._version_key("v0.1.0-alpha6")
    assert core._version_key("v0.1.0-beta2") > core._version_key("v0.1.0-alpha99")
    assert core._version_key("v0.1.0") > core._version_key("v0.1.0-rc9")


def test_panel_access_uses_separate_port_and_no_email(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    project = tmp_path / "project"
    script = project / "deploy" / "configure-panel-access.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(core, "PANEL_PROJECT_DIR", project)
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(core, "_run", fake_run)
    panel = core.configure_panel_access(
        scheme="https",
        public_host="awg.example.com",
        public_port="62443",
        manage_placeholder=False,
    )
    assert panel["public_port"] == 62443
    assert panel["https_email"] == ""
    assert panel["manage_placeholder"] == 0
    command = calls[0]
    assert "--manage-placeholder" in command
    assert "--email" not in command


def test_panel_access_rejects_443(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    try:
        core.configure_panel_access(
            scheme="https",
            public_host="awg.example.com",
            public_port="443",
        )
    except ValueError as exc:
        assert "динамическом диапазоне" in str(exc)
    else:
        raise AssertionError("TCP 443 was accepted as the panel port")


def test_alpha7_panel_settings_migrate_placeholder_flag(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "panel.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE panel_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            public_scheme TEXT NOT NULL DEFAULT 'http',
            public_host TEXT NOT NULL DEFAULT '',
            public_port INTEGER NOT NULL DEFAULT 8080,
            backend_address TEXT NOT NULL DEFAULT '127.0.0.1',
            backend_port INTEGER NOT NULL DEFAULT 18080,
            https_email TEXT NOT NULL DEFAULT '',
            https_enabled INTEGER NOT NULL DEFAULT 0,
            ip_allowlist TEXT NOT NULL DEFAULT '',
            backup_schedule TEXT NOT NULL DEFAULT 'daily',
            backup_keep INTEGER NOT NULL DEFAULT 20,
            update_channel TEXT NOT NULL DEFAULT 'prerelease',
            latest_version TEXT NOT NULL DEFAULT '',
            latest_checked_at TEXT,
            latest_error TEXT NOT NULL DEFAULT '',
            auth_epoch INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO panel_settings(id, public_scheme, public_port)
        VALUES(1, 'http', 8080);
        """
    )
    con.commit()
    con.close()

    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    with db.connect() as migrated:
        row = migrated.execute("SELECT * FROM panel_settings WHERE id=1").fetchone()
    assert row["manage_placeholder"] == 1



def test_access_settings_defaults_and_validation(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    panel = core.get_panel_settings()
    assert panel["access_enabled"] == 1
    assert panel["access_profile_title"] == "SG-AWG"

    updated = core.configure_access_links(enabled=False, profile_title="Family VPN")
    assert updated["access_enabled"] == 0
    assert updated["access_profile_title"] == "Family VPN"

    try:
        core.configure_access_links(enabled=True, profile_title="")
    except ValueError as exc:
        assert "название профиля" in str(exc).lower()
    else:
        raise AssertionError("empty access profile title was accepted")


def test_old_panel_settings_migrate_access_columns(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "panel.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE panel_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            public_scheme TEXT NOT NULL DEFAULT 'http',
            public_host TEXT NOT NULL DEFAULT '',
            public_port INTEGER NOT NULL DEFAULT 8080,
            backend_address TEXT NOT NULL DEFAULT '127.0.0.1',
            backend_port INTEGER NOT NULL DEFAULT 18080,
            https_email TEXT NOT NULL DEFAULT '',
            https_enabled INTEGER NOT NULL DEFAULT 0,
            manage_placeholder INTEGER NOT NULL DEFAULT 1,
            ip_allowlist TEXT NOT NULL DEFAULT '',
            backup_schedule TEXT NOT NULL DEFAULT 'daily',
            backup_keep INTEGER NOT NULL DEFAULT 20,
            update_channel TEXT NOT NULL DEFAULT 'prerelease',
            latest_version TEXT NOT NULL DEFAULT '',
            latest_checked_at TEXT,
            latest_error TEXT NOT NULL DEFAULT '',
            auth_epoch INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO panel_settings(id, public_scheme, public_port)
        VALUES(1, 'http', 8080);
        """
    )
    con.commit()
    con.close()

    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    with db.connect() as migrated:
        row = migrated.execute("SELECT * FROM panel_settings WHERE id=1").fetchone()
    assert row["access_enabled"] == 1
    assert row["access_profile_title"] == "SG-AWG"


def test_restore_stops_new_interface_before_deleting_new_config(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    backup = core.BACKUP_DIR / "rollback"
    backup.mkdir(parents=True)
    (backup / "metadata.json").write_text(
        '{"config_existed": false, "service_state": "inactive"}',
        encoding="utf-8",
    )
    core.AWG_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    core.AWG_CONFIG_PATH.write_text("current config\n", encoding="utf-8")

    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["systemctl", "stop"]:
            assert core.AWG_CONFIG_PATH.exists(), "config was deleted before awg-quick down"
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(core, "_run", fake_run)
    monkeypatch.setattr(core, "_reload_egress_if_available", lambda: None)
    core._restore_backup(backup)

    assert calls[0][:2] == ["systemctl", "stop"]
    assert not core.AWG_CONFIG_PATH.exists()


def test_beta2_ensure_default_server_keeps_existing_configuration(monkeypatch):
    monkeypatch.setattr(core, "_require_root", lambda: None)
    monkeypatch.setattr(core, "get_awg_settings", lambda: {"configured": 1, "listen_port": 585})
    monkeypatch.setattr(core, "awg_service_state", lambda: "active")
    result = core.ensure_default_awg_server()
    assert result == {"changed": False, "configured": True, "service_state": "active", "listen_port": 585}


def test_beta2_ensure_default_server_configures_first_free_port(monkeypatch):
    monkeypatch.setattr(core, "_require_root", lambda: None)
    monkeypatch.setattr(core, "get_awg_settings", lambda: {"configured": 0, "listen_port": 585})
    def check_port(port, current_port=None):
        if port == 585:
            raise ValueError("busy")
    monkeypatch.setattr(core, "ensure_udp_port_available", check_port)
    captured = {}
    def configure(**values):
        captured.update(values)
        return ({"listen_port": values["listen_port"], "endpoint_host": "203.0.113.10", "server_network": values["server_network"]}, "active")
    monkeypatch.setattr(core, "configure_and_start_awg", configure)
    result = core.ensure_default_awg_server()
    assert captured["listen_port"] == 51820
    assert captured["server_network"] == "10.77.0.0/24"
    assert result["changed"] is True
    assert result["service_state"] == "active"

def test_beta3_server_start_survives_secondary_traffic_failure(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(core, "_keypair", lambda: ("private", "public"))
    monkeypatch.setattr(core, "detect_public_ipv4", lambda **kwargs: "198.51.100.25")
    monkeypatch.setattr(core, "detect_external_interface", lambda: "ens5")
    monkeypatch.setattr(core, "_service_action", lambda action: "active")
    monkeypatch.setattr(core, "_command_path", lambda name: f"/usr/bin/{name}" if name in {"nft", "ip"} else None)

    def broken_traffic():
        raise core.AWGPanelError("dnsmasq rejected protection list")

    import awgpanel.egress as egress
    monkeypatch.setattr(egress, "apply_egress_runtime", broken_traffic)
    settings, state = core.configure_and_start_awg(endpoint_host="", external_interface="")
    assert state == "active"
    assert settings["configured"] == 1
    assert core.get_awg_settings()["configured"] == 1


def test_beta3_backup_is_verified_and_reports_size(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    backup = core.create_manual_backup()
    rows = core.list_backups()
    assert backup.exists()
    assert rows[0]["verified"] is True
    assert rows[0]["size_bytes"] > 0
    assert rows[0]["size_text"]
    assert "panel.db" in rows[0]["files"]


def test_beta9_memory_breakdown_is_additive_and_tracks_panel_peak():
    values = {
        "MemTotal": 1_000_000,
        "MemFree": 100_000,
        "MemAvailable": 400_000,
        "Cached": 200_000,
        "Buffers": 20_000,
        "SReclaimable": 30_000,
        "Shmem": 10_000,
        "SUnreclaim": 40_000,
        "KernelStack": 5_000,
        "PageTables": 5_000,
        "Percpu": 1_000,
        "SwapTotal": 0,
        "SwapFree": 0,
    }
    panel = {"current": 80_000_000, "peak": 100_000_000, "file": 10_000_000, "kernel": 2_000_000}
    nginx = {"current": 20_000_000, "peak": 25_000_000, "file": 4_000_000, "kernel": 1_000_000}
    result = core._build_memory_breakdown(values, panel, nginx)
    assert sum(item["bytes"] for item in result["segments"]) == result["total"]
    assert result["panel_current"] == 80_000_000
    assert result["panel_peak"] == 100_000_000
    assert result["used_percent"] == 60.0
    assert result["status_class"] == "normal"


def test_panel_access_job_returns_complete_terminal_log(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(core, "PANEL_ACCESS_JOBS_DIR", tmp_path)
    token = "z" * 43
    job_dir = tmp_path / token
    job_dir.mkdir()
    (job_dir / "status.json").write_text(
        json.dumps({"state": "running", "message": "Certbot"}),
        encoding="utf-8",
    )
    full_log = "BEGIN\n" + ("certificate-output\n" * 4000) + "END\n"
    assert len(full_log) > 24000
    (job_dir / "access.log").write_text(full_log, encoding="utf-8")

    job = core.get_panel_access_job(token)
    assert job is not None
    assert job["log"] == full_log
    assert job["log"].startswith("BEGIN")
    assert job["log"].endswith("END\n")
