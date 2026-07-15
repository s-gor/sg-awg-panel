from __future__ import annotations

import json
from pathlib import Path

import pytest

import awgpanel.core as core
import awgpanel.db as db
import awgpanel.node_manager as nodes
from awgpanel.traffic import exported_allowed_ips
from node_agent import agent

ROOT = Path(__file__).resolve().parents[1]


def prepare_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setenv("SG_AWG_NODE_ENV", str(tmp_path / "missing-agent.env"))
    db.init_db()


def test_rc5_allocates_twelve_permanent_node_pools_and_never_reuses_retired_slot(tmp_path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)
    nodes.ensure_local_node(public_host="controller.example.com")
    created = [nodes.create_node(name=f"Node {number}")[0] for number in range(1, 13)]
    assert [item["node_slot"] for item in created] == list(range(1, 13))
    assert [item["vpn_network"] for item in created] == [
        f"10.77.{number}.0/24" for number in range(1, 13)
    ]
    assert nodes.get_node_by_slug("controller")["vpn_network"] == "10.77.0.0/24"

    nodes.delete_node(int(created[0]["id"]))
    with pytest.raises(ValueError, match="12 SG-Node"):
        nodes.create_node(name="Replacement")
    with db.connect() as con:
        retired = con.execute(
            "SELECT node_id,retired_at FROM cluster_pool_slots WHERE slot=1"
        ).fetchone()
        remote_count = con.execute(
            "SELECT COUNT(*) FROM cluster_nodes WHERE is_local=0"
        ).fetchone()[0]
    assert retired["node_id"] is None and retired["retired_at"]
    assert remote_count == 11


def test_rc5_migration_moves_each_remote_client_into_its_server_pool(tmp_path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)
    nodes.ensure_local_node(public_host="controller.example.com")
    first = nodes.create_node(name="France")[0]
    second = nodes.create_node(name="USA")[0]
    with db.connect() as con:
        con.execute(
            "INSERT INTO awg_clients(name,address,private_key,public_key,preshared_key) "
            "VALUES('Controller client','10.77.0.2/32','p0','k0','s0')"
        )
        con.execute(
            "INSERT INTO awg_clients(name,address,private_key,public_key,preshared_key,node_id) "
            "VALUES('France client','10.77.0.2/32','p1','k1','s1',?)",
            (int(first["id"]),),
        )
        con.execute(
            "INSERT INTO awg_clients(name,address,private_key,public_key,preshared_key,node_id) "
            "VALUES('USA client','10.77.0.2/32','p2','k2','s2',?)",
            (int(second["id"]),),
        )
    db.init_db()
    with db.connect() as con:
        rows = con.execute(
            "SELECT name,address,deployment_state FROM awg_clients ORDER BY id"
        ).fetchall()
    assert [(row["name"], row["address"]) for row in rows] == [
        ("Controller client", "10.77.0.2/32"),
        ("France client", "10.77.1.2/32"),
        ("USA client", "10.77.2.2/32"),
    ]
    assert rows[1]["deployment_state"] == rows[2]["deployment_state"] == "queued"


