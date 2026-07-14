from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timezone
from typing import Any

from .core import (
    add_awg_service_client,
    delete_awg_service_clients,
    find_awg_client,
    get_awg_settings,
    list_awg_clients,
    render_awg_client_config,
)
from .db import connect, init_db
from .egress import (
    apply_egress_runtime,
    create_outbound,
    delete_outbound,
    find_outbound,
    flush_client_connections,
    list_outbounds,
    replace_outbound,
    traffic_runtime_status,
)
from .errors import AWGPanelError
from .node_clients import (
    _peer_payload,
    _service_address,
    add_remote_service_client,
    remove_remote_service_client,
    render_remote_client_config,
    require_ready_node,
)
from .node_manager import get_job, get_node, list_nodes, queue_job

CASCADE_ROLE_PREFIX = "cascade_exit_"
CASCADE_NAME_PREFIX = "SG-AWG Cascade"


def _stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row(row: object) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _node_endpoint(node: dict[str, Any]) -> str:
    if node.get("is_local"):
        settings = dict(get_awg_settings())
        host = str(settings.get("endpoint_host") or node.get("public_host") or node.get("public_ipv4") or "").strip()
        port = int(settings.get("listen_port") or 585)
    else:
        host = str(node.get("public_ipv4") or node.get("public_host") or "").strip()
        port = 585
    return f"{host}:{port}" if host else ""


def _node_ready(node: dict[str, Any]) -> tuple[bool, str]:
    if node.get("is_local"):
        settings = dict(get_awg_settings())
        if not bool(settings.get("configured")):
            return False, "На Controller ещё не настроен AWG Server"
        if int(settings.get("listen_port") or 0) != 585:
            return False, "AWG Server должен использовать UDP 585"
        if not str(settings.get("public_key") or "").strip():
            return False, "Не найден публичный ключ AWG Server"
        return True, ""
    try:
        require_ready_node(int(node["id"]))
    except (ValueError, AWGPanelError) as exc:
        return False, str(exc)
    if not _node_endpoint(node):
        return False, "SG-Node ещё не передала публичный IP"
    return True, ""


def cascade_servers() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in list_nodes():
        item = dict(node)
        ready, reason = _node_ready(item)
        item["cascade_ready"] = ready
        item["cascade_ready_reason"] = reason
        item["endpoint"] = _node_endpoint(item)
        result.append(item)
    return result


def _enrich(link: dict[str, Any]) -> dict[str, Any]:
    entry = get_node(int(link["entry_node_id"]))
    exit_node = get_node(int(link["exit_node_id"]))
    link["entry"] = entry
    link["exit"] = exit_node
    with connect() as con:
        if entry.get("is_local"):
            count_row = con.execute(
                "SELECT COUNT(*) FROM awg_clients WHERE system_role='' AND node_id IS NULL"
            ).fetchone()
        else:
            count_row = con.execute(
                "SELECT COUNT(*) FROM awg_clients WHERE system_role='' AND node_id=?",
                (int(entry["id"]),),
            ).fetchone()
        link["client_count"] = int(count_row[0])
    link["visible_exit_ip"] = str(link.get("last_exit_ip") or exit_node.get("public_ipv4") or exit_node.get("public_host") or "")
    return link


def list_cascade_links(*, include_disabled: bool = True) -> list[dict[str, Any]]:
    init_db()
    query = "SELECT * FROM cascade_links"
    if not include_disabled:
        query += " WHERE enabled=1 AND state<>'disabled'"
    query += " ORDER BY enabled DESC, id DESC"
    with connect() as con:
        rows = con.execute(query).fetchall()
    return [_enrich(dict(row)) for row in rows]


def get_cascade_link(link_id: int) -> dict[str, Any]:
    init_db()
    with connect() as con:
        row = con.execute("SELECT * FROM cascade_links WHERE id=?", (int(link_id),)).fetchone()
    if row is None:
        raise AWGPanelError("Cascade не найден")
    return _enrich(dict(row))


