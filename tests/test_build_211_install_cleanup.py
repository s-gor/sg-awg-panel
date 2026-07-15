from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_211_remote_node_profile_uses_regular_panel_format():
    source = read("awgpanel/node_clients.py")
    assert '"# Source = SG-AWG-Panel"' in source
    assert '"# Source = SG-AWG-Panel Cluster"' not in source
    assert 'f"# Server = {node' not in source
    assert 'profile_name = f"{node_name}/{client_name}"' in source
    assert 'dns_value = dns_values[0]' in source
    assert 'int(runtime[\'listen_port\'])' in source


def test_green_spinner_is_used_for_logged_install_steps():
    common = read("deploy/install-common.sh")
    assert "\\033[1;32m" in common
    assert "[SG-AWG-Panel] [%s]" in common
    assert "(%%s сек)" not in common
    assert "NO_COLOR" in common


def test_self_extracting_installers_are_reproducible_and_do_not_need_unzip():
    builder = read("tools/build-self-extracting-installers.sh")
    assert "0.7.0-RC5-INSTALL-SG-AWG-PANEL.run" not in builder  # version is derived, not hardcoded
    assert "tar -xzf -" in builder
    assert "unzip" not in builder
    assert "install.sh" in builder
    assert "update.sh" in builder
    assert "INSTALL-SG-AWG-NODE" not in builder
    assert "--verify" in builder


def test_readme_leads_with_no_unzip_installation():
    readme = read("README.md")
    assert "Рекомендуемая установка на новую EC2 без unzip" in readme
    assert "0.7.0-RC5-INSTALL-SG-AWG-PANEL.run" in readme
    assert "0.7.0-RC5-INSTALL-SG-AWG-NODE.run" not in readme
    assert readme.count("sudo bash 0.7.0-RC5-INSTALL-SG-AWG-PANEL.run") >= 2
    assert "sudo bash 0.7.0-RC5-INSTALL-SG-AWG-PANEL.run" in readme
