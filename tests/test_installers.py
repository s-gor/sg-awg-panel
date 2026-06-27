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


def test_installers_wait_only_for_real_locks():
    for relative in (
        "install-from-github.sh",
        "install-or-upgrade.sh",
        "deploy/install-amneziawg.sh",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "wait_for_apt" in text
        assert "fuser" in text
        assert "unattended-upgr" not in text


def test_light_updater_never_calls_apt():
    text = (ROOT / "deploy" / "update-from-github.sh").read_text(encoding="utf-8")
    assert "apt-get" not in text
    assert "wait_for_apt" not in text
    assert "rsync" in text
    assert "init-db" in text


def test_update_and_uninstall_scripts_exist():
    assert (ROOT / "deploy" / "update-from-github.sh").exists()
    uninstall = (ROOT / "deploy" / "uninstall.sh").read_text(encoding="utf-8")
    assert "--purge-amneziawg" in uninstall


def test_automatic_backup_timer_is_installed():
    install = (ROOT / "install-or-upgrade.sh").read_text(encoding="utf-8")
    update = (ROOT / "deploy" / "update-from-github.sh").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "install-backup-timer.sh").read_text(encoding="utf-8")
    assert "install-backup-timer.sh" in install
    assert "install-backup-timer.sh" in update
    assert "OnCalendar=daily" in timer
    assert "Persistent=true" in timer


def test_project_is_installed_into_virtualenv():
    for relative in ("install-or-upgrade.sh", "deploy/update-from-github.sh"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "pip install --no-cache-dir -q -e ." in text
    assert (ROOT / "pyproject.toml").exists()


def test_optional_https_script_exists_and_binds_panel_locally():
    text = (ROOT / "deploy" / "enable-https.sh").read_text(encoding="utf-8")
    assert "AWGPANEL_BIND_ADDRESS" in text
    assert "127.0.0.1" in text
    assert "certbot --nginx" in text
