from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_installer_does_not_source_password_env():
    text = (ROOT / "install-or-upgrade.sh").read_text(encoding="utf-8")
    assert '. "$ENV_FILE"' not in text
    assert "Do not source web.env" in text

def test_os_check_precedes_package_install():
    text = (ROOT / "install-or-upgrade.sh").read_text(encoding="utf-8")
    assert text.index('case "${VERSION_ID:-}"') < text.index("apt-get update")

def test_first_install_validates_awg_before_web_panel():
    text = (ROOT / "deploy" / "first-install.sh").read_text(encoding="utf-8")
    assert text.index("install-amneziawg.sh") < text.index("install-or-upgrade.sh")
