from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_standalone_cascade_wizard_is_compact_without_logic_changes():
    template = (ROOT / "awgpanel/templates/cascade.html").read_text(encoding="utf-8")
    css = (ROOT / "awgpanel/static/app.css").read_text(encoding="utf-8")

    assert 'id="external-cascade-link" name="enrollment_link" rows="3"' in template
    assert ".cascade-external-card .cascade-external-flow{align-items:start" in css
    assert ".cascade-external-card .cascade-enrollment-import-form textarea{height:78px;min-height:78px" in css
    assert ".cascade-external-card .cascade-external-step{gap:10px;padding:15px 16px 15px 56px}" in css
