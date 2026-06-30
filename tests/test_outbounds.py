from __future__ import annotations

from pathlib import Path

import awgpanel.db as db
import awgpanel.egress as egress
from awgpanel.outbounds import parse_amneziawg_outbound_config, render_nftables_script


VALID_CONFIG = """
[Interface]
Address = 10.50.0.2/32
DNS = 1.1.1.1
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
Jc = 6
Jmin = 64
Jmax = 128
S1 = 48
S2 = 48
H1 = 123456789
H2 = 223456789
H3 = 323456789
H4 = 423456789
Table = 123

[Peer]
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
PresharedKey = CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=
AllowedIPs = 0.0.0.0/1, 128.0.0.0/1
Endpoint = vpn.example.com:51820
PersistentKeepalive = 25
"""


def prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setattr(egress, "OUTBOUND_CONFIG_DIR", tmp_path / "outbounds")
    monkeypatch.setattr(egress, "TRAFFIC_STATE_DIR", tmp_path / "traffic")
    monkeypatch.setattr(egress, "NFT_SCRIPT_PATH", tmp_path / "traffic" / "traffic.nft")
    monkeypatch.setattr(egress, "TRAFFIC_LOCK_PATH", tmp_path / "traffic.lock")
    monkeypatch.setattr(egress, "_require_root", lambda: None)
    db.init_db()


def test_outbound_config_is_sanitized():
    parsed = parse_amneziawg_outbound_config(VALID_CONFIG)
    assert parsed.endpoint == "vpn.example.com:51820"
    assert parsed.address == "10.50.0.2/32"
    assert parsed.allowed_ips == "0.0.0.0/0"
    assert "Table = off" in parsed.config_text
    assert "DNS" not in parsed.config_text
    assert "Table = 123" not in parsed.config_text
    assert "PrivateKey = AAAAA" in parsed.config_text
    assert "Jc = 6" in parsed.config_text


def test_outbound_config_rejects_shell_hooks():
    value = VALID_CONFIG.replace(
        "PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\nPostUp = touch /tmp/bad",
    )
    try:
        parse_amneziawg_outbound_config(value)
    except ValueError as exc:
        assert "postup" in str(exc).lower()
    else:
        raise AssertionError("PostUp was accepted")


def test_outbound_requires_full_tunnel():
    value = VALID_CONFIG.replace(
        "AllowedIPs = 0.0.0.0/1, 128.0.0.0/1",
        "AllowedIPs = 10.0.0.0/8",
    )
    try:
        parse_amneziawg_outbound_config(value)
    except ValueError as exc:
        assert "0.0.0.0/0" in str(exc)
    else:
        raise AssertionError("partial-tunnel outbound was accepted")


def test_nft_script_contains_mark_block_nat_and_kill_switch():
    text = render_nftables_script(
        inbound_interface="awg0",
        server_network="10.77.0.0/24",
        blocked_addresses=["10.77.0.3/32"],
        marked_clients=[("10.77.0.2/32", 0x5101, "sgo1")],
        outbound_interfaces=["sgo1"],
    )
    assert 'iifname "awg0" ip saddr 10.77.0.2 meta mark set 0x5101' in text
    assert 'meta mark 0x5101 oifname != "sgo1" drop' in text
    assert 'meta mark 0x5fff drop' in text
    assert 'ip saddr 10.77.0.0/24 oifname "sgo1" masquerade' in text


