from __future__ import annotations

import base64
import hashlib
import io
import os
import secrets
import subprocess
import threading
import time
from datetime import timedelta
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
    build_diagnostic_report,
    configure_and_start_awg,
    configure_backup_policy,
    configure_panel_access,
    create_web_session,
    create_manual_backup,
    delete_awg_client,
    find_awg_client,
    find_client_by_access_token,
    get_awg_diagnostics,
    get_awg_overview,
    get_awg_settings,
    get_panel_settings,
    get_update_status,
    list_awg_clients,
    list_auth_events,
    list_backups,
    list_web_sessions,
    panel_public_url,
    record_auth_event,
    record_client_access,
    regenerate_awg_client,
    regenerate_client_access_token,
    render_awg_client_config,
    restart_awg,
    revoke_all_web_sessions,
    revoke_web_session,
    rotate_auth_epoch,
    restore_backup,
    set_awg_client_enabled,
    set_client_access_enabled,
    start_awg,
    start_panel_update,
    stop_awg,
    update_awg_client_routing,
    update_awg_client_settings,
    update_dns_servers,
    update_ip_allowlist,
    update_routing_settings,
    validate_web_session,
    check_for_updates,
    ip_is_allowed,
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
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )
    if _bool_env("AWGPANEL_TRUST_PROXY_HEADERS", False):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[assignment]

    init_db()
    login_attempts: dict[str, list[float]] = {}
    login_lock = threading.Lock()
    login_window = 15 * 60
    login_limit = 5

    def client_ip() -> str:
        return request.remote_addr or "unknown"

    def client_agent() -> str:
        return request.headers.get("User-Agent", "")[:512]

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            token = str(session.get("session_token", ""))
            if not session.get("authenticated") or validate_web_session(token) is None:
                session.clear()
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
        panel = get_panel_settings()
        base = panel_public_url(panel) if panel["public_host"] else request.host_url.rstrip("/")
        rows: list[dict[str, object]] = []
        for row in list_awg_clients():
            item = dict(row)
            item["access_url"] = f"{base}{url_for('public_client_config', token=row['access_token'])}"
            rows.append(item)
        return rows

    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["panel_version"] = __version__

    @app.before_request
    def enforce_panel_allowlist():
        if request.endpoint in {"static", "health", "public_client_config"}:
            return None
        ip_value = client_ip()
        if not ip_is_allowed(ip_value):
            record_auth_event(
                "allowlist_denied", ip_address=ip_value,
                user_agent=client_agent(), detail=request.path,
            )
            abort(403, "Этот IP не входит в allowlist панели")
        return None

    @app.get("/login")
    def login():
        if session.get("authenticated") and validate_web_session(
            str(session.get("session_token", "")), touch=False
        ) is not None:
            return redirect(url_for("dashboard"))
        if request.args.get("password_changed") == "1":
            flash("Пароль изменён. Все активные сессии завершены.", "success")
        return render_template("login.html")

    @app.post("/login")
    def login_post():
        require_csrf()
        client_key = client_ip()
        agent = client_agent()
        now = time.time()
        with login_lock:
            recent = [stamp for stamp in login_attempts.get(client_key, []) if now - stamp < login_window]
            login_attempts[client_key] = recent
            if len(recent) >= login_limit:
                wait_minutes = max(1, int((login_window - (now - recent[0]) + 59) // 60))
                record_auth_event(
                    "login_blocked", ip_address=client_key,
                    user_agent=agent, detail=f"wait={wait_minutes}m",
                )
                flash(f"Слишком много неудачных попыток. Повторите через {wait_minutes} мин.", "error")
                return redirect(url_for("login"))

        password_hash = os.environ.get("AWGPANEL_PASSWORD_HASH", "")
        if not password_hash:
            record_auth_event("login_error", ip_address=client_key, user_agent=agent, detail="password hash missing")
            flash("Пароль панели не настроен. Повторите установку GUI.", "error")
            return redirect(url_for("login"))
        if not check_password_hash(password_hash, request.form.get("password", "")):
            with login_lock:
                login_attempts.setdefault(client_key, []).append(now)
            record_auth_event("login_failed", ip_address=client_key, user_agent=agent)
            flash("Неверный пароль", "error")
            return redirect(url_for("login"))
        with login_lock:
            login_attempts.pop(client_key, None)
        token = secrets.token_urlsafe(40)
        create_web_session(token, ip_address=client_key, user_agent=agent)
        session.clear()
        session.permanent = True
        session["authenticated"] = True
        session["session_token"] = token
        session["csrf_token"] = secrets.token_urlsafe(32)
        record_auth_event("login_success", ip_address=client_key, user_agent=agent)
        return redirect(url_for("dashboard"))

    @app.post("/logout")
    @login_required
    def logout():
        require_csrf()
        token = str(session.get("session_token", ""))
        if token:
            revoke_web_session(hashlib.sha256(token.encode("utf-8")).hexdigest())
        record_auth_event("logout", ip_address=client_ip(), user_agent=client_agent())
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

    @app.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
    @login_required
    def client_edit(client_id: int):
        client = find_awg_client(client_id)
        if request.method == "POST":
            require_csrf()
            try:
                updated = update_awg_client_settings(
                    client_id,
                    name=request.form.get("name", ""),
                    comment=request.form.get("comment", ""),
                    dns_servers=request.form.get("dns_servers", ""),
                    mtu=request.form.get("mtu", ""),
                )
                flash(f"Настройки клиента {updated['name']} сохранены.", "success")
                return redirect(url_for("clients_page"))
            except (ValueError, PermissionError, AWGPanelError) as exc:
                flash(str(exc), "error")
                client = find_awg_client(client_id)
        return render_template(
            "client_edit.html", client=client, settings=get_awg_settings()
        )

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
        if mode == "all":
            allowed_ips = "0.0.0.0/0"
        elif mode == "server":
            allowed_ips = str(get_awg_settings()["server_network"])
        else:
            allowed_ips = request.form.get("allowed_ips", "")
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

    @app.get("/diagnostics/report")
    @login_required
    def diagnostics_report():
        report = build_diagnostic_report()
        return send_file(
            io.BytesIO(report.encode("utf-8")),
            mimetype="text/plain; charset=utf-8",
            as_attachment=True,
            download_name="sg-awg-panel-diagnostics.txt",
        )

    @app.get("/backups")
    @login_required
    def backups_page():
        return render_template(
            "backups.html",
            backups=list_backups(limit=50),
            panel=get_panel_settings(),
        )

    @app.post("/backups/policy")
    @login_required
    def backup_policy_save():
        require_csrf()
        try:
            panel = configure_backup_policy(
                request.form.get("backup_schedule", "daily"),
                request.form.get("backup_keep", "20"),
            )
            flash(
                f"Расписание резервных копий сохранено. Хранится {panel['backup_keep']} копий.",
                "success",
            )
        except (ValueError, PermissionError, AWGPanelError, OSError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("backups_page"))

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
        token = str(session.get("session_token", ""))
        return render_template(
            "security.html",
            secure_cookies=app.config["SESSION_COOKIE_SECURE"],
            trust_proxy=_bool_env("AWGPANEL_TRUST_PROXY_HEADERS", False),
            panel=get_panel_settings(),
            sessions=list_web_sessions(current_token=token),
            auth_events=list_auth_events(limit=100),
            current_ip=client_ip(),
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
            record_auth_event("password_change_failed", ip_address=client_ip(), user_agent=client_agent())
            flash("Текущий пароль указан неверно.", "error")
            return redirect(url_for("security_page"))
        if len(new_password) < 10:
            flash("Новый пароль должен содержать не менее 10 символов.", "error")
            return redirect(url_for("security_page"))
        if new_password != confirmation:
            flash("Новые пароли не совпадают.", "error")
            return redirect(url_for("security_page"))
        new_hash = generate_password_hash(new_password)
        _write_env_value("AWGPANEL_PASSWORD_HASH", new_hash)
        os.environ["AWGPANEL_PASSWORD_HASH"] = new_hash
        rotate_auth_epoch()
        record_auth_event("password_changed", ip_address=client_ip(), user_agent=client_agent())
        session.clear()
        return redirect(url_for("login", password_changed="1"))

    @app.post("/security/allowlist")
    @login_required
    def security_allowlist():
        require_csrf()
        try:
            panel = update_ip_allowlist(
                request.form.get("ip_allowlist", ""), current_ip=client_ip()
            )
            state = panel["ip_allowlist"] or "выключен"
            record_auth_event(
                "allowlist_changed", ip_address=client_ip(),
                user_agent=client_agent(), detail=str(state),
            )
            flash("IP allowlist сохранён.", "success")
        except (ValueError, PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("security_page"))

    @app.post("/security/sessions/<token_hash>/revoke")
    @login_required
    def security_session_revoke(token_hash: str):
        require_csrf()
        if not re.fullmatch(r"[0-9a-f]{64}", token_hash):
            abort(400)
        current_token = str(session.get("session_token", ""))
        current_hash = hashlib.sha256(current_token.encode("utf-8")).hexdigest()
        revoke_web_session(token_hash)
        record_auth_event(
            "session_revoked", ip_address=client_ip(),
            user_agent=client_agent(), detail=token_hash[:12],
        )
        if token_hash == current_hash:
            session.clear()
            return redirect(url_for("login"))
        flash("Сессия завершена.", "success")
        return redirect(url_for("security_page"))

    @app.post("/security/sessions/revoke-others")
    @login_required
    def security_sessions_revoke_others():
        require_csrf()
        token = str(session.get("session_token", ""))
        revoke_all_web_sessions(except_token=token)
        record_auth_event("sessions_revoked", ip_address=client_ip(), user_agent=client_agent())
        flash("Все остальные сессии завершены.", "success")
        return redirect(url_for("security_page"))

    @app.get("/settings")
    @login_required
    def settings_page():
        update_info = check_for_updates(force=False)
        return render_template(
            "settings.html",
            version=__version__,
            db_path=os.environ.get("AWGPANEL_DB", "/var/lib/sg-awg-panel/panel.db"),
            config_dir=os.environ.get(
                "AWGPANEL_AWG_CONFIG_DIR", "/etc/amnezia/amneziawg"
            ),
            bind_address=os.environ.get("AWGPANEL_BIND_ADDRESS", "127.0.0.1"),
            port=os.environ.get("AWGPANEL_PORT", "18080"),
            panel=get_panel_settings(),
            public_url=panel_public_url(),
            update_info=update_info,
            update_status=get_update_status(),
        )

    @app.post("/settings/access")
    @login_required
    def settings_access_save():
        require_csrf()
        try:
            panel = configure_panel_access(
                scheme=request.form.get("public_scheme", "http"),
                public_host=request.form.get("public_host", ""),
                public_port=request.form.get("public_port", "8080"),
                https_email=request.form.get("https_email", ""),
            )
            record_auth_event(
                "panel_access_changed", ip_address=client_ip(),
                user_agent=client_agent(), detail=panel_public_url(panel),
            )
            unit = f"sg-awg-panel-restart-{secrets.token_hex(4)}"
            subprocess.run(
                [
                    "systemd-run", f"--unit={unit}", "--collect", "--on-active=2s",
                    "/bin/systemctl", "restart", "sg-awg-panel.service",
                ],
                check=False, capture_output=True, text=True, timeout=10,
            )
            if panel["public_host"]:
                target = panel_public_url(panel)
            else:
                host = request.host.split(":", 1)[0]
                default_port = 443 if panel["public_scheme"] == "https" else 80
                suffix = "" if int(panel["public_port"]) == default_port else f":{panel['public_port']}"
                target = f"{panel['public_scheme']}://{host}{suffix}"
            return redirect(target + url_for("settings_page"))
        except (ValueError, PermissionError, AWGPanelError, OSError, subprocess.TimeoutExpired) as exc:
            flash(str(exc), "error")
            return redirect(url_for("settings_page"))

    @app.post("/settings/update/check")
    @login_required
    def settings_update_check():
        require_csrf()
        info = check_for_updates(force=True)
        if info["error"]:
            flash(f"Не удалось проверить обновления: {info['error']}", "error")
        elif info["available"]:
            flash(f"Доступна версия {info['latest']}.", "success")
        else:
            flash("Установлена актуальная версия.", "success")
        return redirect(url_for("settings_page"))

    @app.post("/settings/update/start")
    @login_required
    def settings_update_start():
        require_csrf()
        version = request.form.get("version", "")
        try:
            result = start_panel_update(version)
            record_auth_event(
                "update_started", ip_address=client_ip(),
                user_agent=client_agent(), detail=result["version"],
            )
            flash(f"Обновление до {result['version']} запущено. Страница перезапустится автоматически.", "success")
        except (ValueError, PermissionError, AWGPanelError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("settings_page"))

    @app.get("/health")
    def health():
        return Response("ok\n", mimetype="text/plain")

    return app


app = create_app()
