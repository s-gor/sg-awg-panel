from __future__ import annotations

import json
from pathlib import Path

import awgpanel.config_manager as config_manager
import awgpanel.core as core
import awgpanel.db as db


def prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setattr(core, "AWG_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(core, "AWG_CONFIG_PATH", tmp_path / "config" / "awg0.conf")
    monkeypatch.setattr(core, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(config_manager, "AWG_CONFIG_PATH", tmp_path / "config" / "awg0.conf")
    monkeypatch.setattr(config_manager, "OUTBOUND_CONFIG_DIR", tmp_path / "outbounds")
    monkeypatch.setattr(config_manager, "TRAFFIC_STATE_DIR", tmp_path / "traffic")
    monkeypatch.setattr(config_manager, "NGINX_PANEL_PATH", tmp_path / "nginx-panel.conf")
    monkeypatch.setattr(config_manager, "NGINX_PLACEHOLDER_PATH", tmp_path / "nginx-placeholder.conf")
    monkeypatch.setattr(config_manager, "detect_public_ipv4", lambda: "203.0.113.10")
    monkeypatch.setattr(config_manager, "detect_external_interface", lambda: "ens5")
    monkeypatch.setattr(core, "ensure_udp_port_available", lambda *args, **kwargs: None)
    db.init_db()


def test_full_config_document_contains_every_managed_section(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    document = json.loads(config_manager.panel_config_document())
    assert document["_sgAwgPanel"]["format"] == "panel-v2"
    assert document["_sgAwgPanel"]["version"] == "0.7.0-RC5"
    assert document["_sgAwgPanel"]["secrets"] == "$KEEP"
    assert set(document) == {
        "_sgAwgPanel", "server", "clients", "access", "backups", "security",
        "outbounds", "traffic", "trafficRules", "dns", "panelAccess",
    }
    assert document["panelAccess"]["port"] == 62443
    assert document["panelAccess"]["backend"] == "127.0.0.1:18080"
    assert document["server"]["server"]["endpointHost"] == "203.0.113.10"
    assert document["traffic"]["settings"]["externalInterface"] == "ens5"


def test_full_config_current_document_validates_without_changes(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    parsed = config_manager.parse_panel_config_document(
        config_manager.panel_config_document()
    )
    assert parsed["panel_access"]["public_port"] == 62443
    assert parsed["server_values"]["external_interface"] == "ens5"
    assert parsed["clients"] == []
    assert parsed["outbounds"] == []


def test_full_config_rejects_backend_and_non_dynamic_new_port(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    document = json.loads(config_manager.panel_config_document())
    document["panelAccess"]["backend"] = "0.0.0.0:18080"
    try:
        config_manager.parse_panel_config_document(json.dumps(document))
    except ValueError as exc:
        assert "127.0.0.1:18080" in str(exc)
    else:
        raise AssertionError("unsafe backend was accepted")

    document = json.loads(config_manager.panel_config_document())
    document["panelAccess"]["port"] = 8080
    try:
        config_manager.parse_panel_config_document(json.dumps(document))
    except ValueError as exc:
        assert "49152–65535" in str(exc)
    else:
        raise AssertionError("new non-dynamic public port was accepted")


def test_generated_configs_redact_private_keys(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    config_manager.AWG_CONFIG_PATH.parent.mkdir(parents=True)
    config_manager.AWG_CONFIG_PATH.write_text(
        "[Interface]\nPrivateKey = secret-private\n\n[Peer]\nPresharedKey = secret-psk\n",
        encoding="utf-8",
    )
    config_manager.NGINX_PANEL_PATH.write_text("server { listen 62443; }\n", encoding="utf-8")
    monkeypatch.setattr(
        config_manager,
        "_command_text",
        lambda args: "32764: from all fwmark 0x5101 lookup 10001\n",
    )
    generated = config_manager.generated_configs()
    assert "secret-private" not in generated["awg"]["content"]
    assert "secret-psk" not in generated["awg"]["content"]
    assert generated["awg"]["content"].count("$REDACTED") == 2
    assert "listen 62443" in generated["nginx"]["content"]
    assert "fwmark" in generated["traffic"]["content"]


def test_full_config_roundtrip_with_real_client_and_outbound_rows(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    outbound_config = """[Interface]
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
"""
    with db.connect() as con:
        con.execute(
            """UPDATE awg_settings SET configured=1, endpoint_host='203.0.113.10',
               external_interface='ens5', private_key='server-secret', public_key='server-public',
               h1='100-200', h2='300-400', h3='500-600', h4='700-800' WHERE id=1"""
        )
        con.execute(
            """INSERT INTO awg_clients
               (name,address,private_key,public_key,preshared_key,access_token)
               VALUES ('Phone','10.77.0.2/32','client-secret','client-public','client-psk','token')"""
        )
        con.execute(
            """INSERT INTO outbounds(name,enabled,config_text,endpoint,address)
               VALUES ('Germany',1,?,'vpn.example.com:51820','10.50.0.2/32')""",
            (outbound_config,),
        )
    text = config_manager.panel_config_document()
    assert "server-secret" not in text
    assert "client-secret" not in text
    assert "client-psk" not in text
    assert "AAAAAAAA" not in text
    parsed = config_manager.parse_panel_config_document(text)
    assert parsed["clients"][0]["id"] == 1
    assert parsed["clients"][0]["name"] == "Phone"
    assert parsed["outbounds"][0]["id"] == 1
    assert parsed["outbounds"][0]["name"] == "Germany"


def test_apply_full_config_uses_safe_service_layers_and_outbound_keywords(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    parsed = {
        "server_values": {"endpoint_host": "203.0.113.10", "server_network": "10.77.0.0/24"},
        "confirm_network": False,
        "clients": [{"id": 7, "name": "Phone"}],
        "outbounds": [{
            "id": 2, "name": "Germany", "config_text": "[Interface]\n", "enabled": True,
        }],
        "traffic_settings": {"external_interface": "ens5"},
        "traffic_clients": [{"id": 7}],
        "traffic_rules_dns": {
            "mode": "off", "upstreams": "1.1.1.1",
            "advertise_to_clients": False, "block_dot": True,
        },
        "traffic_policy_rules": [],
        "access_enabled": True,
        "access_profile_title": "SG-AWG",
        "backup_schedule": "daily",
        "backup_keep": 20,
        "ip_allowlist": "",
        "dns_servers": ["1.1.1.1"],
        "panel_access": {
            "scheme": "http", "public_host": "", "public_port": 62443,
            "manage_placeholder": True,
        },
    }
    monkeypatch.setattr(config_manager.os, "geteuid", lambda: 0)
    monkeypatch.setattr(config_manager, "parse_panel_config_document", lambda text: parsed)
    monkeypatch.setattr(config_manager, "_snapshot", lambda: (snapshot, "inactive"))
    monkeypatch.setattr(
        config_manager,
        "get_awg_settings",
        lambda: {"server_network": "10.77.0.0/24"},
    )
    monkeypatch.setattr(
        config_manager,
        "get_panel_settings",
        lambda: {
            "public_scheme": "http", "public_host": "", "public_port": 62443,
            "manage_placeholder": 1, "backup_schedule": "daily",
            "backup_keep": 20, "ip_allowlist": "",
        },
    )
    calls = []
    monkeypatch.setattr(config_manager, "configure_and_start_awg", lambda **values: calls.append(("server", values)))
    monkeypatch.setattr(config_manager, "update_awg_client_document", lambda client_id, values: calls.append(("client", client_id, values)))
    monkeypatch.setattr(config_manager, "replace_outbound", lambda outbound_id, **values: calls.append(("outbound", outbound_id, values)))
    monkeypatch.setattr(config_manager, "update_traffic_document", lambda settings, clients: calls.append(("traffic", settings, clients)))
    monkeypatch.setattr(config_manager, "replace_rules_document", lambda rules: calls.append(("rules", rules)))
    monkeypatch.setattr(config_manager, "apply_egress_runtime", lambda: calls.append(("apply-egress",)))
    monkeypatch.setattr(config_manager, "configure_access_links", lambda **values: calls.append(("access", values)))
    monkeypatch.setattr(config_manager.shutil, "rmtree", lambda *args, **kwargs: None)

    config_manager.apply_panel_config_document("{}")

    assert calls[0][0] == "server"
    assert calls[1][0:2] == ("client", 7)
    assert calls[2] == (
        "outbound", 2,
        {"name": "Germany", "config_text": "[Interface]\n", "enabled": True},
    )
    assert calls[3][0] == "traffic"
    assert calls[4][0] == "rules"
    assert calls[5] == ("apply-egress",)
    assert calls[6] == ("access", {"enabled": True, "profile_title": "SG-AWG"})


def test_full_config_restore_stops_interface_before_removing_config(tmp_path, monkeypatch):
    import awgpanel.config_manager as manager
    import awgpanel.core as core
    import awgpanel.db as db

    db_path = tmp_path / "panel.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(manager.db, "DB_PATH", db_path)
    monkeypatch.setattr(manager, "AWG_CONFIG_PATH", tmp_path / "awg0.conf")
    monkeypatch.setattr(manager, "OUTBOUND_CONFIG_DIR", tmp_path / "outbounds")
    monkeypatch.setattr(manager, "TRAFFIC_STATE_DIR", tmp_path / "traffic")
    monkeypatch.setattr(manager, "DNSMASQ_CONFIG_PATH", tmp_path / "dnsmasq.conf")
    monkeypatch.setattr(manager, "apply_egress_runtime", lambda: None)

    db.init_db()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    destination = __import__("sqlite3").connect(snapshot / "panel.db")
    try:
        with db.connect() as source:
            source.backup(destination)
    finally:
        destination.close()

    manager.AWG_CONFIG_PATH.write_text("new config\n", encoding="utf-8")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["systemctl", "stop"]:
            assert manager.AWG_CONFIG_PATH.exists()
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(manager.subprocess, "run", fake_run)
    manager._restore_snapshot(snapshot, "inactive")

    assert calls[0][:2] == ["systemctl", "stop"]
    assert not manager.AWG_CONFIG_PATH.exists()
