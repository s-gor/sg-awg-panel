from __future__ import annotations

import json
from pathlib import Path

import pytest

import awgpanel.core as core
import awgpanel.db as db
import awgpanel.node_clients as node_clients
import awgpanel.node_manager as nodes

ROOT = Path(__file__).resolve().parents[1]


def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setenv("SG_AWG_NODE_ENV", str(tmp_path / "missing-agent.env"))
    db.init_db()


def test_remote_profile_uses_node_server_key_not_client_key(monkeypatch):
    client = {
        "id": 8,
        "name": "USA-Client",
        "node_id": 2,
        "deployment_state": "active",
        "deployment_error": "",
        "enabled": 1,
        "expires_at": None,
        "address": "10.77.2.2/32",
        "private_key": "client-private",
        "public_key": "client-public",
        "preshared_key": "client-psk",
        "allowed_ips": "0.0.0.0/0",
        "excluded_ips": "",
        "dns_servers": "1.1.1.1",
        "mtu": 1280,
    }
    node = {
        "id": 2,
        "name": "CC2-Node",
        "public_ipv4": "54.196.170.197",
        "public_host": "",
    }
    runtime = {
        "listen_port": 585,
        "public_key": "stale-or-wrong-value",
        "server_public_key": "real-node-server-key",
        "mtu": 1280,
        "masking": {},
    }
    monkeypatch.setattr(node_clients, "node_client_context", lambda row: (node, runtime))
    monkeypatch.setattr(core, "get_awg_settings", lambda: {"dns_servers": "1.1.1.1"})

    rendered = node_clients.render_remote_client_config(client)

    assert "PublicKey = real-node-server-key" in rendered
    assert "PublicKey = client-public" not in rendered


def test_remote_profile_is_blocked_until_node_connection_has_real_server_key(monkeypatch):
    client = {
        "id": 8,
        "name": "USA-Client",
        "node_id": 2,
        "deployment_state": "active",
        "enabled": 1,
        "expires_at": None,
        "address": "10.77.2.2/32",
        "private_key": "client-private",
        "public_key": "same-key",
        "preshared_key": "client-psk",
        "allowed_ips": "0.0.0.0/0",
        "excluded_ips": "",
        "dns_servers": "1.1.1.1",
        "mtu": 1280,
    }
    monkeypatch.setattr(
        node_clients,
        "node_client_context",
        lambda row: (
            {"id": 2, "name": "Node", "public_ipv4": "203.0.113.20"},
            {"listen_port": 585, "server_public_key": "same-key", "public_key": "same-key"},
        ),
    )
    monkeypatch.setattr(core, "get_awg_settings", lambda: {"dns_servers": "1.1.1.1"})

    with pytest.raises(Exception, match="Обновить подключение ноды"):
        node_clients.render_remote_client_config(client)


def test_refresh_job_updates_node_runtime_and_server_key(tmp_path, monkeypatch):
    fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(nodes, "_local_machine_id", lambda: "controller-machine")
    nodes.ensure_local_node(public_host="controller.example.com")
    node, _token = nodes.create_node(name="USA Node")
    with db.connect() as con:
        con.execute(
            "UPDATE cluster_nodes SET state='online',service_awg='active',agent_token_hash='token' WHERE id=?",
            (int(node["id"]),),
        )
    job = nodes.queue_job(int(node["id"]), "refresh")
    result = nodes.finish_job(
        int(node["id"]),
        int(job["id"]),
        ok=True,
        result={
            "metadata": {
                "agent_version": "0.7.0-RC5",
                "machine_id": "node-machine",
                "public_ipv4": "203.0.113.20",
                "private_ipv4": "10.0.0.20",
                "services": {"awg": "active", "traffic": "active", "nginx": "active"},
                "awg_runtime": {
                    "listen_port": 585,
                    "server_public_key": "real-node-server-key",
                    "public_key": "real-node-server-key",
                    "server_network": "10.77.1.0/24",
                    "interface_address": "10.77.1.1/24",
                    "peers": {},
                },
            }
        },
    )

    updated = nodes.get_node(int(node["id"]))
    assert result["state"] == "success"
    assert result["result"]["message"] == "Подключение SG-Node обновлено"
    assert updated["awg_runtime"]["server_public_key"] == "real-node-server-key"
    assert updated["public_ipv4"] == "203.0.113.20"


def test_controller_cannot_enroll_itself_as_node(tmp_path, monkeypatch):
    fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(nodes, "_local_machine_id", lambda: "same-machine")
    nodes.ensure_local_node(public_host="controller.example.com")
    node, token = nodes.create_node(name="Accidental self node")

    with pytest.raises(ValueError, match="уже является Controller"):
        nodes.enroll_node(
            slug=str(node["slug"]),
            enrollment_token=token,
            metadata={
                "machine_id": "same-machine",
                "public_ipv4": "203.0.113.10",
                "private_ipv4": "10.0.0.10",
                "awg_runtime": {},
            },
        )


def test_ui_and_help_explain_remote_apply_and_refresh():
    clients = (ROOT / "awgpanel/templates/clients.html").read_text(encoding="utf-8")
    node_detail = (ROOT / "awgpanel/templates/node_detail.html").read_text(encoding="utf-8")
    help_text = (ROOT / "awgpanel/templates/help.html").read_text(encoding="utf-8")

    assert "client_stats.applying > 0" in clients
    assert "Обновить подключение ноды" in node_detail
    assert "Статус «Применяется» означает" in help_text
