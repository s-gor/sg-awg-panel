from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_superseded_system_theme_expectations_are_removed() -> None:
    base = read("awgpanel/templates/base.html")
    assert 'data-theme-choice="dark"' in base
    assert 'data-theme-choice="latte"' in base
    assert 'data-theme-choice="system"' not in base
    assert "systemTheme.addEventListener" not in base


def test_latte_success_components_use_stronger_palette() -> None:
    css = read("awgpanel/static/app.css")
    assert "#D8EADF" in css
    assert "#8DBFA2" in css
    assert "#17623F" in css
    assert ".server-identity-preview" in css
    assert ".beta9-ui .system-pill" in css
    assert ".status-badge.success" in css


def test_build_fix7_busts_asset_cache() -> None:
    web = read("awgpanel/web.py")
    assert "sgawg070rc5bf7" in web
