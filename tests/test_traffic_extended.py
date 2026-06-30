import pytest

import awgpanel.core as core
import awgpanel.db as db
from awgpanel.traffic import effective_allowed_ips, normalize_networks


def prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setattr(core, "AWG_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(core, "AWG_CONFIG_PATH", tmp_path / "config" / "awg0.conf")
    monkeypatch.setattr(core, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(core, "_require_root", lambda: None)
    monkeypatch.setattr(core, "awg_service_state", lambda: "inactive")
    monkeypatch.setattr(core, "_command_path", lambda name: None)
    monkeypatch.setattr(core, "_reload_if_active", lambda: None)
    db.init_db()


def configured_with_clients(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    keys = iter([
        ("server-private", "server-public"),
        ("one-private", "one-public"),
        ("two-private", "two-public"),
    ])
    monkeypatch.setattr(core, "_keypair", lambda: next(keys))
    monkeypatch.setattr(core, "_psk", lambda: "shared")
    core.configure_awg(endpoint_host="203.0.113.10", external_interface="ens5")
    return core.add_awg_client("One"), core.add_awg_client("Two")


def test_full_tunnel_exclusion_is_rendered_as_complement():
    value = effective_allowed_ips("0.0.0.0/0", "192.168.1.0/24")
    assert "0.0.0.0/0" not in value
    assert "192.168.1.0/24" not in value
    assert value


def test_network_normalization_collapses_values():
    assert normalize_networks("10.0.0.0/9, 10.128.0.0/9") == "10.0.0.0/8"
    with pytest.raises(ValueError):
        normalize_networks("2001:db8::/32")


def test_server_lan_and_exclusion_enter_client_config(tmp_path, monkeypatch):
    first, _ = configured_with_clients(tmp_path, monkeypatch)
    core.update_traffic_settings(
        isolate_clients=True,
        nat_enabled=True,
        external_interface="ens5",
        server_lan_networks="172.16.10.0/24",
    )
    core.update_awg_client_traffic(
        first["id"],
        "0.0.0.0/0",
        excluded_ips="192.168.1.0/24",
        include_server_lan=True,
    )
    text = core.render_awg_client_config(first["id"])
    allowed_line = next(line for line in text.splitlines() if line.startswith("AllowedIPs = "))
    routes = [__import__("ipaddress").ip_network(item.strip()) for item in allowed_line.split("=", 1)[1].split(",")]
    assert any(__import__("ipaddress").ip_address("172.16.10.1") in route for route in routes)
    assert not any(__import__("ipaddress").ip_address("192.168.1.1") in route for route in routes)
    assert "AllowedIPs = 0.0.0.0/0" not in text


def test_advertised_network_enters_server_peer_and_overlap_is_rejected(tmp_path, monkeypatch):
    first, second = configured_with_clients(tmp_path, monkeypatch)
    core.update_awg_client_traffic(
        first["id"],
        "0.0.0.0/0",
        advertised_networks="192.168.50.0/24",
    )
    text = core.render_awg_server_config()
    assert "AllowedIPs = 10.77.0.2/32, 192.168.50.0/24" in text
    with pytest.raises(ValueError):
        core.update_awg_client_traffic(
            second["id"],
            "0.0.0.0/0",
            advertised_networks="192.168.50.128/25",
        )


def test_nat_can_be_disabled_and_interface_changed(tmp_path, monkeypatch):
    configured_with_clients(tmp_path, monkeypatch)
    core.update_traffic_settings(
        isolate_clients=False,
        nat_enabled=False,
        external_interface="eth1",
        server_lan_networks="",
    )
    text = core.render_awg_server_config()
    assert "MASQUERADE" not in text
    settings = core.get_awg_settings()
    assert settings["external_interface"] == "eth1"
    assert settings["nat_enabled"] == 0


def test_traffic_columns_migrate_without_losing_client(tmp_path, monkeypatch):
    first, _ = configured_with_clients(tmp_path, monkeypatch)
    db.init_db()
    row = core.find_awg_client(first["id"])
    assert row["excluded_ips"] == ""
    assert row["advertised_networks"] == ""
    assert row["include_server_lan"] == 0
    settings = core.get_awg_settings()
    assert settings["nat_enabled"] == 1
    assert settings["server_lan_networks"] == ""
