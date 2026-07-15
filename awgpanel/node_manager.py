from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import secrets
import shlex
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import connect, init_db
from .errors import AWGPanelError
from .geography import country_flag, country_name, normalize_country_code

NODE_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
ALLOWED_JOB_KINDS = {
    "refresh",
    "diagnostics",
    "restart_awg",
    "restart_traffic",
    "restart_nginx",
    "apply_awg_config",
}
AGENT_OFFLINE_AFTER_SECONDS = 95
ENROLLMENT_TTL_MINUTES = 30
MAX_NODE_SLOTS = 12


def pool_network(slot: int) -> str:
    value = int(slot)
    if not 0 <= value <= MAX_NODE_SLOTS:
        raise ValueError("Некорректный номер VPN-пула")
    return f"10.77.{value}.0/24"


def pool_interface(slot: int) -> str:
    return f"10.77.{int(slot)}.1/24"


def _allocate_pool(con, node_id: int) -> tuple[int, str]:
    row = con.execute("SELECT node_slot,vpn_network FROM cluster_nodes WHERE id=?", (int(node_id),)).fetchone()
    if row is None:
        raise AWGPanelError("SG-Node не найдена")
    if row["node_slot"] is not None and str(row["vpn_network"] or ""):
        return int(row["node_slot"]), str(row["vpn_network"])
    for slot in range(1, MAX_NODE_SLOTS + 1):
        if con.execute("SELECT 1 FROM cluster_pool_slots WHERE slot=?", (slot,)).fetchone() is not None:
            continue
        network = pool_network(slot)
        con.execute("INSERT INTO cluster_pool_slots(slot,vpn_network,node_id) VALUES(?,?,?)", (slot, network, int(node_id)))
        con.execute("UPDATE cluster_nodes SET node_slot=?,vpn_network=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (slot, network, int(node_id)))
        return slot, network
    raise ValueError("Достигнут предел: подключено или зарезервировано 12 SG-Node")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime | None = None) -> str:
    return (value or _utcnow()).replace(microsecond=0).isoformat()


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


def _local_machine_id() -> str:
    try:
        return Path("/etc/machine-id").read_text(encoding="utf-8").strip()[:128]
    except OSError:
        return ""


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    slug = slug[:64].strip("-")
    return slug or f"node-{secrets.token_hex(3)}"


