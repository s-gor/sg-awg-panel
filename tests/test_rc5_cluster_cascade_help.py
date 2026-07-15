from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

import awgpanel.core as core
import awgpanel.db as db
import awgpanel.node_clients as node_clients
import awgpanel.node_manager as nodes
import awgpanel.web as web
import node_agent.agent as agent
from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]


def login(client):
    with client.session_transaction() as session:
        session["csrf_token"] = "token"
    response = client.post(
        "/login",
        data={"password": "correct-password", "csrf_token": "token"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setenv("AWGPANEL_PASSWORD_HASH", generate_password_hash("correct-password"))
    monkeypatch.setenv("AWGPANEL_SECRET_KEY", "build-202-test-secret")
    monkeypatch.setattr(
        web,
        "get_awg_settings",
        lambda: {
            "configured": 1,
            "endpoint_host": "203.0.113.10",
            "listen_port": 585,
            "interface_name": "awg0",
            "server_network": "10.77.0.0/24",
            "dns_servers": "1.1.1.1, 1.0.0.1",
            "external_interface": "ens5",
            "mtu": 1280,
            "isolate_clients": 1,
            "jc": 6,
            "jmin": 64,
            "jmax": 128,
            "s1": 48,
            "s2": 48,
            "s3": 32,
            "s4": 16,
            "h1": "",
            "h2": "",
            "h3": "",
            "h4": "",
            "i1": "",
            "i2": "",
            "i3": "",
            "i4": "",
            "i5": "",
        },
    )
    app = web.create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def ready_remote_node(tmp_path, monkeypatch, *, name="France Exit"):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()
    nodes.ensure_local_node(public_host="controller.example.com")
    node, enrollment_token = nodes.create_node(name=name)
    enrolled, agent_token = nodes.enroll_node(
        slug=node["slug"],
        enrollment_token=enrollment_token,
        metadata={
            "agent_version": "202",
            "public_ipv4": "203.0.113.20",
            "capabilities": {"managed_clients": True, "arbitrary_shell": False},
        },
    )
    updated = nodes.heartbeat(
        int(enrolled["id"]),
        {
            "agent_version": "202",
            "public_ipv4": "203.0.113.20",
            "private_ipv4": "10.0.0.20",
            "services": {"awg": "active", "traffic": "active", "nginx": "active"},
            "awg_runtime": {
                "configured": True,
                "interface": "awg0",
                "interface_address": "10.77.1.1/24",
                "server_network": "10.77.1.0/24",
                "listen_port": 585,
                "configured_listen_port": 585,
                "public_key": "server-public-key",
                "mtu": 1280,
                "masking": {"jc": 6, "jmin": 64, "jmax": 128, "s1": 48, "s2": 48, "s3": 32, "s4": 16},
                "peers": {},
            },
        },
    )
    return updated, agent_token


def test_build_202_schema_unifies_clients_and_removes_legacy_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()
    with db.connect() as con:
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        client_columns = {row[1] for row in con.execute("PRAGMA table_info(awg_clients)")}
        cascade = con.execute("SELECT * FROM cascade_settings WHERE id=1").fetchone()
    assert {"cluster_nodes", "node_jobs", "awg_clients", "cascade_settings"} <= tables
    assert "node_profiles" not in tables
    assert {"node_id", "deployment_state", "deployment_job_id", "deployment_error", "deployed_enabled"} <= client_columns
    assert cascade is not None
    assert cascade["last_state"] == "not_configured"


def test_remote_node_uses_standard_awg_port_585_only(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()
    with pytest.raises(ValueError, match="585"):
        nodes.create_node(name="Wrong Port", public_port=64441)
    node, _token = nodes.create_node(name="Standard Port")
    assert int(node["public_port"]) == 585


def test_v202_migration_preserves_local_clients_and_drops_legacy_profile_table(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE awg_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            enabled INTEGER NOT NULL DEFAULT 1,
            address TEXT NOT NULL UNIQUE,
            private_key TEXT NOT NULL,
            public_key TEXT NOT NULL UNIQUE,
            preshared_key TEXT NOT NULL,
            comment TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO awg_clients (name,address,private_key,public_key,preshared_key,comment)
        VALUES ('Existing','10.77.0.2/32','private','public','psk','kept');
        CREATE TABLE node_profiles (id INTEGER PRIMARY KEY, node_id INTEGER, state TEXT);
        INSERT INTO node_profiles VALUES (1, 7, 'active');
        """
    )
    con.commit()
    con.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    with db.connect() as migrated:
        row = migrated.execute("SELECT * FROM awg_clients WHERE name='Existing'").fetchone()
        tables = {item[0] for item in migrated.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        sql = migrated.execute("SELECT sql FROM sqlite_master WHERE name='awg_clients'").fetchone()[0]
    assert row["comment"] == "kept"
    assert row["node_id"] is None
    assert row["deployment_state"] == "active"
    assert "node_profiles" not in tables
    assert "address TEXT NOT NULL UNIQUE" not in sql


def test_node_enrollment_heartbeat_and_safe_job_queue(tmp_path, monkeypatch):
    node, agent_token = ready_remote_node(tmp_path, monkeypatch)
    assert node["effective_state"] == "online"
    assert node["public_port"] == 585
    assert node["awg_runtime"]["listen_port"] == 585
    assert nodes.authenticate_agent(node["slug"], agent_token)["id"] == node["id"]

    queued = nodes.queue_job(int(node["id"]), "diagnostics")
    claimed = nodes.claim_next_job(int(node["id"]))
    assert claimed and claimed["id"] == queued["id"]
    finished = nodes.finish_job(
        int(node["id"]), int(queued["id"]), ok=True, result={"message": "done"}
    )
    assert finished["state"] == "success"

    try:
        nodes.queue_job(int(node["id"]), "shell")
    except ValueError as exc:
        assert "Неподдерживаемое" in str(exc)
    else:
        raise AssertionError("arbitrary job kind must be rejected")


def test_cluster_web_flow_uses_standard_port_and_explicit_status_action(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    login(client)
    page = client.get("/cluster")
    text = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Подключить SG-Node" in text
    assert "UDP-порт 585" in text

    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    created = client.post(
        "/cluster/nodes",
        data={"csrf_token": csrf, "name": "Paris Node"},
        follow_redirects=False,
    )
    assert created.status_code == 200
    node = next(item for item in nodes.list_nodes() if item["slug"] == "paris-node")
    assert node["public_port"] == 585
    html = created.get_data(as_text=True)
    match = re.search(r"--token ([A-Za-z0-9_-]+)", html)
    assert match is not None
    assert "Копировать команду подключения" in html
    assert "автоматически" in html

    enrolled = client.post(
        "/api/cluster/v1/enroll",
        json={"node": "paris-node", "token": match.group(1), "metadata": {"agent_version": "202"}},
    )
    assert enrolled.status_code == 200
    data = enrolled.get_json()
    heartbeat = client.post(
        "/api/cluster/v1/nodes/paris-node/heartbeat",
        headers={"Authorization": f"Bearer {data['agent_token']}"},
        json={
            "services": {"awg": "active", "traffic": "active", "nginx": "active"},
            "awg_runtime": {
                "interface_address": "10.77.1.1/24",
                "server_network": "10.77.1.0/24",
                "listen_port": 585,
                "public_key": "server-public-key",
                "peers": {},
            },
        },
    )
    assert heartbeat.status_code == 200
    assert heartbeat.get_json()["ok"] is True


def test_remote_client_is_created_in_unified_registry_and_verified(tmp_path, monkeypatch):
    node, _ = ready_remote_node(tmp_path, monkeypatch)
    keys = iter([("client-private-key", "client-public-key")])
    monkeypatch.setattr(core, "_keypair", lambda: next(keys))
    monkeypatch.setattr(core, "_psk", lambda: "client-preshared-key")

    created = node_clients.add_remote_client(int(node["id"]), name="Paris phone")
    assert created["node_id"] == node["id"]
    assert created["address"] == "10.77.1.2/32"
    assert created["deployment_state"] == "queued"
    job = nodes.get_job(int(created["deployment_job_id"]))
    assert job["kind"] == "apply_awg_config"
    assert job["payload"]["mode"] == "sync_clients"
    assert job["payload"]["expected"]["listen_port"] == 585
    assert job["payload"]["peers"][0]["id"] == created["id"]

    runtime = dict(node["awg_runtime"])
    runtime["peers"] = {"client-public-key": {"latest_handshake": 0, "rx": 0, "tx": 0}}
    finished = nodes.finish_job(
        int(node["id"]),
        int(job["id"]),
        ok=True,
        result={
            "message": "verified",
            "verified_client_ids": [int(created["id"])],
            "listen_port": 585,
            "server_network": "10.77.1.0/24",
            "interface_address": "10.77.1.1/24",
            "node_slot": 1,
            "server_public_key": "server-public-key",
            "runtime": runtime,
        },
    )
    assert finished["state"] == "success"
    active = core.find_awg_client(int(created["id"]))
    assert active["deployment_state"] == "active"
    config = node_clients.render_remote_client_config(active)
    assert config.startswith(
        "# Name = France Exit/Paris phone\n"
        "# Client = Paris phone\n"
        "# Source = SG-AWG-Panel\n"
    )
    assert "# Server =" not in config
    assert "SG-AWG-Panel Cluster" not in config
    assert "DNS = 1.1.1.1\n" in config
    assert "DNS = 1.1.1.1, 1.0.0.1" not in config
    assert "Endpoint = 203.0.113.20:585" in config
    assert "Address = 10.77.1.2/32" in config
    assert "PublicKey = server-public-key" in config


def test_verified_remote_client_deletion_removes_unified_registry_row(tmp_path, monkeypatch):
    node, _ = ready_remote_node(tmp_path, monkeypatch)
    monkeypatch.setattr(core, "_keypair", lambda: ("private-delete", "public-delete"))
    monkeypatch.setattr(core, "_psk", lambda: "psk-delete")
    monkeypatch.setattr(core, "_require_root", lambda: None)
    created = node_clients.add_remote_client(int(node["id"]), name="Delete me")
    create_job = nodes.get_job(int(created["deployment_job_id"]))
    runtime = dict(node["awg_runtime"])
    runtime["peers"] = {"public-delete": {"latest_handshake": 0, "rx": 0, "tx": 0}}
    nodes.finish_job(
        int(node["id"]), int(create_job["id"]), ok=True,
        result={
            "verified_client_ids": [int(created["id"])],
            "listen_port": 585,
            "server_network": "10.77.1.0/24",
            "interface_address": "10.77.1.1/24",
            "node_slot": 1,
            "server_public_key": "server-public-key",
            "runtime": runtime,
        },
    )
    core.delete_awg_client(int(created["id"]))
    deleting = core.find_awg_client(int(created["id"]))
    assert deleting["deployment_state"] == "deleting"
    delete_job = nodes.get_job(int(deleting["deployment_job_id"]))
    runtime["peers"] = {}
    finished = nodes.finish_job(
        int(node["id"]), int(delete_job["id"]), ok=True,
        result={
            "verified_client_ids": [],
            "listen_port": 585,
            "server_network": "10.77.1.0/24",
            "interface_address": "10.77.1.1/24",
            "node_slot": 1,
            "server_public_key": "server-public-key",
            "runtime": runtime,
        },
    )
    assert finished["state"] == "success"
    with db.connect() as con:
        assert con.execute("SELECT 1 FROM awg_clients WHERE id=?", (int(created["id"]),)).fetchone() is None


def test_remote_client_is_not_marked_active_without_real_runtime_confirmation(tmp_path, monkeypatch):
    node, _ = ready_remote_node(tmp_path, monkeypatch)
    monkeypatch.setattr(core, "_keypair", lambda: ("private", "public"))
    monkeypatch.setattr(core, "_psk", lambda: "psk")
    created = node_clients.add_remote_client(int(node["id"]), name="Must verify")
    job = nodes.get_job(int(created["deployment_job_id"]))
    result = nodes.finish_job(
        int(node["id"]),
        int(job["id"]),
        ok=True,
        result={
            "message": "claimed success",
            "verified_client_ids": [int(created["id"])],
            "listen_port": 64441,
            "server_network": "10.77.1.0/24",
            "interface_address": "10.77.1.1/24",
            "node_slot": 1,
            "server_public_key": "server-public-key",
        },
    )
    assert result["state"] == "error"
    row = core.find_awg_client(int(created["id"]))
    assert row["deployment_state"] == "error"
    assert "585" in row["deployment_error"] or row["deployment_error"]


def test_controller_and_node_clients_use_separate_permanent_pools(tmp_path, monkeypatch):
    node, _ = ready_remote_node(tmp_path, monkeypatch)
    with db.connect() as con:
        con.execute(
            """
            INSERT INTO awg_clients (name,address,private_key,public_key,preshared_key)
            VALUES ('Local','10.77.0.2/32','l-private','l-public','l-psk')
            """
        )
        con.execute(
            """
            INSERT INTO awg_clients (
                name,address,private_key,public_key,preshared_key,node_id,deployment_state
            ) VALUES ('Remote','10.77.1.2/32','r-private','r-public','r-psk',?,'active')
            """,
            (int(node["id"]),),
        )
        rows = con.execute(
            "SELECT node_id,address FROM awg_clients ORDER BY id"
        ).fetchall()
    assert [(row["node_id"], row["address"]) for row in rows] == [
        (None, "10.77.0.2/32"),
        (int(node["id"]), "10.77.1.2/32"),
    ]


def test_node_agent_preserves_unmanaged_peers_and_replaces_only_managed_clients():
    existing = """[Interface]
Address = 10.77.0.1/24
ListenPort = 585
PrivateKey = server-private

[Peer]
# manually managed peer
PublicKey = keep-public
PresharedKey = keep-psk
AllowedIPs = 10.77.0.9/32

[Peer]
# SG-AWG-CLIENT id=11 name=old
PublicKey = old-public
PresharedKey = old-psk
AllowedIPs = 10.77.0.2/32
"""
    updated = agent._replace_managed_peers(
        existing,
        [{
            "id": 22,
            "name": "new",
            "public_key": "new-public",
            "preshared_key": "new-psk",
            "allowed_ips": "10.77.0.3/32",
        }],
    )
    assert "keep-public" in updated
    assert "old-public" not in updated
    assert "# SG-AWG-CLIENT id=22 name=new" in updated
    assert "ListenPort = 585" in updated


def test_navigation_help_and_clients_workflow_are_explicit():
    base = (ROOT / "awgpanel/templates/base.html").read_text(encoding="utf-8")
    help_template = (ROOT / "awgpanel/templates/help.html").read_text(encoding="utf-8")
    nodes_template = (ROOT / "awgpanel/templates/nodes.html").read_text(encoding="utf-8")
    node_detail = (ROOT / "awgpanel/templates/node_detail.html").read_text(encoding="utf-8")
    clients = (ROOT / "awgpanel/templates/clients.html").read_text(encoding="utf-8")
    runtime = (ROOT / "deploy/install-node-runtime.sh").read_text(encoding="utf-8")
    assert "<b>Cascade</b>" in base
    assert "<b>Cluster</b>" in base
    assert "<b>Help</b>" in base
    assert "Сервер подключения" in clients
    assert "Добавить клиента" in nodes_template
    assert "выберите подключённую SG-Node" in nodes_template
    assert "Проверить подключение" in node_detail
    assert "Подключения через эту SG-Node" in node_detail
    assert "Первый AWG-профиль" not in node_detail
    assert "Клиентский доступ этой SG-Node" not in node_detail
    assert "UDP-порт <code>585</code>" in help_template
    assert "ListenPort = 585" in runtime
    assert "64441" not in nodes_template + node_detail + clients + help_template + runtime
    assert "10.200." not in nodes_template + node_detail + clients + help_template + runtime


def test_node_installers_are_fixed_and_no_arbitrary_shell():
    agent_source = (ROOT / "node_agent/agent.py").read_text(encoding="utf-8")
    runtime = (ROOT / "deploy/install-node-runtime.sh").read_text(encoding="utf-8")
    connect = (ROOT / "deploy/connect-node.sh").read_text(encoding="utf-8")
    assert "JOB_TO_UNIT" in agent_source
    assert "shell=True" not in agent_source
    assert '"arbitrary_shell": False' in agent_source
    assert '"managed_clients": True' in agent_source
    assert "sg-awg-node-agent.service" in runtime
    assert "/api/cluster/v1/enroll" in connect
    assert "chmod 0600" in connect


def test_cascade_public_ip_probe_is_bound_to_outbound_table():
    source = (ROOT / "awgpanel/cascade.py").read_text(encoding="utf-8")
    assert "uidrange" in source
    assert "traffic_table_for" in source
    assert "setpriv" in source
    assert "urllib.request" not in source


def test_claimed_node_job_is_requeued_after_agent_lease_expires(tmp_path, monkeypatch):
    node, _ = ready_remote_node(tmp_path, monkeypatch, name="Lease Node")
    queued = nodes.queue_job(int(node["id"]), "refresh")
    assert nodes.claim_next_job(int(node["id"]))["id"] == queued["id"]
    with db.connect() as con:
        con.execute(
            "UPDATE node_jobs SET claimed_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (int(queued["id"]),),
        )
    reclaimed = nodes.claim_next_job(int(node["id"]))
    assert reclaimed and reclaimed["id"] == queued["id"]
    assert reclaimed["state"] == "claimed"


def test_cascade_one_time_private_config_is_not_written_to_cookie_session(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        web,
        "create_exit_enrollment",
        lambda **kwargs: {
            "client": {"id": 77, "name": "sg-cascade-entry"},
            "link": "sg-awg-cascade://v1/one-time-secret",
            "expires_at": "2030-01-01T00:30:00Z",
            "exit_country_code": "US",
            "exit_name": "Virginia Exit",
            "endpoint": "203.0.113.20:585",
        },
    )
    login(client)
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    response = client.post(
        "/cascade/exit-client",
        data={"csrf_token": csrf, "name": "sg-cascade-entry"},
    )
    assert response.status_code == 200
    assert "sg-awg-cascade://v1/one-time-secret" in response.get_data(as_text=True)
    with client.session_transaction() as session:
        serialized = repr(dict(session))
        assert "one-time-secret" not in serialized
        assert "cascade_exit_config" not in session
        assert "sg-awg-cascade://v1/one-time-secret" not in serialized
