from __future__ import annotations

import json

import pytest

from awgpanel.json_editors import (
    KEEP_SECRET,
    access_json_document,
    backup_json_document,
    client_json_document,
    dns_json_document,
    outbound_json_document,
    parse_access_json_document,
    parse_backup_json_document,
    parse_client_json_document,
    parse_dns_json_document,
    parse_outbound_json_document,
    parse_network_json_document,
    parse_security_json_document,
    parse_server_json_document,
    network_json_document,
    security_json_document,
    server_json_document,
)


SETTINGS = {
    "interface_name": "awg0",
    "endpoint_host": "vpn.example.com",
    "listen_port": 585,
    "server_network": "10.77.0.0/24",
    "dns_servers": "1.1.1.1, 1.0.0.1",
    "mtu": 1280,
    "external_interface": "ens5",
    "private_key": "secret",
    "public_key": "public",
    "jc": 6,
    "jmin": 64,
    "jmax": 128,
    "s1": 48,
    "s2": 48,
    "s3": 32,
    "s4": 16,
    "h1": "100-200",
    "h2": "300-400",
    "h3": "500-600",
    "h4": "700-800",
    "i1": "",
    "i2": "",
    "i3": "",
    "i4": "",
    "i5": "",
    "nat_enabled": 1,
    "isolate_clients": 1,
    "server_lan_networks": "192.168.10.0/24",
}

CLIENT = {
    "id": 7,
    "name": "Phone",
    "enabled": 1,
    "comment": "daily",
    "address": "10.77.0.2/32",
    "private_key": "secret",
    "public_key": "public-client",
    "preshared_key": "psk",
    "dns_servers": "",
    "mtu": None,
    "access_enabled": 1,
    "allowed_ips": "0.0.0.0/0",
    "excluded_ips": "192.168.1.0/24",
    "advertised_networks": "",
    "include_server_lan": 1,
    "egress_mode": "awg_gateway",
    "outbound_id": None,
}

OUTBOUND_CONFIG = """
[Interface]
Address = 10.50.0.2/32
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
Table = off
MTU = 1280
Jc = 6
Jmin = 64
Jmax = 128
S1 = 48
S2 = 48
S3 = 32
S4 = 16
H1 = 100-200
H2 = 300-400
H3 = 500-600
H4 = 700-800

[Peer]
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
PresharedKey = CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
PersistentKeepalive = 25
""".strip() + "\n"


def test_server_json_roundtrip_and_secret_is_hidden():
    text = server_json_document(SETTINGS)
    document = json.loads(text)
    assert document["_sgAwgPanel"]["privateKey"] == KEEP_SECRET
    assert "secret" not in text
    values, confirm = parse_server_json_document(text)
    assert values["endpoint_host"] == "vpn.example.com"
    assert values["listen_port"] == 585
    assert values["jc"] == 6
    assert confirm is False


def test_json_errors_include_line_and_column():
    with pytest.raises(ValueError, match=r"строка 2, столбец"):
        parse_server_json_document('{\n "server": }')


def test_client_json_roundtrip_and_protected_keys():
    text = client_json_document(CLIENT, SETTINGS)
    document = json.loads(text)
    assert document["_sgAwgPanel"]["privateKey"] == KEEP_SECRET
    values = parse_client_json_document(text, expected_id=7)
    assert values["name"] == "Phone"
    assert values["dns_servers"] == ""
    assert values["mtu"] is None
    assert values["allowed_ips"] == "0.0.0.0/0"
    document["_sgAwgPanel"]["privateKey"] = "changed"
    with pytest.raises(ValueError, match="Ключи клиента"):
        parse_client_json_document(json.dumps(document), expected_id=7)


def test_network_json_requires_valid_modes_and_network_arrays():
    text = network_json_document(SETTINGS, [CLIENT])
    settings, clients = parse_network_json_document(text)
    assert settings["external_interface"] == "ens5"
    assert clients[0]["id"] == 7
    assert clients[0]["egress_mode"] == "awg_gateway"
    document = json.loads(text)
    document["clients"][0]["egress"]["mode"] = "random"
    with pytest.raises(ValueError, match="awg_gateway, block или outbound"):
        parse_network_json_document(json.dumps(document))


def test_outbound_json_keeps_existing_secrets_and_builds_safe_config():
    row = {
        "id": 1,
        "name": "Germany",
        "enabled": 1,
        "config_text": OUTBOUND_CONFIG,
    }
    text = outbound_json_document(row)
    assert "AAAAAAAA" not in text
    assert "CCCCCCCC" not in text
    name, config_text, enabled = parse_outbound_json_document(text, current=row)
    assert name == "Germany"
    assert enabled is True
    assert "PrivateKey = AAAAA" in config_text
    assert "PresharedKey = CCCCC" in config_text
    assert "Table = off" in config_text


