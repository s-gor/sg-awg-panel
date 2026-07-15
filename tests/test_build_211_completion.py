from __future__ import annotations

from pathlib import Path

import awgpanel.node_manager as node_manager

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_211_keeps_classic_design_and_rejects_experimental_209_layer():
    base = read("awgpanel/templates/base.html")
    css = read("awgpanel/static/app.css")
    assert "v209-ui" not in base
    assert "SG-AWG-Panel 0.7.0-RC5 — classic UI completion" in css
    assert "sgawg070rc5" in read("awgpanel/web.py")


def test_update_script_is_real_non_destructive_update():
    script = read("update.sh")
    assert 'EXPECTED_VERSION="0.7.0-RC5"' in script
    assert "rsync -a --checksum --delete" in script
    assert "install-or-upgrade.sh" not in script
    assert "ensure-server" not in script
    assert "configure-panel-access" not in script
    assert "panel.db" in script
    assert "ОТКАТ" in script
    assert "классический интерфейс" in script


def test_clients_fit_without_horizontal_minimum():
    template = read("awgpanel/templates/clients.html")
    css = read("awgpanel/static/app.css")
    assert template.count("<th>") == 5
    assert '<th class="right">Действия</th>' in template
    assert "VPN IP</th>" not in template
    assert "Последняя активность</th>" not in template
    assert ".clients-data-table{width:100%;min-width:0;table-layout:fixed}" in css
    assert ".clients-table-panel .beta9-table-wrap{overflow:visible}" in css


def test_cascade_result_is_persistent_next_to_button():
    template = read("awgpanel/templates/cascade.html")
    web = read("awgpanel/web.py")
    cascade = read("awgpanel/cascade.py")
    assert 'id="client-check-result"' in template
    assert "Проверить маршрут клиента" in template
    assert "Маршрут клиента через Cascade подтверждён" in template
    assert '_anchor="client-check-result"' in web
    assert "переподключать VPN не нужно" in cascade


def test_duplicate_node_rows_are_collapsed_without_deleting_history():
    rows = [
        {"id": 1, "is_local": True, "name": "CC1", "effective_state": "online"},
        {"id": 2, "is_local": False, "name": "CC2-Node", "effective_state": "pending", "agent_token_hash": ""},
        {"id": 3, "is_local": False, "name": "cc2-node", "effective_state": "online", "agent_token_hash": "hash"},
        {"id": 4, "is_local": False, "name": "CC2-Node", "effective_state": "offline", "agent_token_hash": "hash"},
    ]
    visible, hidden = node_manager.collapse_duplicate_nodes(rows)
    assert [item["id"] for item in visible] == [1, 3]
    assert hidden == 2


def test_remote_node_cascade_flushes_all_conntrack_tuple_directions():
    agent = read("node_agent/agent.py")
    for selector in ("--orig-src", "--orig-dst", "--reply-src", "--reply-dst"):
        assert selector in agent
    assert '["ip", "route", "flush", "cache"]' in agent
