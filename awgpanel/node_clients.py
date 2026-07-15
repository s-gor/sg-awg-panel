from __future__ import annotations

import ipaddress
import json
import secrets
import sqlite3
from typing import Any, Iterable

from .db import connect, init_db
from .errors import AWGPanelError
from .node_manager import get_job, get_node, pool_interface, queue_job


def _runtime(node: dict[str, Any]) -> dict[str, Any]:
    runtime = node.get("awg_runtime")
    return dict(runtime) if isinstance(runtime, dict) else {}


def _server_public_key(runtime: dict[str, Any]) -> str:
    return str(runtime.get("server_public_key") or runtime.get("public_key") or "").strip()


def require_ready_node(node_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    node = get_node(int(node_id))
    if bool(node.get("is_local")):
        raise ValueError("Для Controller используется локальный AWG Server")
    if str(node.get("effective_state")) != "online":
        raise ValueError("SG-Node не в сети. Подключите её и нажмите «Обновить подключение ноды»")
    runtime = _runtime(node)
    try:
        port = int(runtime.get("listen_port") or node.get("public_port") or 0)
    except (TypeError, ValueError):
        port = 0
    if port != 585:
        raise ValueError(
            f"SG-Node сообщает UDP-порт {port or 'не определён'}. Для клиентов должен использоваться обычный порт 585"
        )
    if str(node.get("service_awg")) != "active":
        raise ValueError("AmneziaWG на SG-Node не запущен")
    public_key = _server_public_key(runtime)
    slot = node.get("node_slot")
    desired_network = str(node.get("vpn_network") or "").strip()
    if slot is None or not desired_network:
        raise ValueError("Для SG-Node не назначен отдельный VPN-пул")
    try:
        network = ipaddress.ip_network(desired_network, strict=True)
        desired_interface = ipaddress.ip_interface(pool_interface(int(slot)))
    except (TypeError, ValueError) as exc:
        raise ValueError("Controller хранит некорректный VPN-пул SG-Node") from exc
    if int(slot) not in range(1, 13) or network != ipaddress.ip_network(f"10.77.{int(slot)}.0/24"):
        raise ValueError("VPN-пул SG-Node не соответствует её номеру")
    if not public_key:
        raise ValueError(
            "SG-Node ещё не передала публичный ключ работающего awg0. Нажмите «Обновить состояние»"
        )
    # Keep the reported pool for safe migration of existing local peers, but
    # allocate every new Controller-managed client from the assigned RC5 pool.
    runtime["reported_server_network"] = str(runtime.get("server_network") or "")
    runtime["reported_interface_address"] = str(runtime.get("interface_address") or "")
    runtime["listen_port"] = port
    runtime["public_key"] = public_key
    runtime["server_public_key"] = public_key
    runtime["server_network"] = str(network)
    runtime["interface_address"] = str(desired_interface)
    runtime["node_slot"] = int(slot)
    return node, runtime


def _runtime_peer_addresses(
    runtime: dict[str, Any],
    *,
    managed_public_keys: set[str] | None = None,
    unmanaged_only: bool = False,
) -> dict[str, str]:
    """Return real /32 addresses currently present on the Node awg0.

    New agents mark Controller-managed peers explicitly. For compatibility with
    an older heartbeat, a peer whose public key exists in Controller's database
    is also treated as managed. Everything else reserves its address for the
    local Node configuration.
    """
    peers = runtime.get("peers") if isinstance(runtime.get("peers"), dict) else {}
    claims = runtime.get("address_claims") if isinstance(runtime.get("address_claims"), list) else []
    managed_keys = managed_public_keys or set()
    result: dict[str, str] = {}
    try:
        reported_network = ipaddress.ip_network(
            str(runtime.get("reported_server_network") or runtime.get("server_network") or ""),
            strict=True,
        )
        desired_network = ipaddress.ip_network(str(runtime.get("server_network") or ""), strict=True)
    except ValueError:
        reported_network = desired_network = None

    def translated(value: ipaddress.IPv4Address) -> ipaddress.IPv4Address:
        if reported_network is None or desired_network is None or value not in reported_network:
            return value
        offset = int(value) - int(reported_network.network_address)
        candidate = ipaddress.ip_address(int(desired_network.network_address) + offset)
        return candidate if candidate in desired_network else value
    for details in claims:
        if not isinstance(details, dict):
            continue
        public_key = str(details.get("key") or "")
        if unmanaged_only and (bool(details.get("managed")) or public_key in managed_keys):
            continue
        raw_address = str(details.get("address") or "").strip()
        try:
            route = ipaddress.ip_network(
                raw_address if "/" in raw_address else f"{raw_address}/32",
                strict=False,
            )
        except ValueError:
            continue
        label = str(details.get("name") or "").strip()
        result[str(translated(route.network_address))] = label or public_key[:12]
    if claims:
        return result
    for public_key, details in peers.items():
        if not isinstance(details, dict):
            continue
        if unmanaged_only:
            if bool(details.get("managed")) or str(public_key) in managed_keys:
                continue
        raw_routes = details.get("allowed_ips")
        if isinstance(raw_routes, list):
            values = [str(value) for value in raw_routes]
        else:
            values = str(raw_routes or "").split(",")
        for raw in values:
            try:
                route = ipaddress.ip_network(raw.strip(), strict=False)
            except ValueError:
                continue
            if route.version != 4 or route.prefixlen != 32:
                continue
            label = str(details.get("name") or "").strip()
            result[str(translated(route.network_address))] = label or str(public_key)[:12]
    return result


def _first_free_address(
    network: ipaddress.IPv4Network,
    server_ip: ipaddress.IPv4Address,
    used: set[str],
) -> str:
    for host in network.hosts():
        if host == server_ip or str(host) in used:
            continue
        return f"{host}/32"
    raise AWGPanelError("В сети этой SG-Node больше нет свободных адресов")


def _next_address(node_id: int, runtime: dict[str, Any]) -> str:
    network = ipaddress.ip_network(str(runtime["server_network"]), strict=True)
    server_ip = ipaddress.ip_interface(str(runtime["interface_address"])).ip
    with connect() as con:
        rows = con.execute(
            "SELECT address FROM awg_clients WHERE node_id=? AND deployment_state<>'deleting'",
            (int(node_id),),
        ).fetchall()
    used = {str(row["address"]).split("/", 1)[0] for row in rows}
    # The database only contains clients created through Controller. A Node can
    # already have local peers, so the heartbeat inventory is authoritative too.
    used.update(_runtime_peer_addresses(runtime))
    return _first_free_address(network, server_ip, used)


def _reconcile_managed_addresses(
    node_id: int,
    runtime: dict[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[int]]:
    """Move Controller clients away from addresses occupied by local peers.

    This repairs records created by older builds that only checked Controller's
    database. It keeps the oldest valid managed address and reallocates only the
    conflicting managed client, then its freshly rendered profile becomes the
    single source of truth.
    """
    network = ipaddress.ip_network(str(runtime["server_network"]), strict=True)
    server_ip = ipaddress.ip_interface(str(runtime["interface_address"])).ip
    managed_keys = {
        str(row.get("public_key") or "") for row in rows
        if str(row.get("deployment_state") or "") != "deleting"
    }
    reserved = _runtime_peer_addresses(
        runtime, managed_public_keys=managed_keys, unmanaged_only=True
    )
    used = set(reserved)
    changes: list[tuple[str, int]] = []

    for row in sorted(rows, key=lambda item: int(item.get("id") or 0)):
        if str(row.get("deployment_state") or "") == "deleting":
            continue
        if str(row.get("system_role") or "").startswith("cascade_exit_"):
            continue
        try:
            address = ipaddress.ip_interface(str(row.get("address") or ""))
        except ValueError:
            address = None
        valid = bool(
            address
            and address.version == 4
            and address.network.prefixlen == 32
            and address.ip in network
            and address.ip != server_ip
            and str(address.ip) not in used
        )
        if valid:
            used.add(str(address.ip))
            continue
        new_address = _first_free_address(network, server_ip, used)
        used.add(new_address.split("/", 1)[0])
        row["address"] = new_address
        changes.append((new_address, int(row["id"])))

    if changes:
        with connect() as con:
            con.executemany(
                "UPDATE awg_clients SET address=?, deployment_state='queued', "
                "deployment_error='', updated_at=CURRENT_TIMESTAMP WHERE id=? AND node_id=?",
                [(address, client_id, int(node_id)) for address, client_id in changes],
            )
    return rows, [client_id for _address, client_id in changes]


def _effective_enabled(row: dict[str, Any]) -> bool:
    from .core import client_lifecycle

    return bool(client_lifecycle(row)["effective_enabled"])


def _peer_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "public_key": str(row["public_key"]),
        "preshared_key": str(row["preshared_key"]),
        "address": str(row["address"]),
        "advertised_networks": str(row.get("advertised_networks") or ""),
        "system_role": str(row.get("system_role") or ""),
    }


def queue_node_client_sync(
    node_id: int,
    *,
    target_client_ids: Iterable[int],
    delete_client_ids: Iterable[int] = (),
) -> dict[str, Any]:
    node, runtime = require_ready_node(int(node_id))
    target_ids = sorted({int(value) for value in target_client_ids})
    delete_ids = sorted({int(value) for value in delete_client_ids})
    with connect() as con:
        rows = [
            dict(row)
            for row in con.execute(
                "SELECT * FROM awg_clients WHERE node_id=? ORDER BY id",
                (int(node_id),),
            ).fetchall()
        ]
    rows, reassigned_ids = _reconcile_managed_addresses(int(node_id), runtime, rows)
    target_ids = sorted(set(target_ids).union(reassigned_ids))
    peers = [
        _peer_payload(row)
        for row in rows
        if str(row.get("deployment_state")) != "deleting" and _effective_enabled(row)
    ]
    payload = {
        "mode": "sync_clients",
        "expected": {
            "listen_port": 585,
            "server_public_key": _server_public_key(runtime),
            "server_network": str(runtime["server_network"]),
            "interface_address": str(runtime["interface_address"]),
            "node_slot": int(runtime["node_slot"]),
        },
        "peers": peers,
        "target_client_ids": target_ids,
        "delete_client_ids": delete_ids,
    }
    job = queue_job(int(node_id), "apply_awg_config", payload)
    if target_ids:
        placeholders = ",".join("?" for _ in target_ids)
        if delete_ids:
            delete_placeholders = ",".join("?" for _ in delete_ids)
            state_sql = f"CASE WHEN id IN ({delete_placeholders}) THEN 'deleting' ELSE 'queued' END"
            parameters = [int(job["id"]), *delete_ids, *target_ids, int(node_id)]
        else:
            state_sql = "'queued'"
            parameters = [int(job["id"]), *target_ids, int(node_id)]
        with connect() as con:
            con.execute(
                f"UPDATE awg_clients SET deployment_job_id=?, deployment_state={state_sql}, "
                f"deployment_error='', updated_at=CURRENT_TIMESTAMP "
                f"WHERE id IN ({placeholders}) AND node_id=?",
                parameters,
            )
    return job


def queue_initial_pool_sync(node_id: int) -> dict[str, Any] | None:
    """Queue one pool-migration/client-sync job after a Node heartbeat."""
    node, runtime = require_ready_node(int(node_id))
    reported = str(runtime.get("reported_server_network") or "")
    desired = str(runtime.get("server_network") or "")
    if reported == desired and str(runtime.get("reported_interface_address") or "") == str(runtime.get("interface_address") or ""):
        return None
    with connect() as con:
        pending = con.execute(
            "SELECT id FROM node_jobs WHERE node_id=? AND kind='apply_awg_config' "
            "AND state IN ('queued','claimed') ORDER BY id DESC",
            (int(node_id),),
        ).fetchall()
    for row in pending:
        job = get_job(int(row["id"]))
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        expected = payload.get("expected") if isinstance(payload.get("expected"), dict) else {}
        if str(payload.get("mode") or "") == "sync_clients" and str(expected.get("server_network") or "") == desired:
            return job
    with connect() as con:
        target_ids = [
            int(row["id"])
            for row in con.execute(
                "SELECT id FROM awg_clients WHERE node_id=? AND deployment_state<>'deleting'",
                (int(node_id),),
            ).fetchall()
        ]
    return queue_node_client_sync(int(node_id), target_client_ids=target_ids)


def _service_address() -> str:
    with connect() as con:
        used = {
            str(row["address"]).split("/", 1)[0]
            for row in con.execute("SELECT address FROM awg_clients WHERE system_role LIKE 'cascade_exit_%'").fetchall()
        }
    for third in range(1, 255):
        value = f"10.254.{third}.2"
        if value not in used:
            return f"{value}/32"
    raise AWGPanelError("Не удалось выделить служебный адрес Cascade")


def add_remote_service_client(
    node_id: int, *, name: str, system_role: str, comment: str = ""
):
    from .core import AWG_NAME_RE, _keypair, _psk

    init_db()
    require_ready_node(int(node_id))
    clean_name = str(name or "").strip()
    role = str(system_role or "").strip()[:48]
    if not AWG_NAME_RE.fullmatch(clean_name):
        raise ValueError("Имя служебного клиента должно содержать от 1 до 64 обычных символов")
    if not role.startswith("cascade_exit_") or not role.replace("_", "").isalnum():
        raise ValueError("Некорректная роль служебного клиента Cascade")
    private_key, public_key = _keypair()
    preshared_key = _psk()
    address = _service_address()
    try:
        with connect() as con:
            cursor = con.execute(
                """
                INSERT INTO awg_clients (
                    name, address, private_key, public_key, preshared_key, comment,
                    allowed_ips, access_token, access_enabled, node_id, system_role,
                    deployment_state, deployment_error, deployed_enabled
                ) VALUES (?, ?, ?, ?, ?, ?, '0.0.0.0/0', '', 0, ?, ?, 'queued', '', 0)
                """,
                (clean_name, address, private_key, public_key, preshared_key,
                 str(comment or "").strip(), int(node_id), role),
            )
            client_id = int(cursor.lastrowid)
        queue_node_client_sync(int(node_id), target_client_ids=[client_id])
    except sqlite3.IntegrityError as exc:
        raise AWGPanelError("Служебный клиент Cascade с таким именем или адресом уже существует") from exc
    except Exception:
        with connect() as con:
            con.execute("DELETE FROM awg_clients WHERE id=?", (locals().get("client_id", -1),))
        raise
    from .core import find_awg_client
    return find_awg_client(client_id)


def remove_remote_service_client(client_id: int) -> dict[str, Any]:
    from .core import find_awg_client

    row = dict(find_awg_client(int(client_id)))
    node_id = int(row.get("node_id") or 0)
    if not node_id or not str(row.get("system_role") or "").startswith("cascade_exit_"):
        raise ValueError("Это не служебный клиент удалённой SG-Node")
    with connect() as con:
        con.execute(
            "UPDATE awg_clients SET enabled=0, deployment_state='deleting', "
            "deployment_error='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(client_id),),
        )
    job = queue_node_client_sync(
        node_id, target_client_ids=[int(client_id)], delete_client_ids=[int(client_id)]
    )
    return {"client": row, "job": job}


def add_remote_client(
    node_id: int,
    *,
    name: str,
    comment: str = "",
    expires_at: object | None = None,
):
    from .core import AWG_NAME_RE, _keypair, _psk, normalize_client_expiry

    init_db()
    node, runtime = require_ready_node(int(node_id))
    clean_name = str(name or "").strip()
    if not AWG_NAME_RE.fullmatch(clean_name):
        raise ValueError("Имя клиента должно содержать от 1 до 64 обычных символов")
    private_key, public_key = _keypair()
    preshared_key = _psk()
    address = _next_address(int(node_id), runtime)
    normalized_expiry = normalize_client_expiry(expires_at)
    try:
        with connect() as con:
            cursor = con.execute(
                """
                INSERT INTO awg_clients (
                    name, address, private_key, public_key, preshared_key, comment,
                    allowed_ips, access_token, access_enabled, expires_at, node_id,
                    deployment_state, deployment_error, deployed_enabled
                ) VALUES (?, ?, ?, ?, ?, ?, '0.0.0.0/0', ?, 1, ?, ?, 'queued', '', 0)
                """,
                (
                    clean_name,
                    address,
                    private_key,
                    public_key,
                    preshared_key,
                    str(comment or "").strip(),
                    secrets.token_urlsafe(24),
                    normalized_expiry,
                    int(node_id),
                ),
            )
            client_id = int(cursor.lastrowid)
        queue_node_client_sync(int(node_id), target_client_ids=[client_id])
    except sqlite3.IntegrityError as exc:
        raise AWGPanelError("Клиент с таким именем уже существует") from exc
    except Exception:
        with connect() as con:
            con.execute("DELETE FROM awg_clients WHERE id=?", (locals().get("client_id", -1),))
        raise
    from .core import find_awg_client

    return find_awg_client(client_id)


def node_client_context(client: object) -> tuple[dict[str, Any], dict[str, Any]]:
    row = dict(client)
    node_id = int(row.get("node_id") or 0)
    if not node_id:
        raise ValueError("Клиент относится к локальному Controller")
    return require_ready_node(node_id)


def render_remote_client_config(client: object) -> str:
    from .core import client_lifecycle, get_awg_settings
    from .traffic import exported_allowed_ips

    row = dict(client)
    if str(row.get("deployment_state")) != "active":
        if str(row.get("deployment_state")) == "error":
            raise AWGPanelError(str(row.get("deployment_error") or "Клиент не применён на SG-Node"))
        raise AWGPanelError("Клиент ещё применяется на SG-Node. Страница обновится автоматически после подтверждения Agent")
    lifecycle = client_lifecycle(row)
    if not bool(row.get("enabled")):
        raise AWGPanelError("Клиент отключён администратором")
    if bool(lifecycle["expired"]):
        raise AWGPanelError("Срок действия клиента истёк")
    node, runtime = node_client_context(row)
    server_public_key = _server_public_key(runtime)
    if not server_public_key or server_public_key == str(row.get("public_key") or "").strip():
        raise AWGPanelError(
            "Подключение SG-Node нужно обновить. Откройте Cluster и нажмите «Обновить подключение ноды»"
        )
    endpoint_host = str(node.get("public_ipv4") or node.get("public_host") or "").strip()
    if not endpoint_host:
        raise AWGPanelError("SG-Node не передала публичный IP")
    if ":" in endpoint_host and not endpoint_host.startswith("["):
        endpoint_host = f"[{endpoint_host}]"
    allowed_ips = exported_allowed_ips(row.get("allowed_ips"), row.get("excluded_ips"), [])
    if not allowed_ips:
        raise AWGPanelError("После применения исключений у клиента не осталось маршрутов")
    settings = get_awg_settings()
    # SG Client and other WireGuard importers are most reliable when a managed
    # SG-Node profile uses the same compact format as a local panel profile.
    # A single primary DNS value also avoids parser-specific comma handling.
    dns_values = [
        item.strip()
        for item in str(row.get("dns_servers") or settings["dns_servers"] or "1.1.1.1").split(",")
        if item.strip()
    ]
    dns_value = dns_values[0] if dns_values else "1.1.1.1"
    try:
        mtu = int(row.get("mtu") or runtime.get("mtu") or 1280)
    except (TypeError, ValueError):
        mtu = 1280
    masking = runtime.get("masking") if isinstance(runtime.get("masking"), dict) else {}
    masking_lines: list[str] = []
    defaults = {"jc": 6, "jmin": 64, "jmax": 128, "s1": 48, "s2": 48, "s3": 32, "s4": 16}
    labels = {"jc": "Jc", "jmin": "Jmin", "jmax": "Jmax", "s1": "S1", "s2": "S2", "s3": "S3", "s4": "S4"}
    for key in ("jc", "jmin", "jmax", "s1", "s2", "s3", "s4"):
        masking_lines.append(f"{labels[key]} = {masking.get(key, defaults[key])}")
    for key in ("h1", "h2", "h3", "h4", "i1", "i2", "i3", "i4", "i5"):
        value = str(masking.get(key) or "").strip()
        if value:
            masking_lines.append(f"{key.upper()} = {value}")
    node_name = str(node.get("name") or "SG-Node").strip() or "SG-Node"
    client_name = str(row.get("name") or "AmneziaWG").strip() or "AmneziaWG"
    profile_name = f"{node_name}/{client_name}"
    lines = [
        f"# Name = {profile_name}",
        f"# Client = {client_name}",
        "# Source = SG-AWG-Panel",
        "",
        "[Interface]",
        f"Address = {row['address']}",
        f"DNS = {dns_value}",
        f"PrivateKey = {row['private_key']}",
        f"MTU = {mtu}",
        *masking_lines,
        "",
        "[Peer]",
        f"PublicKey = {server_public_key}",
        f"PresharedKey = {row['preshared_key']}",
        f"AllowedIPs = {allowed_ips}",
        f"Endpoint = {endpoint_host}:{int(runtime['listen_port'])}",
        "PersistentKeepalive = 25",
    ]
    return "\n".join(lines).rstrip() + "\n"


def remote_peer_stats(client: object) -> dict[str, int]:
    row = dict(client)
    node_id = int(row.get("node_id") or 0)
    if not node_id:
        return {"latest_handshake": 0, "rx": 0, "tx": 0}
    node = get_node(node_id)
    runtime = _runtime(node)
    peers = runtime.get("peers") if isinstance(runtime.get("peers"), dict) else {}
    stats = peers.get(str(row.get("public_key"))) if isinstance(peers, dict) else None
    if not isinstance(stats, dict):
        return {"latest_handshake": 0, "rx": 0, "tx": 0}
    result: dict[str, int] = {}
    for key in ("latest_handshake", "rx", "tx"):
        try:
            result[key] = max(0, int(stats.get(key) or 0))
        except (TypeError, ValueError):
            result[key] = 0
    return result
