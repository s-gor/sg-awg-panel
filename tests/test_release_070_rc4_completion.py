from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_rc4_versions_are_consistent():
    assert '__version__ = "0.7.0-RC4"' in read("awgpanel/__init__.py")
    assert 'version = "0.7.0rc4"' in read("pyproject.toml")
    assert '__version__ = "0.7.0-RC4"' in read("node_agent/__init__.py")
    assert "sgawg070rc4" in read("awgpanel/web.py")
    assert "0.7.0-RC4" in read("README.md")


def test_rc4_release_has_one_installer_and_one_updater():
    builder = read("tools/build-self-extracting-installers.sh")
    assert "INSTALL-SG-AWG-PANEL.run" in builder
    assert "UPDATE-SG-AWG-PANEL.run" in builder
    assert "INSTALL-SG-AWG-NODE.run" not in builder
    assert "0.7.0-RC4-INSTALL-SG-AWG-PANEL.run" in read("README.md")
    assert "0.7.0-RC4-UPDATE-SG-AWG-PANEL.run" in read("README.md")


def test_rc4_update_accepts_uppercase_release_candidate_tags():
    core = read("awgpanel/core.py")
    assert "version, re.I" in core


def test_rc4_screenshot_plan_is_present():
    guide = read("docs/screenshots/README.md")
    for name in (
        "01-system.png",
        "02-clients.png",
        "03-cluster.png",
        "04-cascade.png",
        "05-ssh-menu.png",
        "06-installer.png",
    ):
        assert name in guide


def test_rc4_release_notes_cover_final_scope():
    notes = read("RELEASE-NOTES-0.7.0-RC4.md")
    for phrase in (
        "Новое SSH-меню",
        "Исправление CSRF",
        "Клиенты SG-Node",
        "Единая модель установки",
        "sudo sg-awg-panel",
    ):
        assert phrase in notes
