from __future__ import annotations

import argparse
import os
from pathlib import Path

import awgpanel.admin_cli as admin_cli
import awgpanel.db as db
import awgpanel.web as web

ROOT = Path(__file__).resolve().parents[1]


def test_install_specific_cookie_name_is_stable_and_isolated():
    first = web._session_cookie_name("secret-one")
    second = web._session_cookie_name("secret-two")
    assert first.startswith("sg_awg_session_")
    assert first == web._session_cookie_name("secret-one")
    assert first != second
    assert first != "session"


def test_stale_csrf_redirects_to_fresh_login(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setenv("AWGPANEL_SECRET_KEY", "csrf-test-secret")
    app = web.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.post(
        "/login",
        data={"password": "unused", "csrf_token": "stale-token"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["Location"].endswith("/login?csrf_reset=1")


def test_login_page_disables_browser_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    monkeypatch.setenv("AWGPANEL_SECRET_KEY", "cache-test-secret")
    app = web.create_app()
    app.config.update(TESTING=True)
    response = app.test_client().get("/login")
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Pragma"] == "no-cache"


def test_admin_cli_is_installed_and_documented():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    installer = (ROOT / "install-or-upgrade.sh").read_text(encoding="utf-8")
    uninstall = (ROOT / "deploy/uninstall.sh").read_text(encoding="utf-8")
    assert 'sg-awg-panel = "awgpanel.admin_cli:main"' in pyproject
    assert "/usr/local/sbin/sg-awg-panel" in installer
    assert "AWGPANEL_SECRET_KEY is missing" not in installer
    assert "/usr/local/sbin/sg-awg-panel" in uninstall


def test_admin_cli_reads_systemd_style_env(tmp_path, monkeypatch):
    env = tmp_path / "web.env"
    env.write_text(
        "AWGPANEL_SECRET_KEY='persistent secret'\n"
        "AWGPANEL_PUBLIC_PORT=62443\n"
        "# comment\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AWGPANEL_SECRET_KEY", raising=False)
    monkeypatch.delenv("AWGPANEL_PUBLIC_PORT", raising=False)
    admin_cli._load_env_file(env)
    assert os.environ["AWGPANEL_SECRET_KEY"] == "persistent secret"
    assert os.environ["AWGPANEL_PUBLIC_PORT"] == "62443"


def test_admin_cli_parser_contains_recovery_actions():
    parser = admin_cli.build_parser()
    for command in (
        "status", "password", "sessions", "repair-access", "restart",
        "restart-all", "backup", "backups", "restore", "logs", "errors",
        "diagnostics", "clients", "cluster", "cascade", "update", "uninstall",
        "server-name",
    ):
        args = parser.parse_args([command])
        assert args.command == command


def test_rc4_admin_menu_has_expected_numbered_actions():
    items = {key: label for key, label, _handler, _kind in admin_cli._menu_items()}
    assert items["6"] == "Сменить пароль администратора"
    assert items["9"] == "Проверить клиентов и подключения"
    assert items["10"] == "Проверить Cluster и SG-Node"
    assert items["11"] == "Проверить Cascade"
    assert items["17"] == "Полностью удалить SG-AWG-Panel"


def test_rc4_readme_documents_single_ssh_menu():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    maintenance = (ROOT / "docs/MAINTENANCE.md").read_text(encoding="utf-8")
    assert "sudo sg-awg-panel" in readme
    assert "6. Сменить пароль администратора" in readme
    assert "10. Проверить Cluster и SG-Node" in readme
    assert "sudo sg-awg-panel uninstall" in maintenance
