from __future__ import annotations

import base64
import io
import os
import secrets
from functools import wraps

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
from werkzeug.security import check_password_hash

from . import __version__
from .core import (
    add_awg_client,
    configure_and_start_awg,
    delete_awg_client,
    find_awg_client,
    get_awg_diagnostics,
    get_awg_overview,
    render_awg_client_config,
    regenerate_awg_client,
    restart_awg,
    set_awg_client_enabled,
    start_awg,
    stop_awg,
)
from .db import init_db
from .errors import AWGPanelError


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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

    @app.get("/diagnostics")
    @login_required
    def diagnostics():
        return render_template("diagnostics.html", diagnostics=get_awg_diagnostics())

    @app.post("/settings")
    @login_required
    def save_settings():
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
        return redirect(url_for("dashboard"))

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
        return redirect(url_for("dashboard"))

    @app.post("/clients/add")
    @login_required
    def client_add():
        require_csrf()
        try:
            client = add_awg_client(
                request.form.get("name", ""), request.form.get("comment", "")
            )
            flash(f"Клиент {client['name']} создан.", "success")
        except (ValueError, PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("dashboard"))

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
        return redirect(url_for("dashboard"))

    @app.post("/clients/<int:client_id>/regenerate")
    @login_required
    def client_regenerate(client_id: int):
        require_csrf()
        try:
            client = regenerate_awg_client(client_id)
            flash(f"Ключи клиента {client['name']} пересозданы. Старый конфиг больше не работает.", "success")
            return redirect(url_for("client_access", client_id=client_id))
        except (PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"))

    @app.post("/clients/<int:client_id>/delete")
    @login_required
    def client_delete(client_id: int):
        require_csrf()
        try:
            client = delete_awg_client(client_id)
            flash(f"Клиент {client['name']} удалён.", "success")
        except (PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    @app.get("/clients/<int:client_id>/access")
    @login_required
    def client_access(client_id: int):
        client = find_awg_client(client_id)
        config_text = render_awg_client_config(client_id)
        image = qrcode.make(config_text)
        output = io.BytesIO()
        image.save(output, format="PNG")
        qr_data = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        return render_template(
            "client_access.html", client=client, config_text=config_text, qr_data=qr_data
        )

    @app.get("/clients/<int:client_id>/download")
    @login_required
    def client_download(client_id: int):
        client = find_awg_client(client_id)
        config_text = render_awg_client_config(client_id)
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in client["name"])
        return send_file(
            io.BytesIO(config_text.encode("utf-8")),
            mimetype="text/plain; charset=utf-8",
            as_attachment=True,
            download_name=f"{safe or 'client'}-awg.conf",
        )

    @app.get("/health")
    def health():
        return Response("ok\n", mimetype="text/plain")

    return app


app = create_app()