def _validate_host(value: str) -> str:
    host = str(value or "").strip()
    if not host:
        return ""
    if len(host) > 253 or any(char.isspace() for char in host):
        raise ValueError("Укажите корректный IP или домен SG-Node")
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        pass
    labels = host.rstrip(".").split(".")
    if not labels or any(
        not label or len(label) > 63 or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("Укажите корректный IP или домен SG-Node")
    return host.rstrip(".")


def _unique_slug(name: str) -> str:
    base = _slugify(name)
    with connect() as con:
        candidate = base
        number = 2
        while con.execute("SELECT 1 FROM cluster_nodes WHERE slug=?", (candidate,)).fetchone():
            suffix = f"-{number}"
            candidate = f"{base[:64-len(suffix)].rstrip('-')}{suffix}"
            number += 1
        return candidate


def _row_dict(row) -> dict[str, Any]:
    result = dict(row)
    try:
        result["capabilities"] = json.loads(str(result.get("capabilities_json") or "{}"))
    except (TypeError, ValueError):
        result["capabilities"] = {}
    try:
        result["awg_runtime"] = json.loads(str(result.get("awg_runtime_json") or "{}"))
    except (TypeError, ValueError):
        result["awg_runtime"] = {}
    last_seen = _parse_stamp(result.get("last_seen_at"))
    state = str(result.get("state") or "pending")
    if (
        not bool(result.get("is_local"))
        and state in {"online", "offline"}
        and (last_seen is None or (_utcnow() - last_seen).total_seconds() > AGENT_OFFLINE_AFTER_SECONDS)
    ):
        state = "offline"
    result["effective_state"] = state
    result["online"] = state == "online"
    result["country_code"] = normalize_country_code(result.get("country_code"))
    result["country_flag"] = country_flag(result["country_code"])
    result["country_name"] = country_name(result["country_code"])
    slot = result.get("node_slot")
    if slot is not None and not str(result.get("vpn_network") or ""):
        result["vpn_network"] = pool_network(int(slot))
    result["pool_interface"] = pool_interface(int(slot)) if slot is not None else ""
    return result


def ensure_local_node(
    *, name: str = "SG-AWG Controller", public_host: str = "", country_code: str = ""
) -> dict[str, Any]:
    init_db()
    with connect() as con:
        row = con.execute("SELECT * FROM cluster_nodes WHERE is_local=1").fetchone()
        if row is None:
            con.execute(
                """
                INSERT INTO cluster_nodes (
                    slug, name, role, state, is_local, node_slot, vpn_network, public_host, country_code,
                    machine_id, registered_at, last_seen_at, agent_version, capabilities_json
                ) VALUES ('controller', ?, 'controller', 'online', 1, 0, '10.77.0.0/24', ?, ?, ?, ?, ?, 'built-in', ?)
                """,
                (
                    name.strip() or "SG-AWG Controller",
                    public_host.strip(),
                    normalize_country_code(country_code),
                    _local_machine_id(),
                    _stamp(),
                    _stamp(),
                    json.dumps({"controller": True, "amneziawg": True}),
                ),
            )
        else:
            # A full installation can later be enrolled as an SG-Node. Its
            # Agent then assigns slot 1..12 in the local database. Do not turn
            # that server back into Controller slot 0 when its local UI opens.
            try:
                assigned_slot = int(row["node_slot"] if row["node_slot"] is not None else 0)
            except (TypeError, ValueError):
                assigned_slot = 0
            assigned_network = str(row["vpn_network"] or "")
            if not 1 <= assigned_slot <= MAX_NODE_SLOTS or assigned_network != pool_network(assigned_slot):
                assigned_slot = 0
                assigned_network = pool_network(0)
            con.execute(
                """
                UPDATE cluster_nodes
                SET name=?, public_host=?, node_slot=?, vpn_network=?, state='online', last_seen_at=?,
                    machine_id=CASE WHEN ?<>'' THEN ? ELSE machine_id END,
                    country_code=CASE
                        WHEN country_mode='auto' AND ?<>'' THEN ? ELSE country_code END,
                    country_updated_at=CASE
                        WHEN country_mode='auto' AND ?<>'' THEN CURRENT_TIMESTAMP ELSE country_updated_at END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    name.strip() or str(row["name"]),
                    public_host.strip() or str(row["public_host"]),
                    assigned_slot,
                    assigned_network,
                    _stamp(),
                    _local_machine_id(),
                    _local_machine_id(),
                    normalize_country_code(country_code),
                    normalize_country_code(country_code),
                    normalize_country_code(country_code),
                    int(row["id"]),
                ),
            )
        local_row = con.execute(
            "SELECT id,node_slot,vpn_network FROM cluster_nodes WHERE is_local=1"
        ).fetchone()
        local_id = int(local_row["id"])
        local_slot = int(local_row["node_slot"] or 0)
        local_network = str(local_row["vpn_network"] or pool_network(local_slot))
        con.execute("DELETE FROM cluster_pool_slots WHERE node_id=?", (local_id,))
        con.execute(
            "INSERT INTO cluster_pool_slots(slot,vpn_network,node_id,retired_at) "
            "VALUES(?,?,?,NULL) "
            "ON CONFLICT(slot) DO UPDATE SET vpn_network=excluded.vpn_network, "
            "node_id=excluded.node_id,retired_at=NULL",
            (local_slot, local_network, local_id),
        )
    return get_node_by_slug("controller")


def list_nodes() -> list[dict[str, Any]]:
    init_db()
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM cluster_nodes ORDER BY is_local DESC, name COLLATE NOCASE, id"
        ).fetchall()
    return [_row_dict(row) for row in rows]


def collapse_duplicate_nodes(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Return one visible SG-Node per human-readable name.

    Older builds could leave several enrolled records with the same name. They
    are not deleted automatically because one of them may still own clients or
    Cascade history. The Cluster overview nevertheless has to remain simple:
    prefer the live/enrolled record and hide the older duplicates.
    """
    local = [item for item in rows if bool(item.get("is_local"))]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in rows:
        if bool(item.get("is_local")):
            continue
        key = str(item.get("name") or item.get("slug") or item.get("id") or "").strip().casefold()
        grouped.setdefault(key, []).append(item)

    visible: list[dict[str, Any]] = []
    hidden = 0
    for group in grouped.values():
        preferred = max(
            group,
            key=lambda item: (
                3 if str(item.get("effective_state") or "") == "online" else
                2 if str(item.get("effective_state") or "") in {"offline", "error"} else
                1 if str(item.get("agent_token_hash") or "") or item.get("registered_at") or item.get("last_seen_at") else 0,
                int(item.get("id") or 0),
            ),
        )
        visible.append(preferred)
        hidden += max(0, len(group) - 1)
    visible.sort(key=lambda item: (str(item.get("name") or "").casefold(), int(item.get("id") or 0)))
    return local + visible, hidden


def find_remote_node_by_name(name: object) -> dict[str, Any] | None:
    """Return the best existing remote SG-Node with the same visible name."""
    clean_name = str(name or "").strip()
    if not clean_name:
        return None
    init_db()
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM cluster_nodes WHERE is_local=0 ORDER BY id DESC"
        ).fetchall()
    wanted = clean_name.casefold()
    matches = [
        row for row in rows
        if str(row["name"] or "").strip().casefold() == wanted
    ]
    if not matches:
        return None
    row = max(
        matches,
        key=lambda item: (
            1 if str(item["state"] or "") == "online" else 0,
            1 if item["registered_at"] or item["last_seen_at"] or str(item["agent_token_hash"] or "") else 0,
            int(item["id"]),
        ),
    )
    return _row_dict(row)


def cleanup_duplicate_pending_nodes() -> int:
    """Remove only safe, unused duplicate placeholders created before v208.

    A record is eligible only when it has never enrolled, has no clients and is
    not referenced by Cascade. An enrolled/online record always wins; otherwise
    the newest pending record is kept.
    """
    init_db()
    removed = 0
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM cluster_nodes WHERE is_local=0 ORDER BY id DESC"
        ).fetchall()
        groups: dict[str, list[Any]] = {}
        for row in rows:
            key = str(row["name"] or "").strip().casefold()
            if key:
                groups.setdefault(key, []).append(row)
        for group in groups.values():
            if len(group) < 2:
                continue
            keep = max(
                group,
                key=lambda row: (
                    1 if str(row["state"] or "") == "online" else 0,
                    1 if row["registered_at"] or row["last_seen_at"] or str(row["agent_token_hash"] or "") else 0,
                    int(row["id"]),
                ),
            )
            for row in group:
                if int(row["id"]) == int(keep["id"]):
                    continue
                node_id = int(row["id"])
                never_enrolled = (
                    str(row["state"] or "") == "pending"
                    and not str(row["agent_token_hash"] or "")
                    and not row["registered_at"]
                    and not row["last_seen_at"]
                )
                if not never_enrolled:
                    continue
                clients = con.execute(
                    "SELECT COUNT(*) FROM awg_clients WHERE node_id=?", (node_id,)
                ).fetchone()[0]
                links = con.execute(
                    "SELECT COUNT(*) FROM cascade_links WHERE entry_node_id=? OR exit_node_id=?",
                    (node_id, node_id),
                ).fetchone()[0]
                if int(clients) or int(links):
                    continue
                slot = row["node_slot"] if "node_slot" in row.keys() else None
                if slot is not None:
                    con.execute(
                        "UPDATE cluster_pool_slots SET node_id=NULL,retired_at=CURRENT_TIMESTAMP WHERE slot=?",
                        (int(slot),),
                    )
                con.execute("DELETE FROM cluster_nodes WHERE id=?", (node_id,))
                removed += 1
    return removed


