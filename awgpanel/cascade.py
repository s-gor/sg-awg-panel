from __future__ import annotations

import base64
import ipaddress
import json
import secrets
import os
import pwd
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .core import (
    CASCADE_SYSTEM_ROLE,
    add_awg_service_client,
    delete_awg_service_clients,
    find_awg_client,
    get_awg_overview,
    get_awg_settings,
    get_panel_settings,
    list_awg_clients,
    list_awg_service_clients,
    render_awg_client_config,
)
from .db import connect, init_db
from .egress import (
    client_has_marked_connection,
    create_outbound,
    find_outbound,
    flush_client_connections,
    list_outbounds,
    mutate_traffic_and_apply,
    replace_outbound,
    traffic_runtime_status,
)
from .errors import AWGPanelError
from .geography import normalize_country_code
from .outbounds import fwmark_for, parse_amneziawg_outbound_config, traffic_table_for

CASCADE_OUTBOUND_NAME = "SG-AWG Cascade"
CASCADE_LINK_PREFIX = "sg-awg-cascade://v1/"
CASCADE_LINK_SCHEMA = "sg-awg-panel/cascade-link/v1"
CASCADE_LINK_TTL_MINUTES = 30


def _stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_cascade_settings() -> dict[str, Any]:
    init_db()
    with connect() as con:
        row = con.execute("SELECT * FROM cascade_settings WHERE id=1").fetchone()
        assigned = con.execute(
            """
            SELECT COUNT(*) AS count FROM awg_clients
            WHERE node_id IS NULL AND egress_mode='outbound' AND outbound_id=(SELECT outbound_id FROM cascade_settings WHERE id=1)
            """
        ).fetchone()
    result = dict(row) if row else {
        "id": 1,
        "enabled": 0,
        "outbound_id": None,
        "exit_name": "",
        "exit_host": "",
        "exit_country_code": "",
        "last_state": "not_configured",
        "last_test_at": None,
        "last_exit_ip": "",
        "last_error": "",
        "last_client_test_at": None,
        "last_client_error": "",
    }
    result["assigned_clients"] = int(assigned["count"] if assigned else 0)
    outbound_id = result.get("outbound_id")
    if outbound_id:
        try:
            result["outbound"] = dict(find_outbound(int(outbound_id)))
        except AWGPanelError:
            result["outbound"] = None
    else:
        result["outbound"] = None
    return result


def _find_cascade_outbound() -> Any | None:
    settings = get_cascade_settings()
    if settings.get("outbound_id"):
        try:
            return find_outbound(int(settings["outbound_id"]))
        except AWGPanelError:
            pass
    for row in list_outbounds():
        if str(row["name"]).casefold() == CASCADE_OUTBOUND_NAME.casefold():
            return row
    return None


def _cascade_service_address() -> str:
    settings = get_awg_settings()
    server_network = ipaddress.ip_network(str(settings["server_network"]), strict=True)
    used = {
        ipaddress.ip_interface(str(row["address"])).ip
        for row in list_awg_clients(local_only=True)
        if str(row["address"] or "").strip()
    }
    for third in range(70, 250):
        network = ipaddress.ip_network(f"10.254.{third}.0/30")
        address = ipaddress.ip_address(int(network.network_address) + 2)
        if address not in server_network and address not in used:
            return f"{address}/32"
    raise AWGPanelError("Не удалось выделить отдельный адрес для служебного Cascade-подключения")


def get_exit_service_client() -> dict[str, Any] | None:
    rows = list_awg_service_clients(CASCADE_SYSTEM_ROLE)
    if not rows:
        return None
    client = dict(rows[-1])
    try:
        client["config_text"] = render_awg_client_config(int(client["id"]))
    except (AWGPanelError, ValueError, PermissionError):
        client["config_text"] = ""
    return client


def create_exit_service_client(*, name: str = "sg-cascade-entry") -> dict[str, Any]:
    """Create exactly one dedicated Cascade peer on the Exit server.

    The service peer uses a /32 outside the normal client network. This avoids
    overlapping Entry/Exit subnets and lets the Exit validate forwarded traffic
    after Entry-side masquerading. Repeating the step safely replaces the old
    service peer instead of creating an ambiguous -2/-3 chain.
    """
    clean = re.sub(r"[^A-Za-z0-9_. -]+", "-", str(name or "").strip())[:64].strip()
    clean = clean or "sg-cascade-entry"
    delete_awg_service_clients(CASCADE_SYSTEM_ROLE)
    client = add_awg_service_client(
        clean,
        address=_cascade_service_address(),
        system_role=CASCADE_SYSTEM_ROLE,
        comment="Служебный доступ SG-AWG Cascade. Не передавайте обычным пользователям.",
    )
    return {"client": dict(client), "config_text": render_awg_client_config(int(client["id"]))}


