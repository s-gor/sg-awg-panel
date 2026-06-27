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
    assert "OnCalendar=$CALENDAR" in timer
    assert "backup_schedule" in timer
    assert "Persistent=true" in timer


def test_project_is_installed_into_virtualenv():
    for relative in ("install-or-upgrade.sh", "deploy/update-from-github.sh"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "pip install --no-cache-dir -q -e ." in text
    assert (ROOT / "pyproject.toml").exists()


def test_panel_access_proxy_binds_backend_locally():
    text = (ROOT / "deploy" / "configure-panel-access.sh").read_text(encoding="utf-8")
    service = (ROOT / "deploy" / "install-service.sh").read_text(encoding="utf-8")
    assert "AWGPANEL_BIND_ADDRESS" in text
    assert "127.0.0.1" in text
    assert "certbot certonly" in text
    assert "Backend must bind only to loopback" in service


def test_update_has_automatic_rollback_and_recovery_service():
    update = (ROOT / "deploy" / "update-from-github.sh").read_text(encoding="utf-8")
    install = (ROOT / "install-or-upgrade.sh").read_text(encoding="utf-8")
    assert "rollback" in update
    assert "rolled_back" in update
    assert "install-recovery-service.sh" in update
    assert "install-recovery-service.sh" in install
    assert (ROOT / "deploy" / "recover-after-boot.sh").exists()


def test_quoted_python_heredocs_compile():
    """Catch malformed embedded Python before publishing shell installers."""
    import re

    for script in ROOT.rglob("*.sh"):
        text = script.read_text(encoding="utf-8")
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            match = re.search(r"<<'([A-Za-z_][A-Za-z0-9_]*)'", lines[index])
            if not match:
                index += 1
                continue
            marker = match.group(1)
            body: list[str] = []
            index += 1
            while index < len(lines) and lines[index] != marker:
                body.append(lines[index])
                index += 1
            assert index < len(lines), f"Unterminated heredoc {marker} in {script}"
            if marker.startswith("PY"):
                compile("\n".join(body) + "\n", f"{script}:{marker}", "exec")
            index += 1


def test_https_uses_placeholder_on_443_and_separate_panel_port():
    text = (ROOT / "deploy" / "configure-panel-access.sh").read_text(encoding="utf-8")
    assert "listen 443 ssl" in text
    assert "listen ${PUBLIC_PORT} ssl" in text
    assert "register-unsafely-without-email" in text
    assert "--email" not in text
    assert "rm -f /etc/nginx/sites-enabled/default" not in text
    assert "sg-awg-placeholder.conf" in text
    assert "sg-awg-panel.conf" in text


def test_panel_port_validation_reserves_443():
    text = (ROOT / "deploy" / "configure-panel-access.sh").read_text(encoding="utf-8")
    assert "22|80|443|585|18080" in text
    assert "62443" in text
