from __future__ import annotations

from pathlib import Path

import awgpanel.cascade as cascade
import awgpanel.core as core
import awgpanel.db as db
import awgpanel.node_manager as node_manager
import awgpanel.web as web
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]


def _prepare_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()


def test_hotfix2_schema_marks_managed_service_clients(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    with db.connect() as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(awg_clients)")}
    assert "system_role" in columns


def test_exit_service_address_does_not_overlap_normal_client_network(monkeypatch):
    monkeypatch.setattr(cascade, "get_awg_settings", lambda: {"server_network": "10.77.0.0/24"})
    monkeypatch.setattr(cascade, "list_awg_clients", lambda **_kwargs: [{"address": "10.254.70.2/32"}])
    assert cascade._cascade_service_address() == "10.254.71.2/32"


def test_exit_server_config_nats_dedicated_cascade_peer(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    with db.connect() as con:
        con.execute(
            """
            UPDATE awg_settings SET configured=1, endpoint_host='203.0.113.10',
                external_interface='ens5', interface_name='awg0',
                server_network='10.77.0.0/24', private_key='server-private',
                public_key='server-public', nat_enabled=1, h1='1', h2='2', h3='3', h4='4'
            WHERE id=1
            """
        )
        con.execute(
            """
            INSERT INTO awg_clients(
                name,address,private_key,public_key,preshared_key,system_role,access_enabled
            ) VALUES('sg-cascade-entry','10.254.70.2/32','private','public','psk','cascade_exit',0)
            """
        )
    text = core.render_awg_server_config()
    assert "iptables -t nat -A POSTROUTING -s 10.254.70.2/32 -o ens5 -j MASQUERADE" in text
    assert "AllowedIPs = 10.254.70.2/32" in text


def test_assigning_clients_replaces_previous_complete_selection(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    with db.connect() as con:
        outbound_id = int(
            con.execute(
                "INSERT INTO outbounds(name,config_text,endpoint,address) VALUES(?,?,?,?)",
                ("SG-AWG Cascade", "config", "203.0.113.20:585", "10.254.70.2/32"),
            ).lastrowid
        )
        first_id = int(
            con.execute(
                """INSERT INTO awg_clients(
                    name,address,private_key,public_key,preshared_key,egress_mode,outbound_id
                ) VALUES('First','10.77.0.2/32','p1','u1','s1','outbound',?)""",
                (outbound_id,),
            ).lastrowid
        )
        second_id = int(
            con.execute(
                """INSERT INTO awg_clients(
                    name,address,private_key,public_key,preshared_key
                ) VALUES('Second','10.77.0.3/32','p2','u2','s2')"""
            ).lastrowid
        )
        con.execute(
            "UPDATE cascade_settings SET enabled=1,outbound_id=? WHERE id=1",
            (outbound_id,),
        )
    monkeypatch.setattr(cascade, "mutate_traffic_and_apply", lambda callback: callback())
    cascade.assign_cascade_clients([second_id])
    with db.connect() as con:
        first = con.execute("SELECT egress_mode,outbound_id FROM awg_clients WHERE id=?", (first_id,)).fetchone()
        second = con.execute("SELECT egress_mode,outbound_id FROM awg_clients WHERE id=?", (second_id,)).fetchone()
    assert first["egress_mode"] == "awg_gateway" and first["outbound_id"] is None
    assert second["egress_mode"] == "outbound" and second["outbound_id"] == outbound_id


def test_cluster_bootstrap_command_installs_curl_before_download():
    command = node_manager.node_install_command("https://panel.example.com")
    assert command.startswith("sudo bash -c ")
    assert "command -v curl" in command
    assert "apt-get install" in command
    assert "bootstrap/sg-awg-node-install.sh" in command


def test_hotfix2_templates_have_explicit_wizard_states_and_actions():
    cascade_html = (ROOT / "awgpanel/templates/cascade.html").read_text(encoding="utf-8")
    nodes_html = (ROOT / "awgpanel/templates/nodes.html").read_text(encoding="utf-8")
    clients_html = (ROOT / "awgpanel/templates/clients.html").read_text(encoding="utf-8")
    system_html = (ROOT / "awgpanel/templates/system.html").read_text(encoding="utf-8")
    css = (ROOT / "awgpanel/static/app.css").read_text(encoding="utf-8")

    assert "Сервер подключения" in cascade_html
    assert "Сервер выхода в интернет" in cascade_html
    assert "Вернуть прямой выход в интернет" in cascade_html
    assert "Клиенты остаются в разделе Clients" in cascade_html
    assert all(f"ШАГ {number}" in nodes_html for number in range(1, 5))
    assert "Добавить и показать команду" in nodes_html
    assert "IP и порт вручную вводить не нужно" in nodes_html
    assert "Всего серверов" in nodes_html and "Ожидают подключения" in nodes_html
    assert "Удалить клиента" in clients_html
    assert "Идентификация сервера" in system_html
    assert ".cluster-wizard-step" in css
    assert ".cascade-server-select-card" in css


def test_release_build_identifiers_are_205():
    assert "0.7.0-RC4" in (ROOT / "awgpanel/__init__.py").read_text(encoding="utf-8")
    assert "sgawg070rc4" in (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")


def test_controller_serves_node_bootstrap_without_github(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setenv("AWGPANEL_PASSWORD_HASH", generate_password_hash("correct-password"))
    monkeypatch.setenv("AWGPANEL_SECRET_KEY", "hotfix3-test-secret")
    app = web.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    script = client.get("/bootstrap/sg-awg-node-install.sh")
    assert script.status_code == 200
    text = script.get_data(as_text=True)
    assert "sg-awg-node.tar.gz" in text
    assert "01-install-sg-awg-node.sh" in text

    bundle = client.get("/bootstrap/sg-awg-node.tar.gz")
    assert bundle.status_code == 200
    assert bundle.mimetype == "application/gzip"
    assert len(bundle.data) > 1000
