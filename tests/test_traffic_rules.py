from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pytest

import awgpanel.db as db
import awgpanel.traffic_rules as rules
from awgpanel.traffic_rules import CompiledRule


def prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setattr(rules, "DNSMASQ_CONFIG_PATH", tmp_path / "dnsmasq.conf")
    monkeypatch.setattr(rules, "TRAFFIC_SCHEDULE_STATE_PATH", tmp_path / "schedule-state.json")
    db.init_db()
    rules.ensure_builtin_lists()


def test_list_parsers_normalize_domains_and_ipv4_networks():
    assert rules.parse_domain_list(
        "# comment\nDOMAIN:Example.COM\nfull:sub.example.com\nregexp:skip\n",
        "v2fly",
    ) == ["example.com", "sub.example.com"]
    assert rules.parse_domain_list("*://*.Example.com/*\n", "antifilter") == ["example.com"]
    assert rules.parse_cidr_list("10.0.0.1\n10.0.0.0/25\n10.0.0.128/25\n") == ["10.0.0.0/24"]


def test_geo_and_asn_source_references_are_normalized():
    assert rules.normalize_source_reference("geo:Ru", kind="cidrs") == "geo:ru"
    assert rules.normalize_source_reference("asn:12345", kind="cidrs") == "asn:AS12345"
    assert rules.source_display_type("geo:ru") == "geo"
    assert rules.source_display_type("asn:AS12345") == "asn"
    with pytest.raises(ValueError):
        rules.normalize_source_reference("geo:RU", kind="domains")
    with pytest.raises(ValueError):
        rules.normalize_source_reference("http://example.com/list", kind="cidrs")


def test_schedule_supports_days_daytime_and_overnight():
    monday_0900 = datetime(2026, 6, 29, 9, 0)
    monday_2300 = datetime(2026, 6, 29, 23, 0)
    tuesday_0100 = datetime(2026, 6, 30, 1, 0)
    assert rules.schedule_is_active("mon-fri@08:00-22:00", monday_0900) is False
    assert rules.schedule_is_active("mon,tue,wed,thu,fri@08:00-22:00", monday_0900)
    assert not rules.schedule_is_active("mon@08:00-22:00", monday_2300)
    assert rules.schedule_is_active("mon,tue@22:00-02:00", monday_2300)
    assert rules.schedule_is_active("mon,tue@22:00-02:00", tuesday_0100)
    with pytest.raises(ValueError):
        rules._normalize_schedule("mon@29:00-30:00")