def _local_country_code() -> str:
    init_db()
    with connect() as con:
        row = con.execute(
            "SELECT country_code FROM cluster_nodes WHERE is_local=1 LIMIT 1"
        ).fetchone()
    return normalize_country_code(row[0] if row else "")


def _urlsafe_encode(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _urlsafe_decode(value: str) -> dict[str, Any]:
    token = str(value or "").strip()
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode((token + padding).encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Ссылка Cascade повреждена или скопирована не полностью") from exc
    if not isinstance(data, dict):
        raise ValueError("Ссылка Cascade имеет неизвестный формат")
    return data


def create_exit_enrollment(
    *, name: str = "sg-cascade-entry", ttl_minutes: int = CASCADE_LINK_TTL_MINUTES
) -> dict[str, Any]:
    """Create a short-lived, copy-and-paste enrollment link for a standalone Exit.

    Creating a new link replaces the previous standalone Cascade service peer.
    The link carries the complete dedicated AWG profile, so the Entry does not
    need API access to the Exit panel and the user never enters keys manually.
    """
    ttl = max(5, min(120, int(ttl_minutes or CASCADE_LINK_TTL_MINUTES)))
    result = create_exit_service_client(name=name)
    config_text = str(result.get("config_text") or "")
    parsed = parse_amneziawg_outbound_config(config_text)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires = now + timedelta(minutes=ttl)
    panel = dict(get_panel_settings())
    payload = {
        "schema": CASCADE_LINK_SCHEMA,
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "nonce": secrets.token_urlsafe(12),
        "exit_name": str(panel.get("instance_name") or "SG-AWG Exit")[:96],
        "exit_country_code": _local_country_code(),
        "endpoint": parsed.endpoint,
        "config": config_text,
    }
    return {
        "client": dict(result["client"]),
        "link": CASCADE_LINK_PREFIX + _urlsafe_encode(payload),
        "issued_at": payload["issued_at"],
        "expires_at": payload["expires_at"],
        "exit_name": payload["exit_name"],
        "exit_country_code": payload["exit_country_code"],
        "endpoint": payload["endpoint"],
    }


def parse_exit_enrollment(value: object, *, now: datetime | None = None) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Вставьте ссылку Cascade, созданную на сервере выхода")
    if text.startswith("[Interface]"):
        # Backward-compatible manual profile import. It has no metadata or TTL.
        parsed = parse_amneziawg_outbound_config(text)
        return {
            "schema": "legacy-awg-config",
            "exit_name": "Другой SG-AWG-сервер",
            "exit_country_code": "",
            "endpoint": parsed.endpoint,
            "config": text,
            "expires_at": "",
        }
    if not text.startswith(CASCADE_LINK_PREFIX):
        raise ValueError("Это не ссылка SG-AWG Cascade. Скопируйте её целиком с сервера выхода")
    data = _urlsafe_decode(text[len(CASCADE_LINK_PREFIX):])
    if data.get("schema") != CASCADE_LINK_SCHEMA:
        raise ValueError("Версия ссылки Cascade не поддерживается этой панелью")
    config_text = str(data.get("config") or "").strip()
    parsed = parse_amneziawg_outbound_config(config_text)
    expires_text = str(data.get("expires_at") or "").strip()
    try:
        expires = datetime.fromisoformat(expires_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("В ссылке Cascade отсутствует корректный срок действия") from exc
    current = now or datetime.now(timezone.utc)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if current.astimezone(timezone.utc) >= expires.astimezone(timezone.utc):
        raise ValueError("Срок действия ссылки Cascade истёк. Создайте новую ссылку на сервере выхода")
    endpoint = str(data.get("endpoint") or parsed.endpoint).strip()
    if endpoint != parsed.endpoint:
        raise ValueError("Endpoint в ссылке Cascade не совпадает с вложенной конфигурацией")
    return {
        "schema": CASCADE_LINK_SCHEMA,
        "exit_name": str(data.get("exit_name") or "Другой SG-AWG-сервер")[:96],
        "exit_country_code": normalize_country_code(data.get("exit_country_code")),
        "endpoint": parsed.endpoint,
        "config": config_text + ("" if config_text.endswith("\n") else "\n"),
        "issued_at": str(data.get("issued_at") or ""),
        "expires_at": expires_text,
    }


def configure_cascade_from_link(value: object) -> dict[str, Any]:
    enrollment = parse_exit_enrollment(value)
    settings = configure_cascade(
        config_text=str(enrollment["config"]),
        exit_name=str(enrollment["exit_name"]),
        exit_country_code=str(enrollment.get("exit_country_code") or ""),
        apply_to_all=True,
    )
    settings["enrollment"] = enrollment
    return settings


def remove_exit_service_client() -> list[dict[str, Any]]:
    return delete_awg_service_clients(CASCADE_SYSTEM_ROLE)


def configure_cascade(
    *,
    config_text: str,
    exit_name: str = "Exit SG-AWG",
    exit_country_code: str = "",
    client_ids: Iterable[int] | None = None,
    apply_to_all: bool = False,
) -> dict[str, Any]:
    parsed = parse_amneziawg_outbound_config(config_text)
    outbound = _find_cascade_outbound()
    if outbound is None:
        outbound = create_outbound(CASCADE_OUTBOUND_NAME, parsed.config_text, enabled=True)
    else:
        outbound = replace_outbound(
            int(outbound["id"]),
            name=CASCADE_OUTBOUND_NAME,
            config_text=parsed.config_text,
            enabled=True,
        )
    outbound_id = int(outbound["id"])
    selected = sorted({int(value) for value in (client_ids or []) if int(value) > 0})
    local_clients = [dict(row) for row in list_awg_clients(local_only=True)]
    if apply_to_all:
        changed_addresses = [
            str(row.get("address") or "") for row in local_clients
            if bool(row.get("enabled", True)) and not str(row.get("system_role") or "")
        ]
    else:
        selected_ids = set(selected)
        changed_addresses = [
            str(row.get("address") or "") for row in local_clients
            if int(row.get("id") or 0) in selected_ids
        ]

    def mutate():
        with connect() as con:
            if apply_to_all:
                con.execute(
                    """
                    UPDATE awg_clients SET egress_mode='outbound', outbound_id=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE node_id IS NULL AND enabled=1 AND system_role='' AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                    """,
                    (outbound_id,),
                )
            elif selected:
                placeholders = ",".join("?" for _ in selected)
                con.execute(
                    f"""
                    UPDATE awg_clients SET egress_mode='outbound', outbound_id=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE node_id IS NULL AND system_role='' AND id IN ({placeholders})
                    """,
                    (outbound_id, *selected),
                )
            con.execute(
                """
                UPDATE cascade_settings SET enabled=1, outbound_id=?, exit_name=?,
                    exit_host=?, exit_country_code=?, last_state='pending_check',
                    last_test_at=NULL, last_exit_ip='', last_error='',
                    last_client_test_at=NULL, last_client_error='',
                    updated_at=CURRENT_TIMESTAMP WHERE id=1
                """,
                (
                    outbound_id,
                    str(exit_name or "Exit SG-AWG").strip()[:96],
                    parsed.endpoint,
                    normalize_country_code(exit_country_code),
                ),
            )
        return get_cascade_settings()

    result = mutate_traffic_and_apply(mutate)
    flush_client_connections(changed_addresses)
    return result


def assign_cascade_clients(client_ids: Iterable[int]) -> dict[str, Any]:
    settings = get_cascade_settings()
    outbound_id = settings.get("outbound_id")
    if not outbound_id:
        raise ValueError("Сначала импортируйте служебный AWG-профиль выходного сервера")
    selected = sorted({int(value) for value in client_ids if int(value) > 0})
    local_clients = [dict(row) for row in list_awg_clients(local_only=True)]
    selected_ids = set(selected)
    changed_addresses = [
        str(row.get("address") or "") for row in local_clients
        if (
            int(row.get("id") or 0) in selected_ids
            or (str(row.get("egress_mode") or "") == "outbound" and int(row.get("outbound_id") or 0) == int(outbound_id))
        )
    ]

    def mutate():
        with connect() as con:
            # The form represents the complete desired selection. First return
            # clients previously assigned to this Cascade to the normal Entry
            # gateway, then apply the new set. This makes unchecking a client a
            # real action instead of leaving a hidden stale assignment behind.
            con.execute(
                """
                UPDATE awg_clients SET egress_mode='awg_gateway', outbound_id=NULL,
                    updated_at=CURRENT_TIMESTAMP
                WHERE node_id IS NULL AND system_role='' AND egress_mode='outbound' AND outbound_id=?
                """,
                (int(outbound_id),),
            )
            if selected:
                placeholders = ",".join("?" for _ in selected)
                con.execute(
                    f"""
                    UPDATE awg_clients SET egress_mode='outbound', outbound_id=?,
                        updated_at=CURRENT_TIMESTAMP WHERE node_id IS NULL AND system_role='' AND id IN ({placeholders})
                    """,
                    (int(outbound_id), *selected),
                )
            con.execute(
                "UPDATE cascade_settings SET enabled=1, last_state=CASE WHEN last_state='healthy' THEN 'server_ready' ELSE last_state END, last_client_test_at=NULL, last_client_error='', updated_at=CURRENT_TIMESTAMP WHERE id=1"
            )
        return get_cascade_settings()

    result = mutate_traffic_and_apply(mutate)
    flush_client_connections(changed_addresses)
    return result


def disable_cascade() -> dict[str, Any]:
    settings = get_cascade_settings()
    outbound_id = settings.get("outbound_id")
    local_clients = [dict(row) for row in list_awg_clients(local_only=True)]
    changed_addresses = [
        str(row.get("address") or "") for row in local_clients
        if outbound_id and str(row.get("egress_mode") or "") == "outbound"
        and int(row.get("outbound_id") or 0) == int(outbound_id)
    ]

    def mutate():
        with connect() as con:
            if outbound_id:
                con.execute(
                    """
                    UPDATE awg_clients SET egress_mode='awg_gateway', outbound_id=NULL,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE node_id IS NULL AND egress_mode='outbound' AND outbound_id=?
                    """,
                    (int(outbound_id),),
                )
                con.execute("DELETE FROM outbounds WHERE id=?", (int(outbound_id),))
            con.execute(
                """
                UPDATE cascade_settings SET enabled=0, outbound_id=NULL, exit_name='',
                    exit_host='', exit_country_code='', last_state='disabled', last_test_at=NULL,
                    last_exit_ip='', last_error='', last_client_test_at=NULL,
                    last_client_error='', updated_at=CURRENT_TIMESTAMP WHERE id=1
                """
            )
        return get_cascade_settings()

    result = mutate_traffic_and_apply(mutate)
    flush_client_connections(changed_addresses)
    return result


def _public_ip_via_outbound(outbound_id: int) -> tuple[str, str]:
    """Probe the public IPv4 through the selected policy-routing table.

    The check deliberately runs curl under an unprivileged uid and installs a
    short-lived uidrange rule. It therefore does not confuse the Controller's
    ordinary public address with the Cascade exit address.
    """
    if os.geteuid() != 0:
        return "", "Проверка выходного IP требует прав root"
    ip_cmd = shutil.which("ip")
    curl_cmd = shutil.which("curl")
    setpriv_cmd = shutil.which("setpriv")
    if not ip_cmd or not curl_cmd or not setpriv_cmd:
        return "", "Для проверки выходного IP нужны ip, curl и setpriv"
    try:
        account = pwd.getpwnam("nobody")
        uid, gid = int(account.pw_uid), int(account.pw_gid)
    except KeyError:
        uid = gid = 65534

    table = int(traffic_table_for(int(outbound_id)))
    existing = subprocess.run(
        [ip_cmd, "rule", "show"], capture_output=True, text=True, timeout=5, check=False
    )
    priorities: set[int] = set()
    if existing.returncode == 0:
        for line in existing.stdout.splitlines():
            match = re.match(r"\s*(\d+):", line)
            if match:
                priorities.add(int(match.group(1)))
    priority = next((value for value in range(11000, 12000) if value not in priorities), None)
    if priority is None:
        return "", "Не найден свободный приоритет для безопасной проверки маршрута"

    rule = [
        ip_cmd, "rule", "add", "priority", str(priority),
        "uidrange", f"{uid}-{uid}", "lookup", str(table),
    ]
    added = subprocess.run(rule, capture_output=True, text=True, timeout=5, check=False)
    if added.returncode != 0:
        return "", (added.stderr or added.stdout or "Не удалось создать временное правило проверки").strip()
    try:
        subprocess.run([ip_cmd, "route", "flush", "cache"], capture_output=True, timeout=5, check=False)
        for url in ("https://checkip.amazonaws.com", "https://api.ipify.org"):
            result = subprocess.run(
                [
                    setpriv_cmd, "--reuid", str(uid), "--regid", str(gid), "--clear-groups",
                    curl_cmd, "-4fsS", "--noproxy", "*", "--connect-timeout", "4",
                    "--max-time", "8", url,
                ],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            value = result.stdout.strip()
            try:
                if result.returncode == 0 and ipaddress.ip_address(value).is_global:
                    return value, ""
            except ValueError:
                pass
        return "", "AWG runtime готов, но внешний сервис не подтвердил выходной IP"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"Не удалось выполнить проверку выходного IP: {exc}"
    finally:
        subprocess.run(
            [
                ip_cmd, "rule", "del", "priority", str(priority),
                "uidrange", f"{uid}-{uid}", "lookup", str(table),
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
        subprocess.run([ip_cmd, "route", "flush", "cache"], capture_output=True, timeout=5, check=False)


def _cascade_route_diagnostic(outbound_id: int) -> tuple[bool, str]:
    ip_cmd = shutil.which("ip")
    if not ip_cmd:
        return False, "Не найдена команда ip"
    clients = [
        row for row in list_awg_clients(local_only=True)
        if str(row["egress_mode"] or "") == "outbound"
        and int(row["outbound_id"] or 0) == int(outbound_id)
        and not str(row["system_role"] or "")
    ]
    if not clients:
        return False, "К Cascade пока не назначен ни один обычный клиент"
    client_ip = str(ipaddress.ip_interface(str(clients[0]["address"])).ip)
    interface = f"sgo{int(outbound_id)}"
    result = subprocess.run(
        [
            ip_cmd, "route", "get", "1.1.1.1", "from", client_ip,
            "mark", hex(0x5100 + int(outbound_id)),
            "iif", str(get_awg_settings()["interface_name"]),
        ],
        capture_output=True, text=True, timeout=5, check=False,
    )
    text = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, text or "Не удалось проверить маршрут выбранного клиента"
    if f"dev {interface}" not in text:
        return False, f"Маршрут клиента не указывает на {interface}: {text}"
    return True, text


def test_cascade(*, probe_public_ip: bool = True) -> dict[str, Any]:
    settings = get_cascade_settings()
    outbound_id = settings.get("outbound_id")
    if not outbound_id:
        raise ValueError("Cascade ещё не настроен")
    runtime = traffic_runtime_status()
    profile = next(
        (item for item in runtime.get("profiles", []) if int(item.get("id", -1)) == int(outbound_id)),
        None,
    )
    runtime_ok = bool(profile and profile.get("healthy"))
    route_ok, route_note = _cascade_route_diagnostic(int(outbound_id)) if runtime_ok else (False, "")
    ready = bool(runtime_ok and route_ok)
    exit_ip, probe_note = (
        _public_ip_via_outbound(int(outbound_id))
        if ready and probe_public_ip
        else ("", "")
    )
    # A server-side route check is not a client check. Always return to
    # server_ready so the green healthy state can only be restored by a fresh
    # client handshake with traffic in both directions.
    state = "server_ready" if ready else "error"
    error = "" if ready else (route_note or "AWG outbound не поднят или policy routing не применён")
    with connect() as con:
        con.execute(
            """
            UPDATE cascade_settings SET last_state=?, last_test_at=?, last_exit_ip=?,
                last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=1
            """,
            (state, _stamp(), exit_ip, error),
        )
    if ready and exit_ip:
        message = f"Серверный маршрут Cascade готов. Выходной IP: {exit_ip}. Откройте на клиенте любой сайт и проверьте маршрут клиента — переподключать VPN не нужно."
    elif ready:
        message = "Серверный маршрут Cascade готов. Откройте на клиенте любой сайт и проверьте маршрут клиента — переподключать VPN не нужно."
    else:
        message = error
    return {
        "ok": ready,
        "state": state,
        "message": message,
        "probe_note": probe_note,
        "route_note": route_note,
        "exit_ip": exit_ip,
        "profile": profile,
        "runtime": runtime,
    }



def _parse_stamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def test_cascade_client() -> dict[str, Any]:
    """Confirm that at least one assigned VPN client has a fresh two-way session.

    The panel cannot see the browser on the client device, so this check is
    deliberately honest: it verifies a fresh AWG handshake, non-zero traffic
    in both directions, and a live conntrack flow carrying the expected
    Outbound route mark. The visible exit IP remains the independently
    verified Outbound IP from the server-route check.
    """
    settings = get_cascade_settings()
    outbound_id = int(settings.get("outbound_id") or 0)
    if not outbound_id:
        raise ValueError("Cascade ещё не настроен")
    if str(settings.get("last_state") or "") not in {"server_ready", "healthy"}:
        raise ValueError("Сначала нажмите «Проверить серверный маршрут»")

    checked_at = _stamp()
    server_checked = _parse_stamp(settings.get("last_test_at"))
    threshold = int(server_checked.timestamp()) - 30 if server_checked else 0
    overview = get_awg_overview()
    assigned = [
        item for item in overview.get("clients", [])
        if not item.get("node_id")
        and str(item.get("egress_mode") or "") == "outbound"
        and int(item.get("outbound_id") or 0) == outbound_id
        and bool(item.get("effective_enabled"))
    ]
    if not assigned:
        error = "К Cascade не назначен ни один активный клиент"
        with connect() as con:
            con.execute(
                "UPDATE cascade_settings SET last_state='server_ready', "
                "last_client_test_at=?, last_client_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=1",
                (checked_at, error),
            )
        return {"ok": False, "message": error, "clients": [], "exit_ip": settings.get("last_exit_ip") or ""}

    verified = []
    expected_mark = fwmark_for(outbound_id)
    for item in assigned:
        handshake = int(item.get("latest_handshake") or 0)
        rx = int(item.get("rx") or 0)
        tx = int(item.get("tx") or 0)
        route_marked = client_has_marked_connection(item.get("address"), expected_mark)
        if handshake >= threshold and handshake > 0 and rx > 0 and tx > 0 and route_marked:
            verified.append(item)

    if verified:
        with connect() as con:
            con.execute(
                "UPDATE cascade_settings SET last_state='healthy', last_client_test_at=?, "
                "last_client_error='', updated_at=CURRENT_TIMESTAMP WHERE id=1",
                (checked_at,),
            )
        names = ", ".join(str(item.get("name") or "Клиент") for item in verified[:3])
        return {
            "ok": True,
            "message": f"Активное соединение клиента использует маршрут Cascade: {names}.",
            "clients": verified,
            "exit_ip": settings.get("last_exit_ip") or "",
        }

    error = (
        "Активное соединение с меткой маршрута Cascade пока не найдено. "
        "Откройте на клиенте любой сайт и повторите проверку; переподключать VPN не требуется."
    )
    with connect() as con:
        con.execute(
            "UPDATE cascade_settings SET last_state='server_ready', last_client_test_at=?, "
            "last_client_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=1",
            (checked_at, error),
        )
    return {
        "ok": False,
        "message": error,
        "clients": assigned,
        "exit_ip": settings.get("last_exit_ip") or "",
    }

def cascade_document() -> dict[str, Any]:
    settings = get_cascade_settings()
    exit_service = get_exit_service_client()
    return {
        "schema": "sg-awg-panel/cascade/v2",
        "enabled": bool(settings.get("enabled")),
        "exit_name": settings.get("exit_name") or "",
        "exit_host": settings.get("exit_host") or "",
        "exit_country_code": normalize_country_code(settings.get("exit_country_code")),
        "outbound_id": settings.get("outbound_id"),
        "assigned_clients": settings.get("assigned_clients", 0),
        "last_state": settings.get("last_state") or "not_configured",
        "last_test_at": settings.get("last_test_at"),
        "last_exit_ip": settings.get("last_exit_ip") or "",
        "last_error": settings.get("last_error") or "",
        "last_client_test_at": settings.get("last_client_test_at"),
        "last_client_error": settings.get("last_client_error") or "",
        "exit_service_client": (
            {"id": exit_service["id"], "name": exit_service["name"], "address": exit_service["address"]}
            if exit_service else None
        ),
    }
