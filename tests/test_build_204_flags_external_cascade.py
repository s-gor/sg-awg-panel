import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import awgpanel.cascade as standalone_cascade
import awgpanel.cluster_cascade as cluster_cascade
import awgpanel.db as db
import awgpanel.geography as geography
import awgpanel.node_manager as node_manager

ROOT = Path(__file__).resolve().parents[1]


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()


def _ready_cluster(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    controller = node_manager.ensure_local_node(
        name="Frankfurt Controller", public_host="198.51.100.10", country_code="DE"
    )
    remote, _ = node_manager.create_node(name="Virginia Node", public_host="203.0.113.20")
    with db.connect() as con:
        con.execute(
            "UPDATE awg_settings SET configured=1, endpoint_host='198.51.100.10', "
            "listen_port=585, public_key='controller-key' WHERE id=1"
        )
        con.execute(
            "UPDATE cluster_nodes SET state='online', public_ipv4='203.0.113.20', "
            "last_seen_at=CURRENT_TIMESTAMP, service_awg='active', country_code='US', "
            "awg_runtime_json=? WHERE id=?",
            (
                json.dumps({
                    "listen_port": 585,
                    "public_key": "node-key",
                    "server_network": "10.77.0.0/24",
                    "interface_address": "10.77.0.1/24",
                }),
                int(remote["id"]),
            ),
        )
    return node_manager.get_node(int(controller["id"])), node_manager.get_node(int(remote["id"]))


def test_country_helpers_use_bundled_svg_assets_and_neutral_fallback():
    assert geography.normalize_country_code("de") == "DE"
    assert geography.country_flag_asset("DE") == "flags/de.svg"
    assert geography.country_flag_asset("US") == "flags/us.svg"
    assert geography.country_flag_asset("") == "flags/unknown.svg"
    assert geography.country_name("US") == "США"
    assert geography.normalize_country_code("Germany") == ""
    assert (ROOT / "awgpanel/static/flags/de.svg").is_file()
    assert (ROOT / "awgpanel/static/flags/us.svg").is_file()
    assert (ROOT / "awgpanel/static/flags/unknown.svg").is_file()
    assert len(list((ROOT / "awgpanel/static/flags").glob("*.svg"))) >= 60


def test_manual_country_override_is_not_replaced_by_auto_refresh(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    node = node_manager.ensure_local_node(name="Controller", country_code="DE")
    changed = node_manager.set_node_country(int(node["id"]), "FR", automatic=False)
    assert changed["country_code"] == "FR"
    assert changed["country_mode"] == "manual"

    refreshed = node_manager.ensure_local_node(name="Controller", country_code="US")
    assert refreshed["country_code"] == "FR"
    assert refreshed["country_mode"] == "manual"

    automatic = node_manager.set_node_country(int(node["id"]), "US", automatic=True)
    assert automatic["country_code"] == "US"
    assert automatic["country_mode"] == "auto"


def test_cascade_uses_cluster_registry_and_one_active_route_per_entry(tmp_path, monkeypatch):
    controller, remote = _ready_cluster(tmp_path, monkeypatch)
    monkeypatch.setattr(cluster_cascade, "_create_exit_service", lambda link: {})

    servers = cluster_cascade.cascade_servers()
    assert {item["name"] for item in servers} == {"Frankfurt Controller", "Virginia Node"}
    assert all(item["cascade_ready"] for item in servers)
    assert {item["country_code"] for item in servers} == {"DE", "US"}

    link = cluster_cascade.create_cascade_link(
        entry_node_id=int(controller["id"]), exit_node_id=int(remote["id"])
    )
    assert link["entry"]["name"] == "Frankfurt Controller"
    assert link["exit"]["name"] == "Virginia Node"

    with pytest.raises(ValueError, match="уже включён Cascade"):
        cluster_cascade.create_cascade_link(
            entry_node_id=int(controller["id"]), exit_node_id=int(remote["id"])
        )
    with pytest.raises(ValueError, match="два разных сервера"):
        cluster_cascade.create_cascade_link(
            entry_node_id=int(controller["id"]), exit_node_id=int(controller["id"])
        )


def test_standalone_enrollment_link_roundtrip_and_expiry(monkeypatch):
    config = """[Interface]
Address = 10.254.70.2/32
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=

[Peer]
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
PresharedKey = CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=
AllowedIPs = 0.0.0.0/0
Endpoint = 203.0.113.20:585
PersistentKeepalive = 25
"""
    monkeypatch.setattr(
        standalone_cascade,
        "create_exit_service_client",
        lambda **kwargs: {"client": {"id": 7, "name": "sg-cascade-entry"}, "config_text": config},
    )
    monkeypatch.setattr(standalone_cascade, "get_panel_settings", lambda: {"instance_name": "Virginia Exit"})
    monkeypatch.setattr(standalone_cascade, "_local_country_code", lambda: "US")

    enrollment = standalone_cascade.create_exit_enrollment(ttl_minutes=30)
    assert enrollment["link"].startswith("sg-awg-cascade://v1/")
    parsed = standalone_cascade.parse_exit_enrollment(enrollment["link"])
    assert parsed["exit_name"] == "Virginia Exit"
    assert parsed["exit_country_code"] == "US"
    assert parsed["endpoint"] == "203.0.113.20:585"
    assert "PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" in parsed["config"]

    future = datetime.now(timezone.utc) + timedelta(hours=2)
    with pytest.raises(ValueError, match="Срок действия ссылки Cascade истёк"):
        standalone_cascade.parse_exit_enrollment(enrollment["link"], now=future)


def test_204_templates_use_svg_flags_plain_terms_and_both_cascade_modes():
    cascade = (ROOT / "awgpanel/templates/cascade.html").read_text(encoding="utf-8")
    base = (ROOT / "awgpanel/templates/base.html").read_text(encoding="utf-8")
    nodes = (ROOT / "awgpanel/templates/nodes.html").read_text(encoding="utf-8")
    clients = (ROOT / "awgpanel/templates/clients.html").read_text(encoding="utf-8")
    help_html = (ROOT / "awgpanel/templates/help.html").read_text(encoding="utf-8")

    assert "Сервер подключения (Inbound)" in cascade
    assert "Сервер выхода в интернет (Outbound)" in cascade
    assert "Из Cluster" in cascade and "Другой сервер" in cascade
    assert "Создать ссылку для Cascade" in cascade
    assert "Проверить ссылку и включить Cascade" in cascade
    assert "Вернуть прямой выход в интернет" in cascade
    assert "country_flag_icon" in base and "country_flag_icon" in nodes and "country_flag_icon" in clients
    assert "data-flag-select" in cascade and "data-flag-select" in clients
    assert "Inbound" in help_html and "Outbound" in help_html
    assert "одноразов" in help_html.lower()


def test_cascade_service_profile_disables_automatic_default_routes():
    source = """[Interface]
Address = 10.254.1.2/32
DNS = 1.1.1.1
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=

[Peer]
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
AllowedIPs = 0.0.0.0/0
Endpoint = 203.0.113.20:585
"""
    rendered = cluster_cascade._cascade_tunnel_config(source)
    assert "Table = off" in rendered
    assert "DNS =" not in rendered
    assert "AllowedIPs = 0.0.0.0/0" in rendered


def test_204_agent_has_explicit_cascade_modes_and_standard_awg_port():
    agent = (ROOT / "node_agent/agent.py").read_text(encoding="utf-8")
    assert 'AGENT_VERSION = "0.7.0-RC6"' in agent
    assert 'CASCADE_INTERFACE = "sgcascade"' in agent
    assert 'mode == "configure_cascade"' in agent
    assert 'mode == "disable_cascade"' in agent
    assert 'mode == "test_cascade"' in agent
    assert "def restore_cascade_runtime" in agent
    assert "Cascade restore failed" in agent
    assert "listen_port" in agent and "585" in agent
    assert "64441" not in agent


def test_node_runtime_install_and_uninstall_clean_only_managed_cascade_artifacts():
    install_script = (ROOT / "deploy/install-node-runtime.sh").read_text(encoding="utf-8")
    uninstall_script = (ROOT / "deploy/uninstall-node-agent.sh").read_text(encoding="utf-8")
    for script in (install_script, uninstall_script):
        assert "sgcascade.conf" in script
        assert "sg_awg_node_cascade" in script
        assert "sg_awg_node_cascade_nat" in script
        assert "priority 13050" in script
        assert "table 23000" in script
    assert "awg0.conf" not in uninstall_script
    assert "sg-awg-server.service" not in uninstall_script
    assert "cascade.nft" in install_script
    assert "sg-awg-server.service sg-awg-traffic.service" in (
        ROOT / "deploy/install-node-agent.sh"
    ).read_text(encoding="utf-8")
