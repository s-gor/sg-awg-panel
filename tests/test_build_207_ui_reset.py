from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_207_release_identifiers():
    assert '__version__ = "0.7.0-RC4"' in read("awgpanel/__init__.py")
    assert 'version = "0.7.0rc4"' in read("pyproject.toml")
    assert 'RELEASE_VERSION="v0.7.0-RC4"' in read("install.sh")
    assert 'AGENT_VERSION = "0.7.0-RC4"' in read("node_agent/agent.py")
    assert "sgawg070rc4" in read("awgpanel/web.py")


def test_browser_native_dialogs_are_gone_from_templates_and_static_assets():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (ROOT / "awgpanel/templates", ROOT / "awgpanel/static")
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".html", ".js", ".css"}
    )
    for native in ("confirm", "alert", "prompt"):
        assert re.search(rf"(?:window\s*\.\s*)?{native}\s*\(", source) is None
    base = read("awgpanel/templates/base.html")
    assert 'id="ui-action-dialog"' in base
    assert "window.sgDialog" in base
    assert "ask(options={})" in base


def test_clients_use_real_table_and_full_width_workspace():
    template = read("awgpanel/templates/clients.html")
    css = read("awgpanel/static/app.css")
    assert 'class="clients-workspace"' in template
    assert 'class="beta9-panel flush clients-table-panel"' in template
    assert 'class="beta9-table clients-data-table"' in template
    assert 'class="beta9-panel flush clients-modern-table"' not in template
    assert "Маршрут" in template
    assert "client-route-chip" in template
    assert ".clients-page .content{max-width:none}" in css
    assert ".clients-data-table{display:table;width:100%" in css


def test_external_cascade_has_explicit_two_server_reset():
    template = read("awgpanel/templates/cascade.html")
    web = read("awgpanel/web.py")
    assert "Отключить Cascade и начать заново" in template
    assert "Очистить Inbound" in template
    assert "Удалить служебный доступ Outbound" in template
    assert "Два независимых сервера очищаются двумя отдельными действиями" in template
    assert '@app.post("/cascade/reset")' in web
    assert "Шаг 1 из 2 выполнен" in web


def test_server_rename_is_synchronized_and_visible():
    core = read("awgpanel/core.py")
    base = read("awgpanel/templates/base.html")
    nodes = read("awgpanel/templates/nodes.html")
    system = read("awgpanel/templates/system.html")
    assert "UPDATE cluster_nodes SET name=?" in core
    assert "ТЕКУЩИЙ СЕРВЕР" in base
    assert "cluster-controller-identity" in nodes
    assert "Сохранить новое имя" in system


def test_svg_flags_remain_bundled():
    flags = ROOT / "awgpanel/static/flags"
    assert (flags / "de.svg").is_file()
    assert (flags / "fr.svg").is_file()
    assert (flags / "unknown.svg").is_file()
    assert len(list(flags.glob("*.svg"))) >= 60