def test_rc5_node_agent_migrates_awg0_peers_and_preserves_local_node_pool(tmp_path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)
    nodes.ensure_local_node(public_host="node.example.com")
    with db.connect() as con:
        con.execute(
            "UPDATE awg_settings SET configured=1,server_network='10.77.0.0/24' WHERE id=1"
        )
        con.execute(
            "INSERT INTO awg_clients(name,address,private_key,public_key,preshared_key) "
            "VALUES('Local','10.77.0.2/32','private','local-key','psk')"
        )
    config = """[Interface]
Address = 10.77.0.1/24
ListenPort = 585
PrivateKey = server-private
PostUp = nft add rule ip filter forward ip saddr 10.77.0.0/24 accept

[Peer]
PublicKey = local-key
PresharedKey = psk
AllowedIPs = 10.77.0.2/32
"""
    expected = {
        "node_slot": 3,
        "server_network": "10.77.3.0/24",
        "interface_address": "10.77.3.1/24",
    }
    migrated, moved, old_network = agent._migrate_pool_config(config, expected)
    assert old_network == "10.77.0.0/24"
    assert "Address = 10.77.3.1/24" in migrated
    assert "AllowedIPs = 10.77.3.2/32" in migrated
    assert "10.77.3.0/24 accept" in migrated
    assert moved == {"local-key": "10.77.3.2/32"}

    monkeypatch.setenv("AWGPANEL_DB", str(db.DB_PATH))
    agent._update_local_panel_pool(expected, moved)
    agent_env = tmp_path / "agent.env"
    agent_env.write_text("NODE_SLUG=node-3\n", encoding="utf-8")
    monkeypatch.setenv("SG_AWG_NODE_ENV", str(agent_env))
    db.init_db()
    with db.connect() as con:
        settings = con.execute("SELECT server_network FROM awg_settings WHERE id=1").fetchone()
        local = con.execute(
            "SELECT node_slot,vpn_network FROM cluster_nodes WHERE is_local=1"
        ).fetchone()
        client = con.execute(
            "SELECT address FROM awg_clients WHERE public_key='local-key'"
        ).fetchone()
    assert settings["server_network"] == "10.77.3.0/24"
    assert (local["node_slot"], local["vpn_network"]) == (3, "10.77.3.0/24")
    assert client["address"] == "10.77.3.2/32"
    preserved = nodes.ensure_local_node(public_host="node.example.com")
    assert (preserved["node_slot"], preserved["vpn_network"]) == (3, "10.77.3.0/24")


def test_rc5_amnezia_full_tunnel_is_exact_and_custom_routes_remain_custom():
    assert exported_allowed_ips("0.0.0.0/0") == "0.0.0.0/0, ::/0"
    assert exported_allowed_ips("10.0.0.0/8, 192.168.0.0/16") == "10.0.0.0/8, 192.168.0.0/16"
    assert exported_allowed_ips("0.0.0.0/0", "10.0.0.0/8") != "0.0.0.0/0, ::/0"
    assert "exported_allowed_ips(" in (ROOT / "awgpanel/core.py").read_text(encoding="utf-8")
    assert "exported_allowed_ips(" in (ROOT / "awgpanel/node_clients.py").read_text(encoding="utf-8")


