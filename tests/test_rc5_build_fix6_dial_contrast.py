from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_latte_dial_values_force_readable_color():
    css = (ROOT / "awgpanel/static/app.css").read_text(encoding="utf-8")
    assert 'html[data-theme="latte"] body.beta9-ui .beta9-dial-center > strong{' in css
    assert '-webkit-text-fill-color:#F4F8FC!important' in css
    assert 'opacity:1!important' in css


def test_latte_normal_dial_uses_bright_value():
    css = (ROOT / "awgpanel/static/app.css").read_text(encoding="utf-8")
    assert 'body.beta9-ui .beta9-load-normal .beta9-dial-center > strong{color:#72E3A7!important' in css


def test_build_fix6_busts_asset_cache():
    web = (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")
    assert "sgawg070rc5bf6" in web