def get_node(node_id: int) -> dict[str, Any]:
    init_db()
    with connect() as con:
        row = con.execute("SELECT * FROM cluster_nodes WHERE id=?", (int(node_id),)).fetchone()
    if row is None:
        raise AWGPanelError("SG-Node не найдена")
    return _row_dict(row)


def get_node_by_slug(slug: str) -> dict[str, Any]:
    init_db()
    with connect() as con:
        row = con.execute("SELECT * FROM cluster_nodes WHERE slug=?", (slug,)).fetchone()
    if row is None:
        raise AWGPanelError("SG-Node не найдена")
    return _row_dict(row)


def create_node(*, name: str, public_host: str = "", public_port: int = 585) -> tuple[dict[str, Any], str]:
    init_db()
    clean_name = str(name or "").strip()
    if not clean_name or len(clean_name) > 96:
        raise ValueError("Укажите понятное имя SG-Node длиной до 96 символов")
    if find_remote_node_by_name(clean_name) is not None:
        raise ValueError("SG-Node с таким именем уже существует")
    clean_host = _validate_host(public_host)
    port = int(public_port)
    if port != 585:
        raise ValueError("SG-Node использует обычный UDP-порт AmneziaWG 585")
    token = secrets.token_urlsafe(32)
    slug = _unique_slug(clean_name)
    expires = _stamp(_utcnow() + timedelta(minutes=ENROLLMENT_TTL_MINUTES))
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        cursor = con.execute(
            """
            INSERT INTO cluster_nodes (
                slug, name, role, state, is_local, public_host, public_port,
                enrollment_token_hash, enrollment_expires_at
            ) VALUES (?, ?, 'node', 'pending', 0, ?, ?, ?, ?)
            """,
            (slug, clean_name, clean_host, port, _token_hash(token), expires),
        )
        node_id = int(cursor.lastrowid)
        _allocate_pool(con, node_id)
    return get_node(node_id), token


