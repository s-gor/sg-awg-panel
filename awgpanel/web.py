from __future__ import annotations

import base64
import io
import os
import secrets
from functools import wraps
from pathlib import Path

import qrcode
from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from . import __version__
from .core import (
    add_awg_client,
    configure_and_start_awg,
    create_manual_backup,
    delete_awg_client,
    find_awg_client,
    find_client_by_access_token,
    get_awg_diagnostics,
    get_awg_overview,
    get_awg_settings,
    list_awg_clients,
    list_backups,
    record_client_access,
    regenerate_awg_client,
    regenerate_client_access_token,
    render_awg_client_config,
    restart_awg,
    restore_backup,
    set_awg_client_enabled,
    set_client_access_enabled,
    start_awg,
    stop_awg,
    update_awg_client_routing,
    update_dns_servers,
    update_routing_settings,
)
from .db import init_db
from .errors import AWGPanelError


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_filename(name: str) -> str:
    value = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in name)
    return value.strip("-.") or "client"


def _write_env_value(key: str, value: str) -> None:
    env_path = Path(os.environ.get("AWGPANEL_ENV_FILE", "/etc/sg-awg-panel/web.env"))
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    replaced = False
    output: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            output.append(f"{key}={value}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"{key}={value}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(output) + "\n", encoding="utf-8")
    os.chmod(env_path, 0o600)


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("AWGPANEL_SECRET_KEY") or secrets.token_urlsafe(48)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=_bool_env("AWGPANEL_SECURE_COOKIES", False),
        MAX_CONTENT_LENGTH=256 * 1024,
    )
    if _bool_env("AWGPANEL_TRUST_PROXY_HEADERS", False):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[assignment]

    init_db()

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("authenticated"):
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)

        return wrapped

    def csrf_token() -> str:
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return token

    def require_csrf() -> None:
        expected = session.get("csrf_token", "")
        provided = request.form.get("csrf_token", "")
        if not expected or not secrets.compare_digest(expected, provided):
            abort(400, "CSRF token mismatch")

    def access_rows() -> list[dict[str, object]]:
        base = request.host_url.rstrip("/")
        rows: list[dict[str, object]] = []
        for row in list_awg_clients():
            item = dict(row)
            item["access_url"] = f"{base}{url_for('public_client_config', token=row['access_token'])}"
            rows.append(item)
        return rows

    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["panel_version"] = __version__

    @app.get("/login")
    def login():
        if session.get("authenticated"):
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.post("/login")
    def login_post():
        require_csrf()
        password_hash = os.environ.get("AWGPANEL_PASSWORD_HASH", "")
        if not password_hash:
            flash("Пароль панели не настроен. Повторите установку GUI.", "error")
            return redirect(url_for("login"))
        if not check_password_hash(password_hash, request.form.get("password", "")):
            flash("Неверный пароль", "error")
            return redirect(url_for("login"))
        session.clear()
        session["authenticated"] = True
        session["csrf_token"] = secrets.token_urlsafe(32)
        return redirect(url_for("dashboard"))

    @app.post("/logout")
    @login_required
    def logout():
        require_csrf()
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def dashboard():
        return render_template("dashboard.html", awg=get_awg_overview())

    @app.get("/server")
    @login_required
    def server_page():
        return render_template("server.html", awg=get_awg_overview())

    @app.post("/server")
    @login_required
    def server_save():
        require_csrf()
        try:
            _, state = configure_and_start_awg(
                interface_name="awg0",
                endpoint_host=request.form.get("endpoint_host", ""),
                listen_port=request.form.get("listen_port", "585"),
                server_network=request.form.get("server_network", "10.77.0.0/24"),
                dns_servers=request.form.get("dns_servers", "1.1.1.1, 1.0.0.1"),
                mtu=request.form.get("mtu", "1280"),
                external_interface=request.form.get("external_interface", ""),
                jc=request.form.get("jc", "6"),
                jmin=request.form.get("jmin", "64"),
                jmax=request.form.get("jmax", "128"),
                s1=request.form.get("s1", "48"),
                s2=request.form.get("s2", "48"),
                s3=request.form.get("s3", "32"),
                s4=request.form.get("s4", "16"),
                h1=request.form.get("h1", ""),
                h2=request.form.get("h2", ""),
                h3=request.form.get("h3", ""),
                h4=request.form.get("h4", ""),
                i1=request.form.get("i1", ""),
                i2=request.form.get("i2", ""),
                i3=request.form.get("i3", ""),
                i4=request.form.get("i4", ""),
                i5=request.form.get("i5", ""),
            )
            flash(f"Настройки сохранены и применены. AmneziaWG: {state}.", "success")
        except (ValueError, PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("server_page"))

    @app.post("/service/<action>")
    @login_required
    def service_action(action: str):
        require_csrf()
        actions = {"start": start_awg, "stop": stop_awg, "restart": restart_awg}
        if action not in actions:
            abort(404)
        try:
            state = actions[action]()
            flash(f"Служба AmneziaWG: {state}", "success")
        except (PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(request.referrer or url_for("dashboard"))

    @app.get("/clients")
    @login_required
    def clients_page():
        return render_template("clients.html", awg=get_awg_overview())

    @app.post("/clients/add")
    @login_required
    def client_add():
        require_csrf()
        try:
            client = add_awg_client(
                request.form.get("name", ""), request.form.get("comment", "")
            )
            flash(f"Клиент {client['name']} создан и применён.", "success")
        except (ValueError, PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("clients_page"))

    @app.post("/clients/<int:client_id>/toggle")
    @login_required
    def client_toggle(client_id: int):
        require_csrf()
        try:
            current = find_awg_client(client_id)
            client = set_awg_client_enabled(client_id, not bool(current["enabled"]))
            state = "включён" if client["enabled"] else "отключён"
            flash(f"Клиент {client['name']} {state}.", "success")
        except (PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(request.referrer or url_for("clients_page"))

    @app.post("/clients/<int:client_id>/regenerate")
    @login_required
    def client_regenerate(client_id: int):
        require_csrf()
        try:
            client = regenerate_awg_client(client_id)
            flash(
                f"Ключи клиента {client['name']} пересозданы. Старый конфиг больше не работает.",
                "success",
            )
            return redirect(url_for("client_access", client_id=client_id))
        except (PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("clients_page"))

    @app.post("/clients/<int:client_id>/delete")
    @login_required
    def client_delete(client_id: int):
        require_csrf()
        try:
            client = delete_awg_client(client_id)
            flash(f"Клиент {client['name']} удалён.", "success")
        except (PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("clients_page"))

    @app.get("/clients/<int:client_id>/config")
    @login_required
    def client_access(client_id: int):
        client = find_awg_client(client_id)
        config_text = render_awg_client_config(client_id)
        image = qrcode.make(config_text)
        output = io.BytesIO()
        image.save(output, format="PNG")
        qr_data = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        return render_template(
            "client_config.html", client=client, config_text=config_text, qr_data=qr_data
        )

    @app.get("/clients/<int:client_id>/download")
    @login_required
    def client_download(client_id: int):
        client = find_awg_client(client_id)
        config_text = render_awg_client_config(client_id)
        return send_file(
            io.BytesIO(config_text.encode("utf-8")),
            mimetype="text/plain; charset=utf-8",
            as_attachment=True,
            download_name=f"{_safe_filename(client['name'])}-awg.conf",
        )

    @app.get("/access")
    @login_required
    def access_page():
        return render_template("access.html", clients=access_rows())

    @app.post("/access/<int:client_id>/toggle")
    @login_required
    def access_toggle(client_id: int):
        require_csrf()
        try:
            current = find_awg_client(client_id)
            client = set_client_access_enabled(client_id, not bool(current["access_enabled"]))
            state = "включена" if client["access_enabled"] else "отключена"
            flash(f"Ссылка клиента {client['name']} {state}.", "success")
        except (PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("access_page"))

    @app.post("/access/<int:client_id>/regenerate")
    @login_required
    def access_regenerate(client_id: int):
        require_csrf()
        try:
            client = regenerate_client_access_token(client_id)
            flash(f"Новая ссылка доступа для {client['name']} создана.", "success")
        except (PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("access_page"))

    @app.get("/a/<token>")
    def public_client_config(token: str):
        try:
            client = find_client_by_access_token(token)
            config_text = render_awg_client_config(int(client["id"]))
            record_client_access(int(client["id"]))
        except AWGPanelError:
            abort(404)
        response = Response(config_text, mimetype="text/plain; charset=utf-8")
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{_safe_filename(client["name"])}-awg.conf"'
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/routing")
    @login_required
    def routing_page():
        return render_template(
            "routing.html", settings=get_awg_settings(), clients=list_awg_clients()
        )

    @app.post("/routing")
    @login_required
    def routing_save():
        require_csrf()
        try:
            settings = update_routing_settings(
                isolate_clients=request.form.get("isolate_clients") == "1"
            )
            flash(
                "Routing сохранён и применён. Изоляция клиентов: "
                + ("включена." if settings["isolate_clients"] else "отключена."),
                "success",
            )
        except (ValueError, PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("routing_page"))

    @app.post("/routing/<int:client_id>")
    @login_required
    def client_routing_save(client_id: int):
        require_csrf()
        mode = request.form.get("mode", "all")
        allowed_ips = "0.0.0.0/0" if mode == "all" else request.form.get("allowed_ips", "")
        try:
            client = update_awg_client_routing(client_id, allowed_ips)
            flash(f"Маршруты клиента {client['name']} сохранены.", "success")
        except (ValueError, PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("routing_page"))

    @app.get("/dns")
    @login_required
    def dns_page():
        return render_template("dns.html", settings=get_awg_settings())

    @app.post("/dns")
    @login_required
    def dns_save():
        require_csrf()
        try:
            settings = update_dns_servers(request.form.get("dns_servers", ""))
            flash(
                f"DNS сохранён: {settings['dns_servers']}. Скачайте клиентский конфиг заново.",
                "success",
            )
        except (ValueError, PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("dns_page"))

    @app.get("/diagnostics")
    @login_required
    def diagnostics():
        return render_template("diagnostics.html", diagnostics=get_awg_diagnostics())

    @app.get("/backups")
    @login_required
    def backups_page():
        return render_template("backups.html", backups=list_backups(limit=50))

    @app.post("/backups/create")
    @login_required
    def backup_create():
        require_csrf()
        try:
            backup = create_manual_backup()
            flash(f"Резервная копия создана: {backup.name}", "success")
        except (PermissionError, AWGPanelError, OSError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("backups_page"))

    @app.post("/backups/<name>/restore")
    @login_required
    def backup_restore(name: str):
        require_csrf()
        try:
            restored = restore_backup(name)
            flash(f"Резервная копия восстановлена: {restored.name}", "success")
        except (PermissionError, AWGPanelError, OSError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("backups_page"))

    @app.get("/security")
    @login_required
    def security_page():
        return render_template(
            "security.html",
            secure_cookies=app.config["SESSION_COOKIE_SECURE"],
            trust_proxy=_bool_env("AWGPANEL_TRUST_PROXY_HEADERS", False),
        )

    @app.post("/security/password")
    @login_required
    def security_password():
        require_csrf()
        current = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirmation = request.form.get("new_password_2", "")
        current_hash = os.environ.get("AWGPANEL_PASSWORD_HASH", "")
        if not current_hash or not check_password_hash(current_hash, current):
            flash("Текущий пароль указан неверно.", "error")
        elif len(new_password) < 8:
            flash("Новый пароль должен содержать не менее 8 символов.", "error")
        elif new_password != confirmation:
            flash("Новые пароли не совпадают.", "error")
        else:
            new_hash = generate_password_hash(new_password)
            _write_env_value("AWGPANEL_PASSWORD_HASH", new_hash)
            os.environ["AWGPANEL_PASSWORD_HASH"] = new_hash
            flash("Пароль администратора изменён.", "success")
        return redirect(url_for("security_page"))

    @app.get("/settings")
    @login_required
    def settings_page():
        return render_template(
            "settings.html",
            version=__version__,
            db_path=os.environ.get("AWGPANEL_DB", "/var/lib/sg-awg-panel/panel.db"),
            config_dir=os.environ.get(
                "AWGPANEL_AWG_CONFIG_DIR", "/etc/amnezia/amneziawg"
            ),
            bind_address=os.environ.get("AWGPANEL_BIND_ADDRESS", "0.0.0.0"),
            port=os.environ.get("AWGPANEL_PORT", "8080"),
        )

    @app.get("/health")
    def health():
        return Response("ok\n", mimetype="text/plain")

    return app


app = create_app()
