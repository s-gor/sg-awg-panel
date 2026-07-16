from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_theme_switcher_offers_only_dark_and_latte() -> None:
    base = read("awgpanel/templates/base.html")
    assert 'id="theme-menu"' in base
    assert 'data-theme-choice="dark"' in base
    assert 'data-theme-choice="latte"' in base
    assert 'data-theme-choice="system"' not in base
    assert 'Как в системе' not in base
    assert "sg-awg-theme" in base
    assert "prefers-color-scheme: dark" not in base


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


def test_ui_build_marks_build_fix_5_for_cache_busting() -> None:
    web = read("awgpanel/web.py")
    assert "sgawg070rc5bf" in web


def test_latte_resource_dial_values_stay_visible() -> None:
    css = read("awgpanel/static/app.css")
    assert 'html[data-theme="latte"] body.beta9-ui .beta9-load-normal .beta9-dial-center > strong{color:#72E3A7!important;-webkit-text-fill-color:#72E3A7!important}' in css
    assert 'html[data-theme="latte"] body.beta9-ui .beta9-dial-center > span{color:#D5DEE6!important;-webkit-text-fill-color:#D5DEE6!important;opacity:1!important}' in css