def _role(link_id: int) -> str:
    return f"{CASCADE_ROLE_PREFIX}{int(link_id)}"


def _service_name(link: dict[str, Any]) -> str:
    entry = link["entry"]
    value = re.sub(r"[^A-Za-z0-9_. -]+", "-", f"Cascade {entry['name']}").strip()
    return value[:64] or f"Cascade Entry {link['id']}"


def _create_exit_service(link: dict[str, Any]) -> dict[str, Any]:
    exit_node = link["exit"]
    role = _role(int(link["id"]))
    name = _service_name(link)
    comment = f"Служебный туннель Cascade для сервера подключения {link['entry']['name']}"
    if exit_node.get("is_local"):
        client = add_awg_service_client(
            name,
            address=_service_address(),
            system_role=role,
            comment=comment,
        )
    else:
        client = add_remote_service_client(
            int(exit_node["id"]), name=name, system_role=role, comment=comment
        )
    with connect() as con:
        con.execute(
            "UPDATE cascade_links SET service_client_id=?, exit_job_id=?, "
            "state=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (
                int(client["id"]),
                int(client["deployment_job_id"] or 0) or None,
                "preparing_exit",
                int(link["id"]),
            ),
        )
    return dict(client)


def _cascade_tunnel_config(config_text: str) -> str:
    """Turn an ordinary client profile into an isolated server-to-server tunnel.

    Cascade owns policy routing itself. `Table = off` prevents awg-quick from
    replacing the server default route, and DNS is deliberately omitted because
    this interface is never exposed as a user profile.
    """
    rendered: list[str] = []
    in_interface = False
    table_written = False
    for raw in str(config_text or "").splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            if in_interface and not table_written:
                rendered.append("Table = off")
                table_written = True
            in_interface = line.casefold() == "[interface]"
            rendered.append(raw)
            if in_interface:
                rendered.append("Table = off")
                table_written = True
            continue
        if in_interface and (line.casefold().startswith("dns =") or line.casefold().startswith("table =")):
            continue
        rendered.append(raw)
    if not table_written:
        raise ValueError("В служебной конфигурации Cascade не найден раздел [Interface]")
    return "\n".join(rendered).strip() + "\n"


def _service_config(link: dict[str, Any]) -> str:
    client_id = int(link.get("service_client_id") or 0)
    if not client_id:
        raise ValueError("Служебное подключение к серверу выхода ещё не создано")
    client = find_awg_client(client_id)
    if client["node_id"]:
        config_text = render_remote_client_config(client)
    else:
        config_text = render_awg_client_config(client_id)
    return _cascade_tunnel_config(config_text)


def _managed_peer_snapshot(node_id: int) -> list[dict[str, Any]]:
    rows = [
        dict(row) for row in list_awg_clients(node_id=int(node_id))
        if str(row["deployment_state"] or "active") != "deleting" and bool(row["enabled"])
    ]
    peers: list[dict[str, Any]] = []
    for row in rows:
        item = _peer_payload(row)
        routes = [str(item["address"])]
        advertised = str(item.get("advertised_networks") or "").strip()
        if advertised:
            routes.extend(part.strip() for part in advertised.split(",") if part.strip())
        item["allowed_ips"] = ", ".join(routes)
        peers.append(item)
    return peers


def _find_link_outbound(link_id: int):
    name = f"{CASCADE_NAME_PREFIX} {int(link_id)}"
    for row in list_outbounds():
        if str(row["name"]).casefold() == name.casefold():
            return row
    return None


