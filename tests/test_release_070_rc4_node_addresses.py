from __future__ import annotations

from pathlib import Path

import pytest

import awgpanel.db as db
import awgpanel.node_clients as node_clients
from node_agent import agent

ROOT = Path(__file__).resolve().parents[1]


def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()


def runtime_with_local_peer(address: str = "10.77.1.2/32") -> dict:
    return {
        "listen_port": 585,
        "public_key": "server-public-key",
        "server_network": "10.77.1.0/24",
        "interface_address": "10.77.1.1/24",
        "node_slot": 1,
        "peers": {
            "local-public-key": {
                "allowed_ips": address,
                "managed": False,
                "name": "Local client",
                "latest_handshake": 0,
                "rx": 0,
                "tx": 0,
            }
        },
    }


def test_next_remote_address_reserves_real_local_node_peers(tmp_path, monkeypatch):
    fresh_db(tmp_path, monkeypatch)
    address = node_clients._next_address(7, runtime_with_local_peer())
    assert address == "10.77.1.3/32"


def test_old_conflicting_controller_client_is_reallocated_before_sync(tmp_path, monkeypatch):
    fresh_db(tmp_path, monkeypatch)
    with db.connect() as con:
        node_id = con.execute(
            "INSERT INTO cluster_nodes(slug,name,state,is_local,node_slot,vpn_network) "
            "VALUES('srv2','SRV2','online',0,1,'10.77.1.0/24')"
        ).lastrowid
        client_id = con.execute(
            "INSERT INTO awg_clients(name,address,private_key,public_key,preshared_key,node_id,deployment_state) "
            "VALUES('KLMN','10.77.0.2/32','private','managed-public-key','psk',?,'active')",
            (node_id,),
        ).lastrowid

    runtime = runtime_with_local_peer()
    monkeypatch.setattr(
        node_clients,
        "require_ready_node",
        lambda requested: (
            {"id": requested, "is_local": False, "effective_state": "online", "node_slot": 1, "vpn_network": "10.77.1.0/24"},
            runtime,
        ),
    )
    captured: dict = {}

    def fake_queue(node_id_value, kind, payload):
        captured.update({"node_id": node_id_value, "kind": kind, "payload": payload})
        return {"id": 91}

    monkeypatch.setattr(node_clients, "queue_job", fake_queue)
    job = node_clients.queue_node_client_sync(node_id, target_client_ids=[client_id])

    assert job["id"] == 91
    assert captured["kind"] == "apply_awg_config"
    assert captured["payload"]["target_client_ids"] == [client_id]
    assert captured["payload"]["peers"][0]["address"] == "10.77.1.3/32"
    with db.connect() as con:
        row = con.execute(
            "SELECT address,deployment_state,deployment_job_id FROM awg_clients WHERE id=?",
            (client_id,),
        ).fetchone()
    assert row["address"] == "10.77.1.3/32"
    assert row["deployment_state"] == "queued"
    assert row["deployment_job_id"] == 91


def test_agent_marks_controller_and_local_peer_ownership():
    config = """
[Interface]
Address = 10.77.0.1/24
ListenPort = 585

[Peer]
# local profile created directly on SRV2
PublicKey = local-key
AllowedIPs = 10.77.0.2/32

[Peer]
# SG-AWG-CLIENT id=17 name=KLMN role=client
PublicKey = managed-key
AllowedIPs = 10.77.0.3/32
"""
    metadata = agent._awg_peer_metadata(config)
    assert metadata["local-key"]["managed"] is False
    assert metadata["local-key"]["allowed_ips"] == "10.77.0.2/32"
    assert metadata["managed-key"]["managed"] is True
    assert metadata["managed-key"]["managed_id"] == 17
    assert metadata["managed-key"]["name"] == "KLMN"


def test_agent_rejects_managed_peer_that_overlaps_local_peer():
    runtime = {
        "listen_port": 585,
        "public_key": "server-public-key",
        "server_network": "10.77.1.0/24",
        "interface_address": "10.77.1.1/24",
        "node_slot": 1,
    }
    payload = {
        "expected": {
            "listen_port": 585,
            "server_public_key": "server-public-key",
            "server_network": "10.77.1.0/24",
            "interface_address": "10.77.1.1/24",
            "node_slot": 1,
        },
        "peers": [
            {
                "id": 8,
                "name": "KLMN",
                "public_key": "managed-key",
                "preshared_key": "psk",
                "address": "10.77.1.2/32",
            }
        ],
    }
    with pytest.raises(ValueError, match="уже занят локальным peer"):
        agent._validate_managed_peers(
            payload,
            runtime,
            reserved_addresses={"10.77.1.2": "ОПРС"},
        )


def test_duplicate_runtime_claim_keeps_local_owner_visible():
    runtime = {
        "address_claims": [
            {"address": "10.77.0.2", "key": "local-key", "managed": False, "name": "ОПРС"},
            {"address": "10.77.0.2", "key": "managed-key", "managed": True, "name": "KLMN"},
        ],
        "peers": {},
    }
    reserved = node_clients._runtime_peer_addresses(
        runtime,
        managed_public_keys={"managed-key"},
        unmanaged_only=True,
    )
    assert reserved == {"10.77.0.2": "ОПРС"}