def test_new_outbound_json_rejects_keep_without_existing_profile():
    document = json.loads(outbound_json_document(None))
    document["interface"]["privateKey"] = KEEP_SECRET
    document["peer"]["publicKey"] = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
    with pytest.raises(ValueError, match="при создании"):
        parse_outbound_json_document(json.dumps(document))


def test_server_network_confirmation_must_be_boolean():
    document = json.loads(server_json_document(SETTINGS))
    document["_sgAwgPanel"]["confirmNetworkChange"] = "false"
    with pytest.raises(ValueError, match="true или false"):
        parse_server_json_document(json.dumps(document))


def test_json_documents_accept_real_sqlite_rows(tmp_path, monkeypatch):
    import awgpanel.db as db
    from awgpanel.core import get_awg_settings, list_awg_clients
    from awgpanel.egress import find_outbound

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()
    with db.connect() as con:
        con.execute(
            """
            UPDATE awg_settings SET configured=1, endpoint_host='vpn.example.com',
                external_interface='ens5', private_key='server-secret',
                public_key='server-public', h1='100-200', h2='300-400',
                h3='500-600', h4='700-800' WHERE id=1
            """
        )
        con.execute(
            """
            INSERT INTO awg_clients
                (name, address, private_key, public_key, preshared_key, access_token)
            VALUES ('Phone', '10.77.0.2/32', 'client-secret', 'client-public',
                    'client-psk', 'token')
            """
        )
        con.execute(
            """
            INSERT INTO outbounds
                (id, name, enabled, config_text, endpoint, address)
            VALUES (1, 'Germany', 1, ?, 'vpn.example.com:51820', '10.50.0.2/32')
            """,
            (OUTBOUND_CONFIG,),
        )

    settings_row = get_awg_settings()
    client_row = list_awg_clients()[0]
    outbound_row = find_outbound(1)

    assert json.loads(server_json_document(settings_row))["server"]["interfaceName"] == "awg0"
    assert json.loads(client_json_document(client_row, settings_row))["client"]["name"] == "Phone"
    assert json.loads(network_json_document(settings_row, [client_row]))["clients"][0]["id"] == 1
    assert json.loads(outbound_json_document(outbound_row))["_sgAwgPanel"]["name"] == "Germany"


PANEL = {
    "access_enabled": 1,
    "access_profile_title": "Family AWG",
    "backup_schedule": "daily",
    "backup_keep": 20,
    "public_scheme": "https",
    "public_host": "awg.example.com",
    "public_port": 62443,
    "manage_placeholder": 1,
    "ip_allowlist": "203.0.113.10/32, 198.51.100.0/24",
}


def test_alpha22_dns_json_roundtrip():
    text = dns_json_document(SETTINGS)
    assert json.loads(text)["dns"]["servers"] == ["1.1.1.1", "1.0.0.1"]
    assert parse_dns_json_document(text) == "1.1.1.1, 1.0.0.1"


def test_alpha22_backup_json_roundtrip():
    text = backup_json_document(PANEL)
    assert parse_backup_json_document(text) == ("daily", 20)


def test_alpha22_access_json_hides_tokens_and_requires_full_client_list():
    text = access_json_document(PANEL, [CLIENT])
    assert "access_token" not in text
    enabled, title, states = parse_access_json_document(text, expected_client_ids={7})
    assert enabled is True
    assert title == "Family AWG"
    assert states == {7: True}
    document = json.loads(text)
    document["clients"] = []
    with pytest.raises(ValueError, match="полный текущий список"):
        parse_access_json_document(json.dumps(document), expected_client_ids={7})


def test_alpha22_security_json_roundtrip_and_normalizes_allowlist():
    text = security_json_document(PANEL)
    values, allowlist = parse_security_json_document(text)
    assert values == {
        "scheme": "https",
        "public_host": "awg.example.com",
        "public_port": 62443,
        "manage_placeholder": True,
    }
    assert allowlist == "203.0.113.10/32, 198.51.100.0/24"


def test_legacy_direct_json_is_imported_but_never_exported():
    source = json.loads(network_json_document(SETTINGS, [CLIENT]))
    source["_sgAwgPanel"]["format"] = "traffic-v1"
    source["clients"][0]["egress"]["mode"] = "direct"
    _, clients = parse_network_json_document(json.dumps(source))
    assert clients[0]["egress_mode"] == "awg_gateway"

    exported = network_json_document(SETTINGS, [dict(CLIENT, egress_mode="direct")])
    assert '"mode": "awg_gateway"' in exported
    assert '"mode": "direct"' not in exported
