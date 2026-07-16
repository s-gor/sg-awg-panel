from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_build_202_standard_pages_use_full_workspace_width():
    css = (ROOT / "awgpanel/static/app.css").read_text(encoding="utf-8")
    assert "202-AWG-Panel — one workspace width for every section" in css
    assert ":root { --content-width: 1080px; }" not in css
    assert ".ui-standard-page .page-stack" in css
    assert "max-width: none" in css
    assert "margin-left: 0" in css
    assert "margin-right: 0" in css


def test_hotfix3_installer_recovers_only_stale_residue():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "panel_installation_is_active()" in text
    assert "cleanup_stale_panel_residue()" in text
    assert "/etc/sg-awg-panel/web.env" in text
    assert "/var/lib/sg-awg-panel/panel.db" in text
    assert "systemctl is-active --quiet sg-awg-panel.service" in text
    assert "rm -rf" in text
    assert "/opt/sg-awg-panel" in text
    assert "/etc/systemd/system/sg-awg-panel.service.d" in text
    assert "повторная установка после полного удаления" in text


def test_hotfix3_uninstall_removes_systemd_dropins_for_reinstall():
    for relative in ("uninstall.sh", "deploy/uninstall.sh"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "find /etc/systemd/system -type l -name 'sg-awg-*'" in text
        assert "/etc/systemd/system/sg-awg-*.service.d" in text
        assert "/etc/systemd/system/sg-awg-*.timer.d" in text


def test_build_202_identifiers_legacy_suite():
    assert '0.7.0-RC6' in (ROOT / "awgpanel/__init__.py").read_text(encoding="utf-8")
    assert 'sgawg070rc6' in (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")