def renew_enrollment(node_id: int) -> tuple[dict[str, Any], str]:
    node = get_node(node_id)
    if bool(node["is_local"]):
        raise ValueError("Для локального Controller код подключения не нужен")
    token = secrets.token_urlsafe(32)
    expires = _stamp(_utcnow() + timedelta(minutes=ENROLLMENT_TTL_MINUTES))
    with connect() as con:
        con.execute(
            """
            UPDATE cluster_nodes
            SET state='pending', enrollment_token_hash=?, enrollment_expires_at=?,
                agent_token_hash='', registered_at=NULL, last_error='',
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (_token_hash(token), expires, int(node_id)),
        )
    return get_node(node_id), token


def delete_node(node_id: int) -> None:
    node = get_node(node_id)
    if bool(node["is_local"]):
        raise ValueError("Локальный Controller нельзя удалить")
    with connect() as con:
        client_count = int(
            con.execute("SELECT COUNT(*) FROM awg_clients WHERE node_id=?", (int(node_id),)).fetchone()[0]
        )
        if client_count:
            raise ValueError(
                f"На SG-Node осталось клиентов: {client_count}. Сначала удалите их в разделе Clients"
            )
        slot = node.get("node_slot")
        if slot is not None:
            con.execute(
                "UPDATE cluster_pool_slots SET node_id=NULL,retired_at=CURRENT_TIMESTAMP WHERE slot=?",
                (int(slot),),
            )
        con.execute("DELETE FROM cluster_nodes WHERE id=?", (int(node_id),))


def node_install_command(controller_url: str) -> str:
    """Return one self-contained bootstrap command for a clean Ubuntu Node.

    curl cannot be assumed to exist on a minimal image, so the outer command
    runs under sudo, installs curl when necessary and only then downloads the
    controller-served installer. This mirrors the proven SG-Panel workflow.
    """
    base = controller_url.rstrip("/")
    url = shlex.quote(f"{base}/bootstrap/sg-awg-node-install.sh")
    script = (
        "set -Eeuo pipefail; export DEBIAN_FRONTEND=noninteractive; "
        "if ! command -v curl >/dev/null 2>&1; then "
        "apt-get update -qq && apt-get install -y -qq ca-certificates curl; fi; "
        f"curl -fsSL {url} | bash"
    )
    return f"sudo bash -c {shlex.quote(script)}"


def enrollment_command(*, controller_url: str, slug: str, token: str) -> str:
    base = controller_url.rstrip("/")
    script_url = shlex.quote(f"{base}/bootstrap/sg-awg-node-connect.sh")
    return (
        f"curl -fsSL {script_url} | sudo bash -s -- "
        f"--controller {shlex.quote(base)} --node {shlex.quote(slug)} "
        f"--token {shlex.quote(token)}"
    )


def enroll_node(*, slug: str, enrollment_token: str, metadata: dict[str, Any]) -> tuple[dict[str, Any], str]:
    node = get_node_by_slug(slug)
    if bool(node["is_local"]):
        raise ValueError("Локальный Controller не регистрируется как Agent")
    expires = _parse_stamp(node.get("enrollment_expires_at"))
    if expires is None or expires < _utcnow():
        raise PermissionError("Код подключения SG-Node истёк. Создайте новый в панели")
    expected = str(node.get("enrollment_token_hash") or "")
    provided = _token_hash(str(enrollment_token or ""))
    if not expected or not secrets.compare_digest(expected, provided):
        raise PermissionError("Неверный код подключения SG-Node")
    agent_token = secrets.token_urlsafe(40)
    now = _stamp()
    values = _metadata_values(metadata)
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        local = con.execute(
            "SELECT machine_id,public_ipv4,private_ipv4 FROM cluster_nodes WHERE is_local=1 LIMIT 1"
        ).fetchone()
        same_machine = bool(
            local
            and values["machine_id"]
            and str(local["machine_id"] or "")
            and secrets.compare_digest(str(local["machine_id"]), values["machine_id"])
        )
        same_addresses = bool(
            local
            and values["public_ipv4"]
            and values["private_ipv4"]
            and str(local["public_ipv4"] or "") == values["public_ipv4"]
            and str(local["private_ipv4"] or "") == values["private_ipv4"]
        )
        if same_machine or same_addresses:
            raise ValueError("Этот сервер уже является Controller. Подключить его как SG-Node нельзя")
        _allocate_pool(con, int(node["id"]))
        con.execute(
            """
            UPDATE cluster_nodes SET
                state='online', agent_token_hash=?, enrollment_token_hash='',
                enrollment_expires_at=NULL, registered_at=COALESCE(registered_at, ?),
                last_seen_at=?, agent_version=?, os_name=?, os_version=?, kernel=?, machine_id=?,
                public_ipv4=?, private_ipv4=?,
                country_code=CASE WHEN country_mode='auto' AND ?<>'' THEN ? ELSE country_code END,
                country_updated_at=CASE WHEN country_mode='auto' AND ?<>'' THEN CURRENT_TIMESTAMP ELSE country_updated_at END,
                awg_version=?, panel_version=?,
                capabilities_json=?, awg_runtime_json=?,
                public_port=CASE WHEN ? BETWEEN 1 AND 65535 THEN ? ELSE public_port END,
                last_error='', updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                _token_hash(agent_token), now, now,
                values["agent_version"], values["os_name"], values["os_version"],
                values["kernel"], values["machine_id"], values["public_ipv4"], values["private_ipv4"],
                values["country_code"], values["country_code"], values["country_code"],
                values["awg_version"], values["panel_version"],
                values["capabilities_json"], values["awg_runtime_json"],
                values["runtime_port"], values["runtime_port"], int(node["id"]),
            ),
        )
    return get_node(int(node["id"])), agent_token


