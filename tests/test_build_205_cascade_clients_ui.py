from __future__ import annotations

import time
from pathlib import Path

import awgpanel.cascade as cascade
import awgpanel.db as db
from awgpanel.outbounds import render_nftables_script
from awgpanel.traffic_rules import CompiledRule, render_rule_nft

ROOT = Path(__file__).resolve().parents[1]


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()


def test_205_fallback_guards_only_apply_to_original_awg_client_traffic():
    text = render_nftables_script(
        inbound_interface="awg0",
        server_network="10.77.0.0/24",
        blocked_addresses=["10.77.0.3/32"],
        marked_clients=[("10.77.0.2/32", 0x5101, "sgo1")],
        outbound_interfaces=["sgo1"],
    )
    assert 'iifname "awg0" meta mark 0x5fff drop' in text
    assert 'iifname "awg0" meta mark 0x5101 oifname != "sgo1" drop' in text
    assert '\n    meta mark 0x5fff drop' not in text
    assert '\n    meta mark 0x5101 oifname != "sgo1" drop' not in text


def test_205_policy_guards_do_not_drop_reverse_packets_to_awg_clients():
    compiled = [
        CompiledRule(
            id=1,
            priority=10,
            name="Cascade",
            client_addresses=("10.77.0.2/32",),
            list_kind="",
            list_items=(),
            inline_domains=(),
            inline_cidrs=(),
            protocol="any",
            ports=(),
            invert=False,
            action_mode="outbound",
            outbound_id=2,
            schedule="",
        )
    ]
    _decl, _classify, guards, _domains = render_rule_nft(
        inbound_interface="awg0", rules=compiled
    )
    joined = "\n".join(guards)
    assert 'iifname "awg0" meta mark 0x5102 oifname != "sgo2" drop' in joined
    assert all(line.lstrip().startswith('iifname "awg0"') for line in guards)


def test_205_server_test_is_not_reported_as_full_cascade_success(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    with db.connect() as con:
        outbound_id = con.execute(
            "INSERT INTO outbounds(name,config_text,endpoint,address,enabled) VALUES(?,?,?,?,1)",
            ("SG-AWG Cascade", "config", "203.0.113.20:585", "10.254.70.2/32"),
        ).lastrowid
        con.execute(
            "UPDATE cascade_settings SET enabled=1,outbound_id=?,last_state='pending_check' WHERE id=1",
            (outbound_id,),
        )
    monkeypatch.setattr(
        cascade,
        "traffic_runtime_status",
        lambda: {"profiles": [{"id": int(outbound_id), "healthy": True}]},
    )
    monkeypatch.setattr(cascade, "_cascade_route_diagnostic", lambda _id: (True, "route ok"))
    monkeypatch.setattr(cascade, "_public_ip_via_outbound", lambda _id: ("203.0.113.20", "ok"))

    result = cascade.test_cascade()
    assert result["ok"] is True
    assert result["state"] == "server_ready"
    assert "Серверный маршрут" in result["message"]
    assert cascade.get_cascade_settings()["last_state"] == "server_ready"


def test_205_client_check_requires_fresh_bidirectional_awg_session(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    now = int(time.time())
    with db.connect() as con:
        outbound_id = con.execute(
            "INSERT INTO outbounds(name,config_text,endpoint,address,enabled) VALUES(?,?,?,?,1)",
            ("SG-AWG Cascade", "config", "203.0.113.20:585", "10.254.70.2/32"),
        ).lastrowid
        con.execute(
            """
            INSERT INTO awg_clients(
                name,address,private_key,public_key,preshared_key,egress_mode,outbound_id,enabled
            ) VALUES('Phone','10.77.0.2/32','p','u','s','outbound',?,1)
            """,
            (outbound_id,),
        )
        con.execute(
            "UPDATE cascade_settings SET enabled=1,outbound_id=?,last_state='server_ready',last_test_at=CURRENT_TIMESTAMP,last_exit_ip='203.0.113.20' WHERE id=1",
            (outbound_id,),
        )

    monkeypatch.setattr(
        cascade,
        "get_awg_overview",
        lambda: {
            "clients": [{
                "name": "Phone",
                "address": "10.77.0.2/32",
                "node_id": None,
                "egress_mode": "outbound",
                "outbound_id": int(outbound_id),
                "effective_enabled": True,
                "latest_handshake": now,
                "rx": 4096,
                "tx": 8192,
            }]
        },
    )
    monkeypatch.setattr(cascade, "client_has_marked_connection", lambda address, mark: address == "10.77.0.2/32" and mark > 0)
    result = cascade.test_cascade_client()
    assert result["ok"] is True
    assert result["exit_ip"] == "203.0.113.20"
    assert cascade.get_cascade_settings()["last_state"] == "healthy"


def test_205_cascade_modes_are_real_links_and_statuses_are_honest():
    template = (ROOT / "awgpanel/templates/cascade.html").read_text(encoding="utf-8")
    assert "url_for('cascade_page', mode='cluster')" in template
    assert "url_for('cascade_page', mode='external')" in template
    assert "Вариант 1 · Из Cluster" in template
    assert "Вариант 2 · Другой сервер" in template
    assert "Серверный маршрут готов" in template
    assert "Проверить маршрут клиента" in template
    assert "Cascade работает" in template
    assert "Технические детали и диагностика" in template
    assert "sessionStorage" not in template


def test_205_clients_and_server_rename_ui_are_explicit_and_responsive():
    clients = (ROOT / "awgpanel/templates/clients.html").read_text(encoding="utf-8")
    base = (ROOT / "awgpanel/templates/base.html").read_text(encoding="utf-8")
    system = (ROOT / "awgpanel/templates/system.html").read_text(encoding="utf-8")
    nodes = (ROOT / "awgpanel/templates/nodes.html").read_text(encoding="utf-8")
    css = (ROOT / "awgpanel/static/app.css").read_text(encoding="utf-8")

    assert "client-more-menu" in clients
    assert "client.latest_handshake_text" in clients
    assert "client-traffic-cell" in clients
    assert "Переименовать сервер" in base
    assert "Переименовать сервер" in system
    assert "Переименовать" in nodes
    assert ".clients-filter-toolbar" in css
    assert ".client-detail-panel" in css
    assert ".client-more-menu" in css
    assert "205-AWG-Panel" in css
