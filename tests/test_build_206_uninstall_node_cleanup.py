from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_206_release_identifiers():
    assert '__version__ = "0.7.0-RC6"' in (ROOT / "awgpanel/__init__.py").read_text(encoding="utf-8")
    assert "sgawg070rc6" in (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")
    assert 'RELEASE_VERSION="v0.7.0-RC6"' in (ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'AGENT_VERSION = "0.7.0-RC6"' in (ROOT / "node_agent/agent.py").read_text(encoding="utf-8")


def test_full_uninstall_removes_all_node_connection_artifacts():
    text = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
    required = [
        "sg-awg-node-agent.service",
        "/opt/sg-awg-node",
        "/etc/sg-awg-node",
        "/var/lib/sg-awg-node",
        "sgcascade",
        "sg_awg_node_filter",
        "sg_awg_node_nat",
        "sg_awg_node_cascade",
        "sg_awg_node_cascade_nat",
        "priority 13050",
        "table 23000",
        "/tmp/sg-awg-node-enroll.*",
    ]
    for value in required:
        assert value in text
    assert "следы подключения SG-Node удалены" in text


def test_local_uninstall_and_agent_reset_remove_node_identity_and_token():
    local = (ROOT / "deploy/uninstall.sh").read_text(encoding="utf-8")
    reset = (ROOT / "deploy/uninstall-node-agent.sh").read_text(encoding="utf-8")
    for text in (local, reset):
        assert "sg-awg-node-agent.service" in text
        assert "/opt/sg-awg-node" in text
        assert "/etc/sg-awg-node" in text
        assert "/var/lib/sg-awg-node" in text
        assert "sgcascade" in text
        assert "priority 13050" in text
        assert "table 23000" in text
    assert "Agent token, identity and state were deleted" in reset
    # Agent-only reset must preserve the normal standalone AWG server.
    assert "awg0.conf" not in reset
    assert "sg-awg-server.service" not in reset


def test_uninstall_verifier_checks_node_residue_too():
    text = (ROOT / "verify-uninstall.sh").read_text(encoding="utf-8")
    for value in (
        "/opt/sg-awg-node",
        "/etc/sg-awg-node",
        "/var/lib/sg-awg-node",
        "sg-awg-node-agent.service",
        "sgcascade",
        "sg_awg_node_",
        "13050",
        "23000",
        "подключений Cluster",
    ):
        assert value in text


def test_reinstaller_detects_active_node_and_cleans_inactive_residue():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "systemctl is-active --quiet sg-awg-node-agent.service" in text
    assert "подключённая SG-Node" in text
    for value in (
        "/opt/sg-awg-node",
        "/etc/sg-awg-node",
        "/var/lib/sg-awg-node",
        "sg_awg_node_cascade",
        "priority 13050",
        "table 23000",
    ):
        assert value in text
