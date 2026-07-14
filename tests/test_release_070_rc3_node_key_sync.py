from __future__ import annotations

import json
from pathlib import Path

import awgpanel.core as core
import awgpanel.db as db
import awgpanel.node_clients as node_clients
import awgpanel.node_manager as nodes
from node_agent import agent

ROOT = Path(__file__).resolve().parents[1]


def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()


def test_agent_accepts_current_awg_key_when_controller_heartbeat_is_stale():
    payload = {
        "expected": {
            "listen_port": 585,
            "server_public_key": "stale-controller-key",
            "server_network": "10.77.0.0/24",
        },
        "peers": [{
            "id": 7,
            "name": "America client",
            "public_key": "client-public-key",
            "preshared_key": "client-psk",
            "address": "10.77.0.2/32",
        }],
    }
    runtime = {
        "listen_port": 585,
        "public_key": "actual-running-awg-key",
        "server_network": "10.77.0.0/24",
    }
    peers = agent._validate_managed_peers(payload, runtime)
    assert peers[0]["id"] == 7


def test_controller_accepts_verified_current_node_key_and_updates_runtime(tmp_path, monkeypatch):
    fresh_db(tmp_path, monkeypatch)
    with db.connect() as con:
        node_id = con.execute(
            "INSERT INTO cluster_nodes(slug,name,state,is_local,awg_runtime_json,service_awg) "
            "VALUES('node7','Node7','online',0,?,'active')",
            (json.dumps({
                "listen_port": 585,
                "public_key": "stale-controller-key",
                "server_network": "10.77.0.0/24",
                "interface_address": "10.77.0.1/24",
            }),),
        ).lastrowid
        client_id = con.execute(
            "INSERT INTO awg_clients(name,address,private_key,public_key,preshared_key,node_id,deployment_state) "
            "VALUES('America client','10.77.0.2/32','private','client-public','psk',?,'queued')",
            (node_id,),
        ).lastrowid
    monkeypatch.setattr(
        node_clients,
        "require_ready_node",
        lambda requested: (
            {"id": requested, "is_local": False, "effective_state": "online"},
            {
                "listen_port": 585,
                "public_key": "stale-controller-key",
                "server_network": "10.77.0.0/24",
                "interface_address": "10.77.0.1/24",
                "peers": {},
            },
        ),
    )
    job = node_clients.queue_node_client_sync(node_id, target_client_ids=[client_id])
    actual_runtime = {
        "listen_port": 585,
        "public_key": "actual-running-awg-key",
        "server_network": "10.77.0.0/24",
        "interface_address": "10.77.0.1/24",
        "peers": {"client-public": {"latest_handshake": 0, "rx": 0, "tx": 0}},
    }
    finished = nodes.finish_job(
        node_id,
        int(job["id"]),
        ok=True,
        result={
            "message": "verified",
            "verified_client_ids": [client_id],
            "listen_port": 585,
            "server_network": "10.77.0.0/24",
            "server_public_key": "actual-running-awg-key",
            "runtime": actual_runtime,
        },
    )
    assert finished["state"] == "success"
    assert core.find_awg_client(client_id)["deployment_state"] == "active"
    assert nodes.get_node(node_id)["awg_runtime"]["public_key"] == "actual-running-awg-key"


def test_connect_script_enrolls_with_real_awg_runtime_and_rc3_version():
    text = (ROOT / "deploy" / "connect-node.sh").read_text(encoding="utf-8")
    assert '"agent_version":"0.7.0-RC3"' in text
    assert '"awg_runtime":awg_runtime()' in text
    assert '"public_key": public_key[0].strip() if public_key else ""' in text