def test_compile_does_not_widen_disabled_client_or_list_to_any(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    with db.connect() as con:
        client_id = con.execute(
            """INSERT INTO awg_clients(name,address,private_key,public_key,preshared_key,enabled)
               VALUES('Phone','10.77.0.2/32','p','u','s',0)"""
        ).lastrowid
        list_id = con.execute(
            """INSERT INTO traffic_lists(slug,name,kind,enabled,content_text,item_count)
               VALUES('disabled-list','Disabled','cidrs',0,'203.0.113.0/24\n',1)"""
        ).lastrowid
        con.execute(
            """INSERT INTO traffic_rules(priority,name,client_ids,action_mode)
               VALUES(10,'Disabled client',?,'block')""",
            (str(client_id),),
        )
        con.execute(
            """INSERT INTO traffic_rules(priority,name,list_id,action_mode)
               VALUES(20,'Disabled list',?,'block')""",
            (list_id,),
        )
    assert rules.compile_rules() == []


def test_rule_nft_is_first_match_and_supports_or_invert_and_kill_switch():
    compiled = [
        CompiledRule(
            id=1,
            priority=10,
            name='AI "special"',
            client_addresses=("10.77.0.2/32",),
            list_kind="domains",
            list_items=("example.com",),
            inline_domains=(),
            inline_cidrs=("203.0.113.0/24",),
            protocol="tcp",
            ports=("443",),
            invert=False,
            action_mode="outbound",
            outbound_id=2,
            schedule="",
        ),
        CompiledRule(
            id=2,
            priority=20,
            name="Everything else",
            client_addresses=(),
            list_kind="",
            list_items=(),
            inline_domains=(),
            inline_cidrs=(),
            protocol="any",
            ports=(),
            invert=False,
            action_mode="block",
            outbound_id=None,
            schedule="",
        ),
    ]
    declarations, classification, guards, domain_map = rules.render_rule_nft(
        inbound_interface="awg0", rules=compiled
    )
    text = "\n".join(declarations + classification + guards)
    assert "set rr_1_dom4" in text
    assert "set rr_1_net4" in text
    assert "ip daddr @rr_1_dom4" in text
    assert "ip daddr @rr_1_net4" in text
    assert "tcp dport { 443 }" in text
    assert "meta mark set 0x5102" in text
    assert "return" in text
    assert 'comment "10:AI \\"special\\""' in text
    for line in classification:
        assert " return comment " in line
        assert line.index("meta mark set") < line.index("ct mark set") < line.index("return") < line.index("comment")
    assert "meta mark 0x5fff drop" in text
    assert 'meta mark 0x5102 oifname != "sgo2" drop' in text
    assert domain_map == {1: ("example.com",)}


@pytest.mark.parametrize(
    ("action_mode", "outbound_id", "expected_mark"),
    (("awg_gateway", None, "0x0"), ("block", None, "0x5fff"), ("outbound", 7, "0x5107")),
)
def test_nft_rule_action_precedes_terminal_comment_for_every_action(
    action_mode, outbound_id, expected_mark
):
    compiled = [
        CompiledRule(
            id=77,
            priority=15,
            name="Order check",
            client_addresses=("10.77.0.2/32",),
            list_kind="domains",
            list_items=("example.com",),
            inline_domains=(),
            inline_cidrs=("198.51.100.0/24",),
            protocol="tcp",
            ports=("443",),
            invert=False,
            action_mode=action_mode,
            outbound_id=outbound_id,
            schedule="",
        )
    ]
    _, classification, _, _ = rules.render_rule_nft(inbound_interface="awg0", rules=compiled)
    assert len(classification) == 2
    for line in classification:
        assert f"meta mark set {expected_mark}" in line
        assert line.index("meta mark set") < line.index("ct mark set") < line.index("return") < line.index("comment")
        assert "comment \"15:Order check\" meta mark" not in line


def test_dnsmasq_config_targets_dynamic_nft_sets():
    text = rules.render_dnsmasq_config(
        server_address="10.77.0.1",
        upstreams="1.1.1.1, 9.9.9.9",
        domain_map={3: ("example.com", "sub.example.net")},
    )
    assert "listen-address=10.77.0.1" in text
    assert "server=1.1.1.1" in text
    assert "server=9.9.9.9" in text
    assert "nftset=/example.com/sub.example.net/4#inet#sg_awg_traffic#rr_3_dom4" in text


def test_save_geo_list_forces_cidr_plain_format(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    row = rules.save_traffic_list(
        None,
        slug="geo-fr",
        name="GeoIP FR",
        description="",
        kind="cidrs",
        source_type="url",
        source_url="geo:FR",
        source_format="v2fly",
        content_text="",
        enabled=True,
        auto_update=True,
    )
    assert row["source_url"] == "geo:fr"
    assert row["source_format"] == "plain"


def test_refresh_asn_parses_ripestat_json(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    row = rules.save_traffic_list(
        None,
        slug="asn-test",
        name="ASN Test",
        description="",
        kind="cidrs",
        source_type="url",
        source_url="asn:AS64500",
        source_format="plain",
        content_text="",
        enabled=True,
        auto_update=True,
    )
    payload = json.dumps(
        {"data": {"prefixes": [{"prefix": "203.0.113.0/25"}, {"prefix": "203.0.113.128/25"}]}}
    ).encode()
    monkeypatch.setattr(rules, "_download_source_payload", lambda *a, **k: (payload, "ripe-asn-json"))
    refreshed, changed = rules.refresh_traffic_list(int(row["id"]))
    assert changed is True
    assert refreshed["content_text"] == "203.0.113.0/24\n"
    assert refreshed["item_count"] == 1


def test_schedule_tick_reapplies_only_after_signature_change(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    calls = []
    import awgpanel.egress as egress

    monkeypatch.setattr(egress, "apply_egress_runtime", lambda: calls.append(True) or {"nft_ready": True})
    first = rules.traffic_schedule_tick()
    second = rules.traffic_schedule_tick()
    assert first["changed"] is True
    assert second["changed"] is False
    assert len(calls) == 1


def test_dns_control_is_always_normalized_to_redirect(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    saved = rules.save_dns_traffic_settings(
        mode="off", upstreams="9.9.9.9", advertise_to_clients=False, block_dot=True
    )
    assert saved["mode"] == "redirect"
    assert saved["advertise_to_clients"] == 1
    assert saved["upstreams"] == "9.9.9.9"


def test_alpha21_single_rule_json_roundtrip(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    saved = rules.save_traffic_rule(
        None,
        {
            "name": "Block: example.com",
            "priority": 10,
            "enabled": True,
            "client_ids": "",
            "list_id": "",
            "inline_domains": "example.com",
            "inline_cidrs": "",
            "protocol": "any",
            "ports": "",
            "invert_match": False,
            "schedule": "",
            "action_mode": "block",
            "outbound_id": "",
            "allow_any": False,
        },
    )
    document = rules.traffic_rule_json_document(int(saved["id"]))
    parsed = rules.parse_traffic_rule_json_document(
        document, expected_id=int(saved["id"])
    )
    assert parsed["name"] == "Block: example.com"
    assert parsed["inline_domains"] == "example.com"
    assert parsed["action_mode"] == "block"
    assert parsed["id"] == int(saved["id"])


def test_alpha21_simple_editor_detection_rejects_advanced_fields(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    simple = rules.save_traffic_rule(
        None,
        {
            "name": "Simple",
            "priority": 10,
            "enabled": True,
            "client_ids": "",
            "list_id": "",
            "inline_domains": "example.com",
            "inline_cidrs": "",
            "protocol": "any",
            "ports": "",
            "invert_match": False,
            "schedule": "",
            "action_mode": "block",
            "outbound_id": "",
            "allow_any": False,
        },
    )
    advanced = rules.save_traffic_rule(
        None,
        {
            "name": "Advanced",
            "priority": 20,
            "enabled": True,
            "client_ids": "",
            "list_id": "",
            "inline_domains": "example.org",
            "inline_cidrs": "",
            "protocol": "tcp",
            "ports": "443",
            "invert_match": False,
            "schedule": "",
            "action_mode": "block",
            "outbound_id": "",
            "allow_any": False,
        },
    )
    assert rules.rule_supports_simple_editor(simple)
    assert not rules.rule_supports_simple_editor(advanced)


def test_alpha21_domain_rule_enables_recommended_dns(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    saved = rules.save_traffic_rule(
        None,
        {
            "name": "Domain",
            "priority": 10,
            "enabled": True,
            "client_ids": "",
            "list_id": "",
            "inline_domains": "example.com",
            "inline_cidrs": "",
            "protocol": "any",
            "ports": "",
            "invert_match": False,
            "schedule": "",
            "action_mode": "block",
            "outbound_id": "",
            "allow_any": False,
        },
    )
    assert not rules.ensure_rule_dns_control(saved, force_redirect=True)
    dns = rules.get_dns_traffic_settings()
    assert dns["mode"] == "redirect"
    assert dns["advertise_to_clients"] == 1



def test_beta8_has_no_builtin_lists_or_hidden_protection(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    assert rules.BUILTIN_LISTS == ()
    assert rules.PRESETS == ()
    assert list(rules.list_traffic_rules(include_system=True)) == []


def test_beta8_rejects_redundant_awg_gateway_rule(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    import pytest
    with pytest.raises(ValueError, match="block или outbound"):
        rules.save_traffic_rule(None, {
            "name": "redundant", "priority": 100, "enabled": True,
            "client_ids": "", "list_id": "", "inline_domains": "example.com",
            "inline_cidrs": "", "protocol": "any", "ports": "",
            "invert_match": False, "schedule": "", "action_mode": "awg_gateway",
            "outbound_id": "", "allow_any": False,
        })
