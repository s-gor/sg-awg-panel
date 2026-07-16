from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_theme_switcher_is_available_in_the_global_topbar() -> None:
    base = read("awgpanel/templates/base.html")
    assert 'id="theme-menu"' in base
    assert 'data-theme-choice="dark"' in base
    assert 'data-theme-choice="latte"' in base
    assert 'data-theme-choice="system"' not in base
    assert "sg-awg-theme" in base
    assert "sg-awg-theme" in base


def test_latte_graphite_uses_the_approved_palette() -> None:
    css = read("awgpanel/static/app.css")
    for color in (
        "#E3E9EE",
        "#EEF2F4",
        "#D9E2E8",
        "#EAF0F3",
        "#AEBCC7",
        "#192530",
        "#556672",
        "#31536F",
        "#3A607F",
        "#27465F",
    ):
        assert color in css
    assert 'html[data-theme="latte"]' in css
    assert "color-scheme:light" in css


def test_theme_choice_updates_browser_color_and_persists() -> None:
    base = read("awgpanel/templates/base.html")
    assert 'id="browser-theme-color"' in base
    assert "localStorage.setItem('sg-awg-theme'" in base
    assert "resolved === 'latte' ? '#E3E9EE' : '#0d131b'" in base
    assert "systemTheme.addEventListener" not in base


def test_open_cascade_link_state_is_compact() -> None:
    css = read("awgpanel/static/app.css")
    assert ".cascade-external-card .cascade-enrollment-result textarea{height:48px;min-height:48px" in css
    assert ".cascade-external-card .cascade-enrollment-result .button{min-height:38px" in css
    assert ".cascade-external-card .cascade-enrollment-meta{gap:6px 14px" in css


def test_ui_build_keeps_an_rc5_cache_busting_marker() -> None:
    web = read("awgpanel/web.py")
    assert "sgawg070rc6" in web