def authenticate_agent(slug: str, token: str) -> dict[str, Any]:
    node = get_node_by_slug(slug)
    expected = str(node.get("agent_token_hash") or "")
    if not expected or not secrets.compare_digest(expected, _token_hash(str(token or ""))):
        raise PermissionError("Неверный токен SG-Node Agent")
    if str(node.get("state")) == "disabled":
        raise PermissionError("SG-Node отключена в Controller")
    return node


def _clean_text(value: object, limit: int = 256) -> str:
    return str(value or "").strip()[:limit]


def _number(value: object, minimum: float = 0, maximum: float = 100) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(minimum, min(maximum, result))


def _metadata_values(metadata: dict[str, Any]) -> dict[str, Any]:
    capabilities = metadata.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = {}
    runtime = metadata.get("awg_runtime")
    if not isinstance(runtime, dict):
        runtime = {}
    runtime = dict(runtime)
    server_public_key = str(runtime.get("server_public_key") or runtime.get("public_key") or "").strip()
    if server_public_key:
        runtime["server_public_key"] = server_public_key
        runtime["public_key"] = server_public_key
    runtime_json = json.dumps(runtime, ensure_ascii=False, sort_keys=True)
    if len(runtime_json) > 131072:
        runtime = dict(runtime)
        runtime["peers"] = {}
        runtime_json = json.dumps(runtime, ensure_ascii=False, sort_keys=True)
    try:
        runtime_port = int(runtime.get("listen_port") or 0)
    except (TypeError, ValueError):
        runtime_port = 0
    runtime_error = ""
    if runtime_port and runtime_port != 585:
        runtime_error = f"Ожидается UDP 585, реально {runtime_port}"
        runtime["runtime_error"] = runtime_error
        runtime_json = json.dumps(runtime, ensure_ascii=False, sort_keys=True)
    return {
        "agent_version": _clean_text(metadata.get("agent_version"), 64),
        "os_name": _clean_text(metadata.get("os_name"), 96),
        "os_version": _clean_text(metadata.get("os_version"), 96),
        "kernel": _clean_text(metadata.get("kernel"), 160),
        "machine_id": _clean_text(metadata.get("machine_id"), 128),
        "public_ipv4": _clean_text(metadata.get("public_ipv4"), 64),
        "private_ipv4": _clean_text(metadata.get("private_ipv4"), 64),
        "country_code": normalize_country_code(metadata.get("country_code")),
        "awg_version": _clean_text(metadata.get("awg_version"), 96),
        "panel_version": _clean_text(metadata.get("panel_version"), 64),
        "capabilities_json": json.dumps(capabilities, ensure_ascii=False, sort_keys=True)[:8192],
        "awg_runtime_json": runtime_json[:131072],
        "runtime_port": runtime_port,
        "last_error": _clean_text(metadata.get("last_error"), 1024) or runtime_error,
    }


