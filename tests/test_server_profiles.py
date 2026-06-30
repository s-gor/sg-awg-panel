import socket
import sqlite3

import pytest

import awgpanel.core as core
import awgpanel.db as db
from awgpanel.server_profiles import (
    HARDENED_PROFILE,
    STANDARD_PROFILE,
    detect_masking_profile,
    ensure_udp_port_available,
    network_client_addresses,
    server_change_summary,
)


def prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setattr(core, "AWG_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(core, "AWG_CONFIG_PATH", tmp_path / "config" / "awg0.conf")
    monkeypatch.setattr(core, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(core, "_require_root", lambda: None)
    monkeypatch.setattr(core, "awg_service_state", lambda: "inactive")
    monkeypatch.setattr(core, "_command_path", lambda name: None)
    db.init_db()


def test_profiles_accept_dict_and_sqlite_row(tmp_path):
    assert detect_masking_profile(dict(STANDARD_PROFILE)) == "standard"
    assert detect_masking_profile(dict(HARDENED_PROFILE)) == "hardened"
    path = tmp_path / "row.db"
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE p (jc,jmin,jmax,s1,s2,s3,s4)")
    con.execute("INSERT INTO p VALUES (6,64,128,48,48,32,16)")
    row = con.execute("SELECT * FROM p").fetchone()
    assert detect_masking_profile(row) == "standard"
    con.close()


def test_change_summary_marks_network_and_client_config():
    current = {**STANDARD_PROFILE, "server_network": "10.77.0.0/24", "listen_port": 585}
    submitted = {**current, "server_network": "10.88.0.0/24"}
    summary = server_change_summary(current, submitted)
    assert summary.client_addresses_change is True
    assert summary.clients_need_new_config is True


def test_address_plan_reserves_first_host_for_server():
    assert network_client_addresses("10.88.0.0/29", 2) == ["10.88.0.2/32", "10.88.0.3/32"]
    with pytest.raises(ValueError):
        network_client_addresses("10.88.0.0/30", 2)


def test_udp_probe_reports_busy_new_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    port = sock.getsockname()[1]
    try:
        with pytest.raises(ValueError):
            ensure_udp_port_available(port, current_port=port + 1)
        ensure_udp_port_available(port, current_port=port)
    finally:
        sock.close()


def test_network_change_renumbers_clients_and_server_mode(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    keys = iter([
        ("server-private", "server-public"),
        ("one-private", "one-public"),
        ("two-private", "two-public"),
    ])
    monkeypatch.setattr(core, "_keypair", lambda: next(keys))
    monkeypatch.setattr(core, "_psk", lambda: "shared")
    monkeypatch.setattr(core, "_reload_if_active", lambda: None)
    monkeypatch.setattr(core, "ensure_udp_port_available", lambda *args, **kwargs: None)
    core.configure_awg(endpoint_host="203.0.113.10", external_interface="ens5")
    first = core.add_awg_client("One")
    second = core.add_awg_client("Two")
    core.update_awg_client_traffic(first["id"], "10.77.0.0/24")
    core.configure_awg(
        endpoint_host="203.0.113.10",
        external_interface="ens5",
        server_network="10.88.0.0/24",
    )
    first = core.find_awg_client(first["id"])
    second = core.find_awg_client(second["id"])
    assert first["address"] == "10.88.0.2/32"
    assert second["address"] == "10.88.0.3/32"
    assert first["allowed_ips"] == "10.88.0.0/24"
