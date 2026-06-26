from pathlib import Path

import awgpanel.core as core
import awgpanel.db as db


def prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setattr(core, "AWG_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(core, "AWG_CONFIG_PATH", tmp_path / "config" / "awg0.conf")
    monkeypatch.setattr(core, "_require_root", lambda: None)
    db.init_db()


def test_database_defaults(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    settings = core.get_awg_settings()
    assert settings["interface_name"] == "awg0"
    assert settings["listen_port"] == 585
    assert core.list_awg_clients() == []


def test_settings_validation_generates_config(tmp_path, monkeypatch):
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


def test_add_client_and_render_config(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    keys = iter([("server-private", "server-public"), ("client-private", "client-public")])
    monkeypatch.setattr(core, "_keypair", lambda: next(keys))
    monkeypatch.setattr(core, "_psk", lambda: "shared-key")
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


def test_invalid_overlapping_headers(tmp_path, monkeypatch):
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
