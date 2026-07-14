from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_full_install_prepares_dormant_agent_on_every_server():
    first_install = read("deploy/first-install.sh")
    service = read("deploy/install-node-agent.sh")
    assert 'install-node-agent.sh" "$PROJECT_DIR"' in first_install
    assert "ConditionPathExists=/etc/sg-awg-node/agent.env" in service
    assert "systemctl enable sg-awg-node-agent.service" in service


def test_same_full_installer_is_documented_for_controller_and_node():
    readme = read("README.md")
    cluster = read("awgpanel/templates/nodes.html")
    assert "INSTALL-SG-AWG-NODE.run" not in readme
    assert readme.count("0.7.0-RC3-INSTALL-SG-AWG-PANEL.run") >= 3
    assert "тот же файл" in cluster
    assert "Один универсальный установщик" in cluster
    assert "node-install-command" not in cluster


def test_update_refreshes_and_preserves_agent_connection():
    update = read("update.sh")
    assert "node-agent.tar.gz" in update
    assert 'install-node-agent.sh" "$PROJECT_DIR"' in update
    assert "sg-awg-node-agent.service" in update
    assert "подключение Agent сохранены" in update


def test_release_builder_creates_only_full_install_and_update():
    builder = read("tools/build-self-extracting-installers.sh")
    assert "INSTALL-SG-AWG-PANEL.run" in builder
    assert "UPDATE-SG-AWG-PANEL.run" in builder
    assert "INSTALL-SG-AWG-NODE.run" not in builder