def heartbeat(node_id: int, metadata: dict[str, Any]) -> dict[str, Any]:
    node = get_node(node_id)
    values = _metadata_values(metadata)
    services = metadata.get("services") if isinstance(metadata.get("services"), dict) else {}
    with connect() as con:
        con.execute(
            """
            UPDATE cluster_nodes SET
                state='online', last_seen_at=?, agent_version=?, os_name=?, os_version=?,
                kernel=?, machine_id=CASE WHEN ?<>'' THEN ? ELSE machine_id END,
                public_ipv4=CASE WHEN ?<>'' THEN ? ELSE public_ipv4 END,
                private_ipv4=CASE WHEN ?<>'' THEN ? ELSE private_ipv4 END,
                country_code=CASE WHEN country_mode='auto' AND ?<>'' THEN ? ELSE country_code END,
                country_updated_at=CASE WHEN country_mode='auto' AND ?<>'' THEN CURRENT_TIMESTAMP ELSE country_updated_at END,
                awg_version=?, panel_version=?,
                cpu_percent=?, memory_percent=?, disk_percent=?, load1=?,
                service_awg=?, service_traffic=?, service_nginx=?, capabilities_json=?,
                awg_runtime_json=?,
                public_port=CASE WHEN ? BETWEEN 1 AND 65535 THEN ? ELSE public_port END,
                last_error=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                _stamp(), values["agent_version"], values["os_name"], values["os_version"],
                values["kernel"], values["machine_id"], values["machine_id"],
                values["public_ipv4"], values["public_ipv4"],
                values["private_ipv4"], values["private_ipv4"],
                values["country_code"], values["country_code"], values["country_code"],
                values["awg_version"], values["panel_version"],
                _number(metadata.get("cpu_percent")), _number(metadata.get("memory_percent")),
                _number(metadata.get("disk_percent")), _number(metadata.get("load1"), 0, 10000),
                _clean_text(services.get("awg"), 32) or "unknown",
                _clean_text(services.get("traffic"), 32) or "unknown",
                _clean_text(services.get("nginx"), 32) or "unknown",
                values["capabilities_json"], values["awg_runtime_json"],
                585 if values["runtime_port"] == 585 else 0,
                585 if values["runtime_port"] == 585 else 0,
                values["last_error"], int(node_id),
            ),
        )
    return get_node(node_id)


def set_node_name(node_id: int, name: object) -> dict[str, Any]:
    node = get_node(node_id)
    clean = str(name or "").strip()
    if not clean:
        raise ValueError("Укажите имя сервера")
    if len(clean) > 96:
        raise ValueError("Имя сервера должно содержать не более 96 символов")
    if any(ord(char) < 32 for char in clean):
        raise ValueError("Имя сервера содержит недопустимые символы")
    if bool(node.get("is_local")):
        raise ValueError("Имя Controller изменяется через кнопку «Переименовать сервер» в шапке")
    with connect() as con:
        con.execute(
            "UPDATE cluster_nodes SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (clean, int(node_id)),
        )
    return get_node(node_id)


def set_node_country(node_id: int, country_code: str = "", *, automatic: bool = False) -> dict[str, Any]:
    node = get_node(node_id)
    code = normalize_country_code(country_code)
    if not automatic and str(country_code or "").strip() and not code:
        raise ValueError("Укажите двухбуквенный код страны, например FR, DE или US")
    mode = "auto" if automatic else "manual"
    with connect() as con:
        con.execute(
            "UPDATE cluster_nodes SET country_code=?, country_mode=?, "
            "country_updated_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (code, mode, int(node["id"])),
        )
    return get_node(int(node["id"]))


def set_node_enabled(node_id: int, enabled: bool) -> dict[str, Any]:
    node = get_node(node_id)
    if bool(node["is_local"]):
        raise ValueError("Локальный Controller нельзя отключить")
    with connect() as con:
        con.execute(
            "UPDATE cluster_nodes SET state=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            ("offline" if enabled else "disabled", int(node_id)),
        )
    return get_node(node_id)


def queue_job(node_id: int, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    node = get_node(node_id)
    if bool(node["is_local"]):
        raise ValueError("Для Controller используйте локальные действия панели")
    clean_kind = str(kind or "").strip()
    if clean_kind not in ALLOWED_JOB_KINDS:
        raise ValueError("Неподдерживаемое действие SG-Node")
    if str(node.get("effective_state")) == "disabled":
        raise ValueError("SG-Node отключена")
    encoded = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    if len(encoded) > 262144:
        raise ValueError("Параметры задания слишком большие")
    with connect() as con:
        cursor = con.execute(
            "INSERT INTO node_jobs (node_id, kind, payload_json) VALUES (?, ?, ?)",
            (int(node_id), clean_kind, encoded),
        )
        job_id = int(cursor.lastrowid)
    return get_job(job_id)


def get_job(job_id: int) -> dict[str, Any]:
    init_db()
    with connect() as con:
        row = con.execute("SELECT * FROM node_jobs WHERE id=?", (int(job_id),)).fetchone()
    if row is None:
        raise AWGPanelError("Задание SG-Node не найдено")
    result = dict(row)
    for key in ("payload_json", "result_json"):
        try:
            result[key.removesuffix("_json")] = json.loads(str(result.get(key) or "{}"))
        except (TypeError, ValueError):
            result[key.removesuffix("_json")] = {}
    return result


def list_jobs(node_id: int, limit: int = 20) -> list[dict[str, Any]]:
    get_node(node_id)
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM node_jobs WHERE node_id=? ORDER BY id DESC LIMIT ?",
            (int(node_id), max(1, min(int(limit), 100))),
        ).fetchall()
    return [get_job(int(row["id"])) for row in rows]


def claim_next_job(node_id: int) -> dict[str, Any] | None:
    get_node(node_id)
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        stale_before = _stamp(_utcnow() - timedelta(minutes=5))
        con.execute(
            """
            UPDATE node_jobs SET state='queued', claimed_at=NULL
            WHERE node_id=? AND state='claimed' AND claimed_at IS NOT NULL AND claimed_at<?
            """,
            (int(node_id), stale_before),
        )
        row = con.execute(
            "SELECT id FROM node_jobs WHERE node_id=? AND state='queued' ORDER BY id LIMIT 1",
            (int(node_id),),
        ).fetchone()
        if row is None:
            return None
        job_id = int(row["id"])
        con.execute(
            "UPDATE node_jobs SET state='claimed', claimed_at=? WHERE id=? AND state='queued'",
            (_stamp(), job_id),
        )
    return get_job(job_id)


def finish_job(node_id: int, job_id: int, *, ok: bool, result: dict[str, Any] | None = None) -> dict[str, Any]:
    job = get_job(job_id)
    if int(job["node_id"]) != int(node_id):
        raise PermissionError("Задание принадлежит другой SG-Node")

    result_data = dict(result or {})
    if str(job.get("kind") or "") == "refresh" and ok:
        metadata = result_data.get("metadata")
        if not isinstance(metadata, dict):
            ok = False
            result_data = {"message": "SG-Node не вернула данные подключения"}
        else:
            refreshed = heartbeat(int(node_id), metadata)
            runtime = refreshed.get("awg_runtime") if isinstance(refreshed.get("awg_runtime"), dict) else {}
            result_data = {
                **result_data,
                "message": "Подключение SG-Node обновлено",
                "last_seen_at": str(refreshed.get("last_seen_at") or ""),
                "server_public_key": str(runtime.get("server_public_key") or runtime.get("public_key") or ""),
            }
    message = _clean_text(result_data.get("message"), 1024)
    encoded = json.dumps(result_data, ensure_ascii=False, sort_keys=True)
    if len(encoded) > 262144:
        ok = False
        message = "Результат задания был слишком большим"
        result_data = {"message": message}
        encoded = json.dumps(result_data, ensure_ascii=False)

    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    apply_mode = str(payload.get("mode") or "") if str(job.get("kind")) == "apply_awg_config" else ""
    sync_clients = apply_mode == "sync_clients"
    cascade_job = apply_mode in {"configure_cascade", "disable_cascade", "test_cascade"}
    if str(job.get("kind")) == "apply_awg_config" and not (sync_clients or cascade_job):
        ok = False
        message = "Неподдерживаемое задание AmneziaWG отклонено"
        result_data = {"message": message}
        encoded = json.dumps(result_data, ensure_ascii=False)

    target_ids: list[int] = []
    delete_ids: list[int] = []
    expected_active: set[int] = set()
    verified_ok = bool(ok)
    if sync_clients:
        target_ids = [
            int(value) for value in payload.get("target_client_ids", [])
            if str(value).isdigit()
        ]
        delete_ids = [
            int(value) for value in payload.get("delete_client_ids", [])
            if str(value).isdigit()
        ]
        expected_active = {
            int(item.get("id")) for item in payload.get("peers", [])
            if isinstance(item, dict) and str(item.get("id", "")).isdigit()
        }
        verified = {
            int(value) for value in result_data.get("verified_client_ids", [])
            if str(value).isdigit()
        }
        expected_runtime = payload.get("expected") if isinstance(payload.get("expected"), dict) else {}
        runtime = result_data.get("runtime") if isinstance(result_data.get("runtime"), dict) else {}
        try:
            result_port = int(result_data.get("listen_port") or runtime.get("listen_port") or 0)
        except (TypeError, ValueError):
            result_port = 0
        result_key = str(
            result_data.get("server_public_key") or runtime.get("public_key") or ""
        ).strip()
        result_network = str(
            result_data.get("server_network") or runtime.get("server_network") or ""
        ).strip()
        verified_ok = bool(
            ok
            and verified == expected_active
            and result_port == 585
            and bool(result_key)
            and result_network == str(expected_runtime.get("server_network") or "").strip()
        )
        if not verified_ok:
            ok = False
            message = message or "Agent не подтвердил реальный awg0, UDP-порт 585 и полный список peers"
            result_data = {**result_data, "message": message}
            encoded = json.dumps(result_data, ensure_ascii=False, sort_keys=True)

    with connect() as con:
        con.execute(
            """
            UPDATE node_jobs SET state=?, result_json=?, finished_at=?,
                payload_json=CASE WHEN kind='apply_awg_config' THEN '{}' ELSE payload_json END
            WHERE id=? AND node_id=?
            """,
            ("success" if ok else "error", encoded, _stamp(), int(job_id), int(node_id)),
        )
        if sync_clients:
            status_ids = target_ids if not verified_ok else [value for value in target_ids if value not in set(delete_ids)]
            if status_ids:
                target_placeholders = ",".join("?" for _ in status_ids)
                active_placeholders = ",".join("?" for _ in expected_active) or "NULL"
                con.execute(
                    f"UPDATE awg_clients SET deployment_state=?, deployment_error=?, "
                    f"deployed_enabled=CASE WHEN id IN ({active_placeholders}) THEN 1 ELSE 0 END, "
                    f"updated_at=CURRENT_TIMESTAMP WHERE id IN ({target_placeholders}) AND node_id=?",
                    [
                        "active" if verified_ok else "error",
                        "" if verified_ok else message,
                        *sorted(expected_active),
                        *status_ids,
                        int(node_id),
                    ],
                )
            if verified_ok and delete_ids:
                placeholders = ",".join("?" for _ in delete_ids)
                con.execute(
                    f"DELETE FROM awg_clients WHERE id IN ({placeholders}) AND node_id=? "
                    "AND deployment_state='deleting'",
                    [*delete_ids, int(node_id)],
                )
            runtime = result_data.get("runtime")
            if verified_ok and isinstance(runtime, dict):
                runtime = dict(runtime)
                runtime["server_public_key"] = result_key
                runtime["public_key"] = result_key
                con.execute(
                    "UPDATE cluster_nodes SET awg_runtime_json=?, public_port=585, "
                    "last_error='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (
                        json.dumps(runtime, ensure_ascii=False, sort_keys=True)[:131072],
                        int(node_id),
                    ),
                )
        if not ok:
            con.execute(
                "UPDATE cluster_nodes SET last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (message, int(node_id)),
            )
    if sync_clients or cascade_job:
        try:
            from .cluster_cascade import handle_job_completion
            handle_job_completion(int(node_id), job, bool(ok), result_data)
        except Exception:
            # Cascade reconciliation must never invalidate the authenticated Agent response.
            pass
    return get_job(job_id)
