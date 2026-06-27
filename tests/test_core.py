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
