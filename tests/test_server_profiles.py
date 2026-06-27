from __future__ import annotations

import socket

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


def test_detects_standard_and_enhanced_profiles():
    assert detect_masking_profile(settings()) == "standard"
    assert detect_masking_profile(settings(**MASKING_PROFILES["enhanced"])) == "enhanced"


def test_signature_forces_custom_profile():
    assert detect_masking_profile(settings(i1="<r 2>")) == "custom"


def test_network_change_requires_new_client_configs():
    result = server_change_summary(settings(), settings(server_network="10.88.0.0/24"))
    assert result["network_changed"] is True
    assert result["client_configs_changed"] is True


def test_external_interface_only_does_not_invalidate_client_configs():
    result = server_change_summary(settings(), settings(external_interface="ens5"))
    assert result["client_configs_changed"] is False


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