def prepare_backup(tmp_path, monkeypatch):
    prepare_db(tmp_path, monkeypatch)
    monkeypatch.setattr(core, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(core, "AWG_CONFIG_DIR", tmp_path / "amneziawg")
    monkeypatch.setattr(core, "AWG_CONFIG_PATH", tmp_path / "amneziawg" / "awg0.conf")
    monkeypatch.setattr(core, "_require_root", lambda: None)
    monkeypatch.setattr(core, "awg_service_state", lambda: "inactive")
    monkeypatch.setattr(core, "_managed_backup_sources", lambda: [])
    core.AWG_CONFIG_DIR.mkdir(parents=True)
    core.AWG_CONFIG_PATH.write_text(
        "[Interface]\nAddress = 10.77.0.1/24\nPrivateKey = secret\n", encoding="utf-8"
    )
    nodes.ensure_local_node(public_host="controller.example.com")
    with db.connect() as con:
        con.execute(
            "INSERT INTO awg_clients(name,address,private_key,public_key,preshared_key) "
            "VALUES('Backup client','10.77.0.2/32','private','public','psk')"
        )


def test_rc5_backup_manifest_cannot_be_rebuilt_after_tampering_or_deletion(tmp_path, monkeypatch):
    prepare_backup(tmp_path, monkeypatch)
    backup = core._backup_state("manual")
    first = core.verify_backup(backup.name)
    assert first["verified"] is True
    assert first["summary"]["clients"] == 1
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"]["panel.db"]["sha256"]
    assert manifest["files"]["metadata.json"]["sha256"]

    with (backup / "panel.db").open("ab") as stream:
        stream.write(b"tampered")
    assert core.verify_backup(backup.name)["verified"] is False
    second = core.verify_backup(backup.name)
    assert second["verified"] is False
    assert any("контрольная сумма" in item for item in second["verification_errors"])

    metadata_tampered = core._backup_state("metadata-tampered")
    metadata = json.loads((metadata_tampered / "metadata.json").read_text(encoding="utf-8"))
    metadata["service_state"] = "active" if metadata.get("service_state") != "active" else "inactive"
    (metadata_tampered / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    changed = core.verify_backup(metadata_tampered.name)
    assert changed["verified"] is False
    assert any("metadata.json" in item for item in changed["verification_errors"])

    clean = core._backup_state("clean")
    (clean / "manifest.json").unlink()
    missing = core.verify_backup(clean.name)
    assert missing["verified"] is False
    assert any("manifest.json" in item for item in missing["verification_errors"])


def test_rc5_restore_is_blocked_before_any_change_when_backup_is_damaged(tmp_path, monkeypatch):
    prepare_backup(tmp_path, monkeypatch)
    backup = core._backup_state("manual")
    with (backup / "panel.db").open("ab") as stream:
        stream.write(b"damage")
    restored: list[Path] = []
    monkeypatch.setattr(core, "_restore_backup", lambda path: restored.append(path))
    with pytest.raises(core.AWGPanelError, match="не прошла проверку"):
        core.restore_backup(backup.name)
    assert restored == []


def test_rc5_main_only_delivery_and_compact_ssh_menu_are_regressions():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    updater = (ROOT / "deploy/update-from-github.sh").read_text(encoding="utf-8")
    core_source = (ROOT / "awgpanel/core.py").read_text(encoding="utf-8")
    cli = (ROOT / "awgpanel/admin_cli.py").read_text(encoding="utf-8")
    assert "archive/refs/heads/main.tar.gz" in installer
    assert "archive/refs/heads/main.tar.gz" in updater
    assert "raw.githubusercontent.com/{UPDATE_REPOSITORY}/main/awgpanel/__init__.py" in core_source
    agent_service = (ROOT / "deploy/install-node-agent.sh").read_text(encoding="utf-8")
    assert "/var/lib/sg-awg-panel" in agent_service.split("ReadWritePaths=", 1)[1].splitlines()[0]
    assert '"10", "Проверить резервную копию"' in cli
    for removed in ("Показать только адрес панели", "Полная диагностика", "Восстановить доступ к панели", "Показать последние ошибки"):
        assert removed not in cli


def test_rc5_backup_follows_letsencrypt_directory_symlink(tmp_path):
    archive = tmp_path / "archive" / "vpn.example.com"
    archive.mkdir(parents=True)
    (archive / "fullchain1.pem").write_text("certificate", encoding="utf-8")
    live = tmp_path / "live" / "vpn.example.com"
    live.parent.mkdir(parents=True)
    live.symlink_to(archive, target_is_directory=True)
    destination = tmp_path / "backup" / "etc" / "letsencrypt" / "live" / "vpn.example.com"

    copied = core._copy_backup_source(live, destination)

    assert [path.relative_to(destination).as_posix() for path in copied] == ["fullchain1.pem"]
    assert (destination / "fullchain1.pem").read_text(encoding="utf-8") == "certificate"
    assert not (destination / "fullchain1.pem").is_symlink()


def test_rc5_backup_rejects_managed_path_escape_even_with_rebuilt_manifest(tmp_path, monkeypatch):
    prepare_backup(tmp_path, monkeypatch)
    backup = core._backup_state("managed-path")
    metadata_path = backup / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["managed_files"] = ["managed/../../etc/shadow"]
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    core._write_backup_manifest(backup)

    result = core.verify_backup(backup.name)

    assert result["verified"] is False
    assert any("Недопустимый управляемый путь" in item for item in result["verification_errors"])
