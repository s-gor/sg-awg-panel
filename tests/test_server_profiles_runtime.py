from __future__ import annotations

import socket
import sqlite3

import pytest

from awgpanel.server_profiles import (
    MASKING_PROFILES,
    detect_masking_profile,
    ensure_udp_port_available,
    server_change_summary,
)


def settings(**updates):
    value = {
        "endpoint_host": "203.0.113.10",
        "listen_port": 585,
        "server_network": "10.77.0.0/24",
        "dns_servers": "1.1.1.1, 1.0.0.1",
        "mtu": 1280,
        "external_interface": "eth0",
        **MASKING_PROFILES["standard"],
        "h1": "100-110",
        "h2": "200-210",
        "h3": "300-310",
        "h4": "400-410",
        "i1": "",
        "i2": "",
        "i3": "",
        "i4": "",
        "i5": "",
    }
    value.update(updates)
    return value


def sqlite_row(values):
    columns = list(values)
    definition = ", ".join(f'"{name}"' for name in columns)
    placeholders = ", ".join("?" for _ in columns)
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(f"CREATE TABLE settings ({definition})")
    con.execute(
        f"INSERT INTO settings VALUES ({placeholders})",
        [values[name] for name in columns],
    )
    row = con.execute("SELECT * FROM settings").fetchone()
    assert row is not None
    return con, row


def test_real_sqlite_row_is_supported():
    con, row = sqlite_row(settings())
    try:
        assert detect_masking_profile(row) == "standard"
        result = server_change_summary(row, settings())
        assert result["changed"] == ()
        assert result["client_configs_changed"] is False
    finally:
        con.close()


def test_real_sqlite_row_detects_network_change():
    con, row = sqlite_row(settings())
    try:
        result = server_change_summary(row, settings(server_network="10.88.0.0/24"))
        assert result["network_changed"] is True
        assert result["client_configs_changed"] is True
    finally:
        con.close()


def test_udp_probe_reports_busy_new_port():
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("0.0.0.0", 0))
    port = listener.getsockname()[1]
    try:
        with pytest.raises(ValueError, match="занят"):
            ensure_udp_port_available(port)
    finally:
        listener.close()


def test_udp_probe_allows_current_port_without_binding():
    ensure_udp_port_available(585, current_port=585)