def _configure_local_entry(link: dict[str, Any], config_text: str) -> dict[str, Any]:
    changed_addresses = [
        str(row.get("address") or "") for row in map(dict, list_awg_clients(local_only=True))
        if not str(row.get("system_role") or "")
    ]
    existing = _find_link_outbound(int(link["id"]))
    if existing is None:
        outbound = create_outbound(f"{CASCADE_NAME_PREFIX} {int(link['id'])}", config_text, enabled=True)
    else:
        outbound = replace_outbound(
            int(existing["id"]),
            name=f"{CASCADE_NAME_PREFIX} {int(link['id'])}",
            config_text=config_text,
            enabled=True,
        )
    outbound_id = int(outbound["id"])
    with connect() as con:
        con.execute(
            "UPDATE awg_clients SET egress_mode='outbound', outbound_id=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE node_id IS NULL AND system_role=''",
            (outbound_id,),
        )
        con.execute(
            "UPDATE cascade_links SET outbound_id=?, state='active', enabled=1, "
            "last_exit_ip=?, last_error='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (
                str(link["exit"].get("public_ipv4") or link["exit"].get("public_host") or ""),
                int(link["id"]),
            ),
        )
    apply_egress_runtime()
    flush_client_connections(changed_addresses)
    return get_cascade_link(int(link["id"]))


def _configure_remote_entry(link: dict[str, Any], config_text: str) -> dict[str, Any]:
    entry = link["entry"]
    payload = {
        "mode": "configure_cascade",
        "link_id": int(link["id"]),
        "config_text": config_text,
        "exit_public_ip": str(link["exit"].get("public_ipv4") or link["exit"].get("public_host") or ""),
        "managed_peers": _managed_peer_snapshot(int(entry["id"])),
    }
    job = queue_job(int(entry["id"]), "apply_awg_config", payload)
    with connect() as con:
        con.execute(
            "UPDATE cascade_links SET state='preparing_entry', entry_job_id=?, "
            "last_error='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(job["id"]), int(link["id"])),
        )
    return get_cascade_link(int(link["id"]))


def _configure_entry(link: dict[str, Any]) -> dict[str, Any]:
    config_text = _service_config(link)
    if link["entry"].get("is_local"):
        return _configure_local_entry(link, config_text)
    return _configure_remote_entry(link, config_text)


def create_cascade_link(*, entry_node_id: int, exit_node_id: int) -> dict[str, Any]:
    init_db()
    entry_id, exit_id = int(entry_node_id), int(exit_node_id)
    if entry_id == exit_id:
        raise ValueError("Выберите два разных сервера")
    entry = get_node(entry_id)
    exit_node = get_node(exit_id)
    for label, node in (("Сервер подключения", entry), ("Сервер выхода в интернет", exit_node)):
        ready, reason = _node_ready(node)
        if not ready:
            raise ValueError(f"{label} недоступен: {reason}")
    with connect() as con:
        current = con.execute(
            "SELECT id FROM cascade_links WHERE entry_node_id=? AND enabled=1 AND state<>'disabled'",
            (entry_id,),
        ).fetchone()
        if current:
            raise ValueError("Для этого сервера подключения уже включён Cascade. Сначала верните прямой выход в интернет")
        cursor = con.execute(
            "INSERT INTO cascade_links (entry_node_id, exit_node_id, state) VALUES (?, ?, 'preparing_exit')",
            (entry_id, exit_id),
        )
        link_id = int(cursor.lastrowid)
    try:
        link = get_cascade_link(link_id)
        _create_exit_service(link)
        return reconcile_cascade_link(link_id)
    except Exception as exc:
        with connect() as con:
            con.execute(
                "UPDATE cascade_links SET state='error', last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(exc)[:1024], link_id),
            )
        raise


