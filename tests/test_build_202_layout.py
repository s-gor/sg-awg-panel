from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_build_202_removes_standard_page_1080_limit():
    css = (ROOT / "awgpanel/static/app.css").read_text(encoding="utf-8")
    assert ":root { --content-width: 100%; }" in css
    assert ":root { --content-width: 1080px; }" not in css
    assert "202-AWG-Panel — one workspace width for every section" in css
    assert "body.ui-standard-page.beta9-ui .page-stack" in css

def test_build_202_identifiers():
    assert '__version__ = "0.7.0-RC6"' in (ROOT / "awgpanel/__init__.py").read_text(encoding="utf-8")
    assert "sgawg070rc6" in (ROOT / "awgpanel/web.py").read_text(encoding="utf-8")
    assert 'RELEASE_VERSION="v0.7.0-RC6"' in (ROOT / "install.sh").read_text(encoding="utf-8")
