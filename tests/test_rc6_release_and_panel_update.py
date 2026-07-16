from __future__ import annotations

from pathlib import Path

import awgpanel
import awgpanel.core as core
import awgpanel.db as db

ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return b'__version__ = "0.7.0-RC6"\n'


def test_rc6_versions_and_update_scripts_are_consistent() -> None:
    assert '__version__ = "0.7.0-RC6"' in (ROOT / "awgpanel/__init__.py").read_text(encoding="utf-8")
    assert 'version = "0.7.0rc6"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'RELEASE_VERSION="v0.7.0-RC6"' in (ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'VERSION="${SG_AWG_PANEL_VERSION:-v0.7.0-RC6}"' in (ROOT / "deploy/update-from-github.sh").read_text(encoding="utf-8")
    update = (ROOT / "update.sh").read_text(encoding="utf-8")
    assert 'EXPECTED_VERSION="0.7.0-RC6"' in update
    assert 'EXPECTED_UI="sgawg070rc6"' in update


def test_installed_rc5_detects_rc6_in_github_main(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db()
    monkeypatch.setattr(awgpanel, "__version__", "0.7.0-RC5")
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda *args, **kwargs: _Response())

    info = core.check_for_updates(force=True)

    assert info["current"] == "v0.7.0-RC5"
    assert info["latest"] == "v0.7.0-RC6"
    assert info["available"] is True


def test_rc6_description_mentions_all_consolidated_areas() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    notes = (ROOT / "RELEASE-NOTES-0.7.0-RC6.md").read_text(encoding="utf-8")
    for value in ("Latte Graphite", "12 SG-Node", "Проверить резервную копию", "Обновить подключение ноды", "Cascade"):
        assert value in readme or value in notes
    assert "Maintenance → Updates" in readme