def test_database_has_outbound_and_client_egress_columns(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    with db.connect() as con:
        outbound_columns = {
            row[1] for row in con.execute("PRAGMA table_info(outbounds)")
        }
        client_columns = {row[1] for row in con.execute("PRAGMA table_info(awg_clients)")}
    assert {"name", "config_text", "endpoint", "address"} <= outbound_columns
    assert {"egress_mode", "outbound_id"} <= client_columns


def test_client_can_be_assigned_to_outbound(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(egress, "_apply_egress_runtime_unlocked", lambda: {"nft_ready": True})
    with db.connect() as con:
        cursor = con.execute(
            "INSERT INTO outbounds(name, config_text, endpoint, address) VALUES(?,?,?,?)",
            ("Germany", VALID_CONFIG, "vpn.example.com:51820", "10.50.0.2/32"),
        )
        outbound_id = int(cursor.lastrowid)
        cursor = con.execute(
            """
            INSERT INTO awg_clients(name,address,private_key,public_key,preshared_key)
            VALUES('Phone','10.77.0.2/32','private','public','psk')
            """
        )
        client_id = int(cursor.lastrowid)
    client = egress.set_client_egress(client_id, "outbound", outbound_id)
    assert client["egress_mode"] == "outbound"
    assert client["outbound_id"] == outbound_id


def test_apply_builds_outbound_route_and_nftables(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    parsed = parse_amneziawg_outbound_config(VALID_CONFIG)
    with db.connect() as con:
        con.execute(
            "UPDATE awg_settings SET configured=1, interface_name='awg0', server_network='10.77.0.0/24' WHERE id=1"
        )
        cursor = con.execute(
            "INSERT INTO outbounds(name, config_text, endpoint, address) VALUES(?,?,?,?)",
            ("Germany", parsed.config_text, parsed.endpoint, parsed.address),
        )
        outbound_id = int(cursor.lastrowid)
        con.execute(
            """
            INSERT INTO awg_clients(
                name,address,private_key,public_key,preshared_key,egress_mode,outbound_id
            ) VALUES('Phone','10.77.0.2/32','private','public','psk','outbound',?)
            """,
            (outbound_id,),
        )

    calls: list[list[str]] = []

    def fake_which(name: str):
        return f"/usr/bin/{name}"

    def fake_run(args, **kwargs):
        calls.append(list(args))
        returncode = 1 if args[:3] == ["/usr/bin/ip", "rule", "del"] else 0
        return type("Result", (), {"returncode": returncode, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(egress.shutil, "which", fake_which)
    monkeypatch.setattr(egress, "_run", fake_run)
    monkeypatch.setattr(
        egress, "apply_dnsmasq_runtime",
        lambda **kwargs: {"enabled": True, "mode": "redirect", "domains": 0},
    )
    monkeypatch.setattr(
        egress,
        "_delete_nft_tables",
        lambda: (_ for _ in ()).throw(AssertionError("old nft guard was deleted before replacement")),
    )
    monkeypatch.setattr(
        egress,
        "traffic_runtime_status",
        lambda: {"profiles": [], "nft_ready": True, "active_profiles": 1, "blocked_clients": 0},
    )

    result = egress.apply_egress_runtime()
    assert result["nft_ready"] is True
    assert any(call[:2] == ["/usr/bin/awg-quick", "strip"] for call in calls)
    assert any(call[:2] == ["/usr/bin/awg-quick", "up"] for call in calls)
    assert any(call[:4] == ["/usr/bin/ip", "route", "replace", "default"] for call in calls)
    assert any(call[:3] == ["/usr/bin/ip", "rule", "add"] for call in calls)
    assert any(call[:3] == ["/usr/bin/nft", "-c", "-f"] for call in calls)
    assert egress.NFT_SCRIPT_PATH.exists()
    nft_text = egress.NFT_SCRIPT_PATH.read_text()
    assert nft_text.startswith("delete table inet sg_awg_traffic\ndelete table ip sg_awg_traffic_nat\n")
    assert 'oifname != "sgo1" drop' in nft_text


def test_apply_validates_profile_before_server_is_configured(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    parsed = parse_amneziawg_outbound_config(VALID_CONFIG)
    with db.connect() as con:
        con.execute(
            "INSERT INTO outbounds(name, config_text, endpoint, address) VALUES(?,?,?,?)",
            ("Germany", parsed.config_text, parsed.endpoint, parsed.address),
        )

    calls: list[list[str]] = []

    monkeypatch.setattr(egress.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(args, **kwargs):
        calls.append(list(args))
        returncode = 1 if args[:3] == ["/usr/bin/ip", "rule", "del"] else 0
        return type("Result", (), {"returncode": returncode, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(egress, "_run", fake_run)
    monkeypatch.setattr(
        egress,
        "traffic_runtime_status",
        lambda: {"profiles": [], "nft_ready": False, "active_profiles": 0, "blocked_clients": 0},
    )

    egress.apply_egress_runtime()
    assert any(call[:2] == ["/usr/bin/awg-quick", "strip"] for call in calls)
    assert not any(call[:2] == ["/usr/bin/awg-quick", "up"] for call in calls)


def test_outbound_rejects_invalid_masking_numbers():
    value = VALID_CONFIG.replace("Jc = 6", "Jc = 99")
    try:
        parse_amneziawg_outbound_config(value)
    except ValueError as exc:
        assert "Jc" in str(exc)
    else:
        raise AssertionError("invalid Jc was accepted")


def test_outbound_rejects_multiline_masking_value():
    value = VALID_CONFIG.replace("I1 =", "I1 =") + "\n"
    value = value.replace("Jc = 6", "Jc = 6\n  PostUp = touch /tmp/bad")
    try:
        parse_amneziawg_outbound_config(value)
    except ValueError as exc:
        assert "одну строку" in str(exc) or "разобрать" in str(exc)
    else:
        raise AssertionError("multiline masking value was accepted")


def test_create_outbound_reuses_lowest_free_slot(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(egress, "_apply_egress_runtime_unlocked", lambda: {"nft_ready": True})
    with db.connect() as con:
        con.execute(
            "INSERT INTO outbounds(id,name,config_text,endpoint,address) VALUES(1,?,?,?,?)",
            ("First", VALID_CONFIG, "vpn.example.com:51820", "10.50.0.2/32"),
        )
        con.execute(
            "INSERT INTO outbounds(id,name,config_text,endpoint,address) VALUES(3,?,?,?,?)",
            ("Third", VALID_CONFIG, "vpn.example.com:51820", "10.50.0.4/32"),
        )
    created = egress.create_outbound("Second", VALID_CONFIG)
    assert created["id"] == 2
