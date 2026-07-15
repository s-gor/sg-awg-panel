from __future__ import annotations

import re
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import awgpanel.cascade as cascade
import awgpanel.db as db
import awgpanel.egress as egress
import awgpanel.node_manager as nodes
import awgpanel.web as web


ROOT = Path(__file__).resolve().parents[1]


def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()


def login(client):
    with client.session_transaction() as session:
        session["csrf_token"] = "token"
    response = client.post(
        "/login",
        data={"password": "correct-password", "csrf_token": "token"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_211_release_identifiers():
    assert '__version__ = "0.7.0-RC5"' in (ROOT / "awgpanel/__init__.py").read_text()
    assert 'version = "0.7.0rc5"' in (ROOT / "pyproject.toml").read_text()
    assert 'RELEASE_VERSION="v0.7.0-RC5"' in (ROOT / "install.sh").read_text()
    assert 'AGENT_VERSION = "0.7.0-RC5"' in (ROOT / "node_agent/agent.py").read_text()
    assert "sgawg070rc5" in (ROOT / "awgpanel/web.py").read_text()


def test_cluster_ui_shows_command_directly_and_has_no_card_workflow():
    template = (ROOT / "awgpanel/templates/nodes.html").read_text(encoding="utf-8")
    detail = (ROOT / "awgpanel/templates/node_detail.html").read_text(encoding="utf-8")
    help_text = (ROOT / "awgpanel/templates/help.html").read_text(encoding="utf-8")
    combined = template + detail + help_text
    assert "Добавить и показать команду" in template
    assert "Создать команду подключения" in template
    assert "Копировать команду подключения" in template
    assert "node_status_json" in template
    assert "and not one_time_command" in detail
    assert "Открыть карточку" not in combined
    assert "Карточка SG-Node" not in combined
    assert "Новое подключение" not in combined


def test_same_node_name_is_reused_without_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setenv("AWGPANEL_PASSWORD_HASH", generate_password_hash("correct-password"))
    monkeypatch.setenv("AWGPANEL_SECRET_KEY", "build-211-secret")
    app = web.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    login(client)
    with client.session_transaction() as session:
        csrf = session["csrf_token"]

    first = client.post("/cluster/nodes", data={"csrf_token": csrf, "name": "CC2-Node"})
    second = client.post("/cluster/nodes", data={"csrf_token": csrf, "name": "cc2-node"})
    assert first.status_code == 200 and second.status_code == 200
    assert len([item for item in nodes.list_nodes() if not item["is_local"]]) == 1
    assert re.search(r"--token [A-Za-z0-9_-]+", second.get_data(as_text=True))


def test_cleanup_removes_only_unused_pending_duplicates(tmp_path, monkeypatch):
    fresh_db(tmp_path, monkeypatch)
    nodes.ensure_local_node(public_host="controller.example.com")
    with db.connect() as con:
        online_id = con.execute(
            "INSERT INTO cluster_nodes(slug,name,state,is_local,agent_token_hash,registered_at,last_seen_at) "
            "VALUES('cc2-online','CC2-Node','online',0,'hash',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ).lastrowid
        con.execute(
            "INSERT INTO cluster_nodes(slug,name,state,is_local) VALUES('cc2-old','CC2-Node','pending',0)"
        )
        con.execute(
            "INSERT INTO cluster_nodes(slug,name,state,is_local) VALUES('cc2-new','cc2-node','pending',0)"
        )
    assert nodes.cleanup_duplicate_pending_nodes() == 2
    remote = [item for item in nodes.list_nodes() if not item["is_local"]]
    assert [item["id"] for item in remote] == [online_id]


def test_conntrack_flush_is_limited_to_changed_client(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(egress.shutil, "which", lambda name: "/usr/sbin/conntrack" if name == "conntrack" else None)
    monkeypatch.setattr(egress, "_run", lambda args, **kwargs: commands.append(args) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    result = egress.flush_client_connections(["10.77.0.2/32", "10.77.0.2"])
    assert result == {"available": True, "clients": 1, "commands": 4}
    assert commands == [
        ["/usr/sbin/conntrack", "-D", "-f", "ipv4", "--orig-src", "10.77.0.2"],
        ["/usr/sbin/conntrack", "-D", "-f", "ipv4", "--orig-dst", "10.77.0.2"],
        ["/usr/sbin/conntrack", "-D", "-f", "ipv4", "--reply-src", "10.77.0.2"],
        ["/usr/sbin/conntrack", "-D", "-f", "ipv4", "--reply-dst", "10.77.0.2"],
    ]


def test_cascade_assignment_flushes_old_and_new_client_flows(tmp_path, monkeypatch):
    fresh_db(tmp_path, monkeypatch)
    with db.connect() as con:
        outbound_id = con.execute(
            "INSERT INTO outbounds(name,config_text,endpoint,address,enabled) VALUES('SG-AWG Cascade','x','203.0.113.2:585','10.254.70.2/32',1)"
        ).lastrowid
        old_id = con.execute(
            "INSERT INTO awg_clients(name,address,private_key,public_key,preshared_key,egress_mode,outbound_id) "
            "VALUES('Old','10.77.0.2/32','p1','u1','s1','outbound',?)",
            (outbound_id,),
        ).lastrowid
        new_id = con.execute(
            "INSERT INTO awg_clients(name,address,private_key,public_key,preshared_key) "
            "VALUES('New','10.77.0.3/32','p2','u2','s2')"
        ).lastrowid
        con.execute(
            "UPDATE cascade_settings SET enabled=1,outbound_id=?,last_state='server_ready' WHERE id=1",
            (outbound_id,),
        )
    flushed: list[str] = []
    monkeypatch.setattr(cascade, "mutate_traffic_and_apply", lambda callback: callback())
    monkeypatch.setattr(cascade, "flush_client_connections", lambda addresses: flushed.extend(addresses))
    cascade.assign_cascade_clients([new_id])
    assert set(flushed) == {"10.77.0.2/32", "10.77.0.3/32"}
    with db.connect() as con:
        old = con.execute("SELECT egress_mode,outbound_id FROM awg_clients WHERE id=?", (old_id,)).fetchone()
        new = con.execute("SELECT egress_mode,outbound_id FROM awg_clients WHERE id=?", (new_id,)).fetchone()
    assert old["egress_mode"] == "awg_gateway" and old["outbound_id"] is None
    assert new["egress_mode"] == "outbound" and new["outbound_id"] == outbound_id


def test_clients_table_is_compact_but_full_width():
    template = (ROOT / "awgpanel/templates/clients.html").read_text(encoding="utf-8")
    css = (ROOT / "awgpanel/static/app.css").read_text(encoding="utf-8")
    assert "<th>Сервер подключения</th>" in template
    assert "<th>Статус</th>" in template
    assert "<th>Последняя активность</th>" not in template
    assert "<col class=\"col-ip\">" not in template
    assert ".clients-data-table{width:100%;min-width:0;table-layout:fixed}" in css
    assert ".clients-data-table .col-client{width:26%}" in css
