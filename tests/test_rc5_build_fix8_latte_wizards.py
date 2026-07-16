from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_latte_cluster_wizard_uses_light_surfaces_without_global_opacity() -> None:
    css = read("awgpanel/static/app.css")
    assert 'html[data-theme="latte"] .cluster-wizard-step{' in css
    assert 'html[data-theme="latte"] .cluster-wizard-step.locked{' in css
    locked = css.split('html[data-theme="latte"] .cluster-wizard-step.locked{', 1)[1].split('}', 1)[0]
    assert 'background:#D9E2E8' in locked
    assert 'opacity:1' in locked


def test_latte_cluster_step_text_and_number_have_explicit_contrast() -> None:
    css = read("awgpanel/static/app.css")
    assert 'html[data-theme="latte"] .cluster-step-number{' in css
    assert 'html[data-theme="latte"] .cluster-step-body :is(h2,b,strong,label){color:#192530}' in css
    assert 'html[data-theme="latte"] .cluster-step-body :is(p,small){color:#556672}' in css


def test_latte_locked_cascade_steps_do_not_use_whole_card_opacity() -> None:
    css = read("awgpanel/static/app.css")
    assert 'html[data-theme="latte"] .cascade-step-card.step-locked{opacity:1}' in css
    assert 'html[data-theme="latte"] .cascade-step-nav a.locked,' in css


def test_build_fix8_busts_asset_cache() -> None:
    web = read("awgpanel/web.py")
    assert "sgawg070rc6" in web