def reconcile_cascade_link(link_id: int) -> dict[str, Any]:
    link = get_cascade_link(link_id)
    state = str(link["state"])
    if state == "preparing_exit":
        client_id = int(link.get("service_client_id") or 0)
        if not client_id:
            return link
        client = dict(find_awg_client(client_id))
        deployment = str(client.get("deployment_state") or "active")
        if deployment == "error":
            with connect() as con:
                con.execute(
                    "UPDATE cascade_links SET state='error', last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (str(client.get("deployment_error") or "Сервер выхода не применил служебное подключение")[:1024], int(link_id)),
                )
            return get_cascade_link(link_id)
        if deployment != "active":
            return link
        with connect() as con:
            con.execute("UPDATE cascade_links SET state='preparing_entry', updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(link_id),))
        return _configure_entry(get_cascade_link(link_id))
    if state == "preparing_entry" and link.get("entry_job_id"):
        job = get_job(int(link["entry_job_id"]))
        if job["state"] == "error":
            with connect() as con:
                con.execute(
                    "UPDATE cascade_links SET state='error', last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (str(job.get("result", {}).get("message") or "Сервер подключения не включил Cascade")[:1024], int(link_id)),
                )
        elif job["state"] == "success":
            with connect() as con:
                con.execute(
                    "UPDATE cascade_links SET state='active', enabled=1, last_exit_ip=?, last_error='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (str(link["exit"].get("public_ipv4") or link["exit"].get("public_host") or ""), int(link_id)),
                )
        return get_cascade_link(link_id)
    if state == "disabling_entry" and link.get("entry_job_id"):
        job = get_job(int(link["entry_job_id"]))
        if job["state"] == "success":
            return _remove_exit_service(link)
        if job["state"] == "error":
            with connect() as con:
                con.execute("UPDATE cascade_links SET state='error', last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (str(job.get("result", {}).get("message") or "Не удалось вернуть прямой выход")[:1024], int(link_id)))
        return get_cascade_link(link_id)
    if state == "disabling_exit":
        client_id = int(link.get("service_client_id") or 0)
        if not client_id:
            _mark_disabled(link_id)
            return get_cascade_link(link_id)
        try:
            client = dict(find_awg_client(client_id))
        except AWGPanelError:
            _mark_disabled(link_id)
            return get_cascade_link(link_id)
        if str(client.get("deployment_state")) == "error":
            with connect() as con:
                con.execute("UPDATE cascade_links SET state='error', last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (str(client.get("deployment_error") or "Не удалось удалить служебное подключение")[:1024], int(link_id)))
        return get_cascade_link(link_id)
    return link


def reconcile_all_cascades() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for link in list_cascade_links(include_disabled=False):
        try:
            result.append(reconcile_cascade_link(int(link["id"])))
        except Exception as exc:
            with connect() as con:
                con.execute("UPDATE cascade_links SET state='error', last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (str(exc)[:1024], int(link["id"])))
            result.append(get_cascade_link(int(link["id"])))
    return result


def _mark_disabled(link_id: int) -> None:
    with connect() as con:
        con.execute(
            "UPDATE cascade_links SET enabled=0, state='disabled', service_client_id=NULL, "
            "outbound_id=NULL, entry_job_id=NULL, exit_job_id=NULL, last_error='', "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(link_id),),
        )


def _remove_exit_service(link: dict[str, Any]) -> dict[str, Any]:
    client_id = int(link.get("service_client_id") or 0)
    if not client_id:
        _mark_disabled(int(link["id"]))
        return get_cascade_link(int(link["id"]))
    try:
        client = dict(find_awg_client(client_id))
    except AWGPanelError:
        _mark_disabled(int(link["id"]))
        return get_cascade_link(int(link["id"]))
    if client.get("node_id"):
        result = remove_remote_service_client(client_id)
        with connect() as con:
            con.execute(
                "UPDATE cascade_links SET state='disabling_exit', exit_job_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (int(result["job"]["id"]), int(link["id"])),
            )
    else:
        delete_awg_service_clients(_role(int(link["id"])))
        _mark_disabled(int(link["id"]))
    return get_cascade_link(int(link["id"]))


def disable_cascade_link(link_id: int) -> dict[str, Any]:
    link = get_cascade_link(link_id)
    if str(link["state"]) == "disabled":
        return link
    entry = link["entry"]
    if entry.get("is_local"):
        outbound_id = int(link.get("outbound_id") or 0)
        changed_addresses = [
            str(row.get("address") or "") for row in map(dict, list_awg_clients(local_only=True))
            if outbound_id and str(row.get("egress_mode") or "") == "outbound"
            and int(row.get("outbound_id") or 0) == outbound_id
        ]
        if outbound_id:
            with connect() as con:
                con.execute(
                    "UPDATE awg_clients SET egress_mode='awg_gateway', outbound_id=NULL, updated_at=CURRENT_TIMESTAMP "
                    "WHERE node_id IS NULL AND system_role='' AND outbound_id=?",
                    (outbound_id,),
                )
            try:
                delete_outbound(outbound_id)
            except AWGPanelError:
                apply_egress_runtime()
        flush_client_connections(changed_addresses)
        return _remove_exit_service(get_cascade_link(link_id))
    payload = {
        "mode": "disable_cascade",
        "link_id": int(link_id),
        "managed_peers": _managed_peer_snapshot(int(entry["id"])),
    }
    job = queue_job(int(entry["id"]), "apply_awg_config", payload)
    with connect() as con:
        con.execute(
            "UPDATE cascade_links SET state='disabling_entry', entry_job_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(job["id"]), int(link_id)),
        )
    return get_cascade_link(link_id)


def test_cascade_link(link_id: int) -> dict[str, Any]:
    link = get_cascade_link(link_id)
    if str(link["state"]) != "active":
        raise ValueError("Cascade ещё не готов")
    if link["entry"].get("is_local"):
        from .cascade import _public_ip_via_outbound
        outbound_id = int(link.get("outbound_id") or 0)
        runtime = traffic_runtime_status()
        profile = next((item for item in runtime.get("profiles", []) if int(item.get("id", -1)) == outbound_id), None)
        if not profile or not profile.get("healthy"):
            raise ValueError("Маршрут Cascade на сервере подключения не готов")
        exit_ip, error = _public_ip_via_outbound(outbound_id)
        if error and not exit_ip:
            raise ValueError(error)
        with connect() as con:
            con.execute(
                "UPDATE cascade_links SET last_test_at=?, last_exit_ip=?, last_error='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (_stamp(), exit_ip, int(link_id)),
            )
        return get_cascade_link(link_id)
    job = queue_job(
        int(link["entry"]["id"]),
        "apply_awg_config",
        {"mode": "test_cascade", "link_id": int(link_id)},
    )
    with connect() as con:
        con.execute("UPDATE cascade_links SET entry_job_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(job["id"]), int(link_id)))
    return get_cascade_link(link_id)


def handle_job_completion(node_id: int, job: dict[str, Any], ok: bool, result: dict[str, Any]) -> None:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    mode = str(payload.get("mode") or "")
    link_id = int(payload.get("link_id") or 0)
    if mode in {"configure_cascade", "disable_cascade", "test_cascade"} and link_id:
        with connect() as con:
            if mode == "configure_cascade":
                con.execute(
                    "UPDATE cascade_links SET state=?, enabled=?, last_exit_ip=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND entry_node_id=?",
                    (
                        "active" if ok and result.get("cascade_active") else "error",
                        1,
                        str(result.get("exit_public_ip") or ""),
                        "" if ok else str(result.get("message") or "Ошибка включения Cascade")[:1024],
                        link_id, int(node_id),
                    ),
                )
            elif mode == "disable_cascade":
                con.execute(
                    "UPDATE cascade_links SET state=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND entry_node_id=?",
                    ("disabling_exit" if ok else "error", "" if ok else str(result.get("message") or "Ошибка отключения Cascade")[:1024], link_id, int(node_id)),
                )
            else:
                con.execute(
                    "UPDATE cascade_links SET last_test_at=?, last_exit_ip=CASE WHEN ?<>'' THEN ? ELSE last_exit_ip END, "
                    "last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND entry_node_id=?",
                    (_stamp(), str(result.get("exit_ip") or ""), str(result.get("exit_ip") or ""), "" if ok else str(result.get("message") or "Проверка Cascade не выполнена")[:1024], link_id, int(node_id)),
                )
        if mode == "disable_cascade" and ok:
            _remove_exit_service(get_cascade_link(link_id))
        return
    if mode == "sync_clients":
        for link in list_cascade_links(include_disabled=False):
            if int(link["exit_node_id"]) == int(node_id):
                try:
                    reconcile_cascade_link(int(link["id"]))
                except Exception:
                    pass
