from __future__ import annotations

import ipaddress
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import __version__, db
from .core import (
    AWG_CONFIG_PATH,
    AWG_SERVICE,
    configure_access_links,
    configure_and_start_awg,
    configure_backup_policy,
    configure_panel_access,
    detect_external_interface,
    detect_public_ipv4,
    find_awg_client,
    get_awg_settings,
    get_panel_settings,
    list_awg_clients,
    normalize_ip_allowlist,
    update_awg_client_document,
    update_ip_allowlist,
    update_traffic_document,
    validate_awg_client_document,
    validate_awg_settings_document,
    validate_traffic_document,
)
from .db import connect, init_db
from .egress import (
    OUTBOUND_CONFIG_DIR,
    TRAFFIC_STATE_DIR,
    apply_egress_runtime,
    validate_egress_runtime,
    find_outbound,
    list_outbounds,
    replace_outbound,
)
from .errors import AWGPanelError
from .traffic_rules import (
    DNSMASQ_CONFIG_PATH,
    parse_rules_json_document,
    replace_rules_document,
    rules_json_document,
)
from .json_editors import (
    META_KEY,
    client_json_document,
    outbound_json_document,
    parse_client_json_document,
    parse_outbound_json_document,
    parse_network_json_document,
    parse_server_json_document,
    network_json_document,
    server_json_document,
)

PANEL_FORMAT = "panel-v2"
NGINX_PANEL_PATH = Path("/etc/nginx/sites-available/sg-awg-panel.conf")
NGINX_PLACEHOLDER_PATH = Path("/etc/nginx/sites-available/sg-awg-placeholder.conf")


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _object(text: str, label: str = "Config") -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON: строка {exc.lineno}, столбец {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON {label} должен быть объектом")
    return value


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} должен быть JSON-объектом")
    return value


def _reject_unknown_keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"{field}: неизвестные поля: {', '.join(unknown)}")


def _array(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} должен быть массивом")
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} должен быть true или false")
    return value


def _string(value: object, field: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} должен быть строкой")
    result = value.strip()
    if not result and not allow_empty:
        raise ValueError(f"{field} не может быть пустым")
    return result


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} должен быть целым числом")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} должен быть от {minimum} до {maximum}")
    return value


def _normalize_dns_list(value: object) -> tuple[list[str], str]:
    items = _array(value, "dns.servers")
    if not 1 <= len(items) <= 4:
        raise ValueError("dns.servers должен содержать от одного до четырёх адресов")
    normalized: list[str] = []
    for index, item in enumerate(items):
        raw = _string(item, f"dns.servers[{index}]", allow_empty=False)
        try:
            normalized.append(str(ipaddress.ip_address(raw)))
        except ValueError as exc:
            raise ValueError(f"Некорректный DNS IP-адрес: {raw}") from exc
    return normalized, ", ".join(normalized)


def _normalize_domain(value: object, *, required: bool) -> str:
    raw = _string(value, "panelAccess.host").rstrip(".")
    if not raw:
        if required:
            raise ValueError("Для HTTPS требуется panelAccess.host")
        return ""
    try:
        ascii_name = raw.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Некорректный домен панели") from exc
    labels = ascii_name.split(".")
    if len(labels) < 2 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(not (ch.isalnum() or ch == "-") for ch in label)
        for label in labels
    ):
        raise ValueError("Укажите полное доменное имя, например awg.example.com")
    return ascii_name


def panel_config_document() -> str:
    init_db()
    settings = get_awg_settings()
    clients = list_awg_clients()
    panel = get_panel_settings()
    outbounds = list_outbounds()

    server_doc = json.loads(server_json_document(settings))
    if not server_doc["server"].get("endpointHost"):
        server_doc["server"]["endpointHost"] = detect_public_ipv4()
    if not server_doc["server"].get("externalInterface"):
        server_doc["server"]["externalInterface"] = detect_external_interface()
    client_docs: list[dict[str, Any]] = []
    for client in clients:
        item = json.loads(client_json_document(client, settings))
        item.pop("traffic", None)
        client_docs.append(item)

    outbound_docs = [json.loads(outbound_json_document(item)) for item in outbounds]
    traffic_doc = json.loads(network_json_document(settings, clients))
    if not traffic_doc["settings"].get("externalInterface"):
        traffic_doc["settings"]["externalInterface"] = (
            server_doc["server"].get("externalInterface") or detect_external_interface()
        )
    dns_servers = [
        part.strip() for part in str(settings["dns_servers"] or "").split(",") if part.strip()
    ]

    document = {
        META_KEY: {
            "format": PANEL_FORMAT,
            "version": __version__,
            "secrets": "$KEEP",
            "note": "Закрытые ключи не выводятся. $KEEP сохраняет действующие значения.",
        },
        "server": server_doc,
        "clients": client_docs,
        "access": {
            "enabled": bool(panel["access_enabled"]),
            "profileTitle": str(panel["access_profile_title"]),
        },
        "backups": {
            "schedule": str(panel["backup_schedule"]),
            "keep": int(panel["backup_keep"]),
        },
        "security": {
            "ipAllowlist": [
                part.strip() for part in str(panel["ip_allowlist"] or "").split(",") if part.strip()
            ],
        },
        "outbounds": outbound_docs,
        "traffic": traffic_doc,
        "trafficRules": json.loads(rules_json_document()),
        "dns": {"servers": dns_servers},
        "panelAccess": {
            "scheme": str(panel["public_scheme"]),
            "host": str(panel["public_host"] or ""),
            "port": int(panel["public_port"]),
            "managePlaceholder": bool(panel["manage_placeholder"]),
            "backend": "127.0.0.1:18080",
        },
    }
    return _dump(document)


def parse_panel_config_document(text: str) -> dict[str, object]:
    document = _object(text)
    _reject_unknown_keys(document, {META_KEY, "server", "clients", "access", "backups", "security", "outbounds", "traffic", "trafficRules", "dns", "panelAccess"}, "$")
    meta = _mapping(document.get(META_KEY, {}), META_KEY)
    _reject_unknown_keys(meta, {"format", "version", "secrets", "note"}, META_KEY)
    if meta.get("format") not in (None, "panel-v1", PANEL_FORMAT):
        raise ValueError(f"{META_KEY}.format должен быть panel-v1 или {PANEL_FORMAT}")
    if meta.get("secrets", "$KEEP") != "$KEEP":
        raise ValueError(f"{META_KEY}.secrets должен оставаться $KEEP")

    current_clients = {int(row["id"]): row for row in list_awg_clients()}
    current_outbounds = {int(row["id"]): row for row in list_outbounds()}

    server_obj = _mapping(document.get("server"), "server")
    server_values, confirm_network = parse_server_json_document(_dump(server_obj))
    validated_server = validate_awg_settings_document(server_values)
    current_server_network = str(get_awg_settings()["server_network"])
    if str(validated_server["server_network"]) != current_server_network:
        raise ValueError(
            "Сеть AWG Server меняйте через отдельную форму или JSON AWG Server; "
            "полный Config не переносит адреса клиентов между сетями"
        )

    dns_obj = _mapping(document.get("dns"), "dns")
    _reject_unknown_keys(dns_obj, {"servers"}, "dns")
    dns_list, dns_text = _normalize_dns_list(dns_obj.get("servers"))
    if dns_text != str(validated_server["dns_servers"]):
        raise ValueError("dns.servers должен совпадать с server.server.dnsServers")

    traffic_obj = _mapping(document.get("traffic"), "traffic")
    traffic_settings, traffic_clients = parse_network_json_document(_dump(traffic_obj))
    normalized_interface, normalized_server_lans, validated_traffic_clients = validate_traffic_document(
        traffic_settings, traffic_clients
    )
    validated_traffic_settings = {
        "external_interface": normalized_interface,
        "server_lan_networks": normalized_server_lans,
        "nat_enabled": bool(traffic_settings["nat_enabled"]),
        "isolate_clients": bool(traffic_settings["isolate_clients"]),
    }
    traffic_by_id = {int(item["id"]): item for item in validated_traffic_clients}

    rules_obj = document.get("trafficRules")
    if rules_obj is None:
        # Backward-compatible Alpha 15/early Alpha 16 document: keep current rules.
        rules_obj = json.loads(rules_json_document())
    policy_rules = parse_rules_json_document(_dump(_mapping(rules_obj, "trafficRules")))

    client_items = _array(document.get("clients"), "clients")
    if len(client_items) != len(current_clients):
        raise ValueError("clients должен содержать всех текущих клиентов ровно по одному разу")
    parsed_clients: list[dict[str, object]] = []
    seen_clients: set[int] = set()
    for index, item in enumerate(client_items):
        client_obj = _mapping(item, f"clients[{index}]")
        client_data = _mapping(client_obj.get("client"), f"clients[{index}].client")
        client_id = _integer(client_data.get("id"), f"clients[{index}].client.id", 1, 2_147_483_647)
        if client_id in seen_clients or client_id not in current_clients:
            raise ValueError(f"clients[{index}].client.id неизвестен или повторяется")
        if client_id not in traffic_by_id:
            raise ValueError(f"Для клиента #{client_id} нет записи в traffic.clients")
        combined = dict(client_obj)
        route = traffic_by_id[client_id]
        combined["traffic"] = {
            "allowedIPs": [part.strip() for part in str(route["allowed_ips"]).split(",") if part.strip()],
            "excludedIPs": [part.strip() for part in str(route["excluded_ips"]).split(",") if part.strip()],
            "advertisedNetworks": [part.strip() for part in str(route["advertised_networks"]).split(",") if part.strip()],
            "includeServerLAN": bool(route["include_server_lan"]),
            "egressMode": str(route["egress_mode"]),
            "outboundId": route["outbound_id"],
        }
        values = parse_client_json_document(_dump(combined), expected_id=client_id)
        validated_client = validate_awg_client_document(client_id, values)
        validated_client["id"] = client_id
        parsed_clients.append(validated_client)
        seen_clients.add(client_id)
    if seen_clients != set(current_clients):
        raise ValueError("clients содержит неполный список клиентов")

    outbound_items = _array(document.get("outbounds"), "outbounds")
    if len(outbound_items) != len(current_outbounds):
        raise ValueError("outbounds должен содержать все текущие Outbound-профили")
    parsed_outbounds: list[dict[str, object]] = []
    seen_outbounds: set[int] = set()
    for index, item in enumerate(outbound_items):
        outbound_obj = _mapping(item, f"outbounds[{index}]")
        outbound_meta = _mapping(outbound_obj.get(META_KEY, {}), f"outbounds[{index}].{META_KEY}")
        outbound_id = _integer(outbound_meta.get("id"), f"outbounds[{index}].{META_KEY}.id", 1, 32)
        if outbound_id in seen_outbounds or outbound_id not in current_outbounds:
            raise ValueError(f"outbounds[{index}] содержит неизвестный или повторяющийся id")
        name, config_text, enabled = parse_outbound_json_document(
            _dump(outbound_obj), current=current_outbounds[outbound_id]
        )
        if bool(enabled) != bool(current_outbounds[outbound_id]["enabled"]):
            raise ValueError(
                f"Состояние Outbound #{outbound_id} меняйте на странице Outbounds; "
                "полный Config не включает и не отключает туннели"
            )
        parsed_outbounds.append(
            {"id": outbound_id, "name": name, "config_text": config_text, "enabled": enabled}
        )
        seen_outbounds.add(outbound_id)
    if seen_outbounds != set(current_outbounds):
        raise ValueError("outbounds содержит неполный список профилей")

    access = _mapping(document.get("access"), "access")
    _reject_unknown_keys(access, {"enabled", "profileTitle"}, "access")
    access_enabled = _boolean(access.get("enabled"), "access.enabled")
    profile_title = _string(access.get("profileTitle", ""), "access.profileTitle", allow_empty=False)
    if len(profile_title) > 80:
        raise ValueError("access.profileTitle должен содержать не более 80 символов")

    backups = _mapping(document.get("backups"), "backups")
    _reject_unknown_keys(backups, {"schedule", "keep"}, "backups")
    backup_schedule = _string(backups.get("schedule", ""), "backups.schedule", allow_empty=False)
    if backup_schedule not in {"hourly", "every_6_hours", "daily", "weekly", "disabled"}:
        raise ValueError("backups.schedule содержит неподдерживаемое значение")
    backup_keep = _integer(backups.get("keep"), "backups.keep", 1, 365)

    security = _mapping(document.get("security"), "security")
    _reject_unknown_keys(security, {"ipAllowlist"}, "security")
    allowlist_items = _array(security.get("ipAllowlist"), "security.ipAllowlist")
    if any(not isinstance(item, str) for item in allowlist_items):
        raise ValueError("security.ipAllowlist должен содержать только строки IP/CIDR")
    ip_allowlist = normalize_ip_allowlist(", ".join(item.strip() for item in allowlist_items))

    panel_access = _mapping(document.get("panelAccess"), "panelAccess")
    _reject_unknown_keys(panel_access, {"scheme", "host", "port", "managePlaceholder", "backend"}, "panelAccess")
    scheme = _string(panel_access.get("scheme", "http"), "panelAccess.scheme", allow_empty=False).lower()
    if scheme not in {"http", "https"}:
        raise ValueError("panelAccess.scheme должен быть http или https")
    port = _integer(panel_access.get("port"), "panelAccess.port", 1, 65535)
    current_panel_port = int(get_panel_settings()["public_port"])
    if not 49152 <= port <= 65535 and port != current_panel_port:
        raise ValueError("panelAccess.port должен быть в динамическом диапазоне 49152–65535")
    host = _normalize_domain(panel_access.get("host", ""), required=scheme == "https")
    placeholder = _boolean(panel_access.get("managePlaceholder"), "panelAccess.managePlaceholder")
    if panel_access.get("backend", "127.0.0.1:18080") != "127.0.0.1:18080":
        raise ValueError("panelAccess.backend фиксирован: 127.0.0.1:18080")

    return {
        "server_values": validated_server,
        "confirm_network": confirm_network,
        "clients": parsed_clients,
        "outbounds": parsed_outbounds,
        "traffic_settings": validated_traffic_settings,
        "traffic_clients": validated_traffic_clients,
        "traffic_policy_rules": policy_rules,
        "access_enabled": access_enabled,
        "access_profile_title": profile_title,
        "backup_schedule": backup_schedule,
        "backup_keep": backup_keep,
        "ip_allowlist": ip_allowlist,
        "dns_servers": dns_list,
        "panel_access": {
            "scheme": scheme,
            "public_host": host,
            "public_port": port,
            "manage_placeholder": placeholder,
        },
    }



def validate_panel_config_document(text: str) -> dict[str, object]:
    parsed = parse_panel_config_document(text)
    settings = dict(parsed["server_values"])
    settings.update(parsed["traffic_settings"])
    validate_egress_runtime(
        candidate_rules=list(parsed["traffic_policy_rules"]),
        candidate_profiles=list(parsed["outbounds"]),
        candidate_clients=list(parsed["clients"]),
        candidate_settings=settings,
    )
    nginx = shutil.which("nginx")
    if nginx:
        result = subprocess.run([nginx, "-t"], capture_output=True, text=True, timeout=20, check=False)
        if result.returncode != 0:
            raise AWGPanelError(result.stderr.strip() or result.stdout.strip() or "Проверка Nginx завершилась ошибкой")
    return parsed


def section_json_configs() -> dict[str, dict[str, str]]:
    settings = get_awg_settings()
    clients = list_awg_clients()
    panel = get_panel_settings()
    outbounds = list_outbounds()
    clients_doc = {
        META_KEY: {"format": "clients-overview-v1", "note": "Read-only обзор JSON всех клиентов."},
        "clients": [json.loads(client_json_document(item, settings)) for item in clients],
    }
    network_doc = {
        META_KEY: {"format": "network-overview-v1", "note": "Read-only обзор Network."},
        "traffic": json.loads(network_json_document(settings, clients)),
        "trafficRules": json.loads(rules_json_document()),
        "outbounds": [json.loads(outbound_json_document(item)) for item in outbounds],
    }
    security_doc = {
        META_KEY: {"format": "security-overview-v1", "note": "Read-only обзор Security."},
        "panelAccess": {
            "scheme": str(panel["public_scheme"]),
            "host": str(panel["public_host"] or ""),
            "port": int(panel["public_port"]),
            "managePlaceholder": bool(panel["manage_placeholder"]),
            "backend": "127.0.0.1:18080",
        },
        "ipAllowlist": [part.strip() for part in str(panel["ip_allowlist"] or "").split(",") if part.strip()],
        "protectedLinks": {
            "enabled": bool(panel["access_enabled"]),
            "profileTitle": str(panel["access_profile_title"]),
        },
    }
    maintenance_doc = {
        META_KEY: {"format": "maintenance-overview-v1", "note": "Read-only обзор Maintenance."},
        "backups": {"schedule": str(panel["backup_schedule"]), "keep": int(panel["backup_keep"])},
        "updates": {"currentVersion": __version__, "automaticRollback": True},
    }
    return {
        "server": {"title": "AWG Server JSON", "filename": "awg-server.json", "content": server_json_document(settings), "edit_url": "/server/json"},
        "clients": {"title": "Clients JSON", "filename": "clients.json", "content": _dump(clients_doc), "edit_url": "/clients"},
        "network": {"title": "Network JSON", "filename": "network.json", "content": _dump(network_doc), "edit_url": "/network"},
        "security": {"title": "Security JSON", "filename": "security.json", "content": _dump(security_doc), "edit_url": "/security"},
        "maintenance": {"title": "Maintenance JSON", "filename": "maintenance.json", "content": _dump(maintenance_doc), "edit_url": "/maintenance"},
    }

def _snapshot() -> tuple[Path, str]:
    root = Path(tempfile.mkdtemp(prefix="sg-awg-config-"))
    init_db()
    destination = sqlite3.connect(root / "panel.db")
    try:
        with connect() as source:
            source.backup(destination)
    finally:
        destination.close()
    os.chmod(root / "panel.db", 0o600)
    if AWG_CONFIG_PATH.exists():
        shutil.copy2(AWG_CONFIG_PATH, root / "awg0.conf")
    if OUTBOUND_CONFIG_DIR.exists():
        shutil.copytree(OUTBOUND_CONFIG_DIR, root / "outbounds")
    if TRAFFIC_STATE_DIR.exists():
        shutil.copytree(TRAFFIC_STATE_DIR, root / "traffic")
    if DNSMASQ_CONFIG_PATH.exists():
        shutil.copy2(DNSMASQ_CONFIG_PATH, root / "dnsmasq.conf")
    result = subprocess.run(
        ["systemctl", "is-active", AWG_SERVICE], capture_output=True, text=True, check=False
    )
    return root, result.stdout.strip()


def _restore_snapshot(root: Path, previous_awg_state: str) -> None:
    # Stop a newly started interface while the current config still exists.
    # Removing awg0.conf first makes ExecStop fail and can leave awg0 behind.
    if previous_awg_state != "active":
        stopped = subprocess.run(
            ["systemctl", "stop", AWG_SERVICE],
            capture_output=True, text=True, check=False,
        )
        if stopped.returncode != 0 and AWG_CONFIG_PATH.exists():
            awg_quick = shutil.which("awg-quick")
            if awg_quick:
                subprocess.run(
                    [awg_quick, "down", str(AWG_CONFIG_PATH)],
                    capture_output=True, text=True, check=False,
                )

    for suffix in ("", "-wal", "-shm"):
        Path(str(db.DB_PATH) + suffix).unlink(missing_ok=True)
    shutil.copy2(root / "panel.db", db.DB_PATH)
    os.chmod(db.DB_PATH, 0o600)

    AWG_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if (root / "awg0.conf").exists():
        shutil.copy2(root / "awg0.conf", AWG_CONFIG_PATH)
        os.chmod(AWG_CONFIG_PATH, 0o600)
    else:
        AWG_CONFIG_PATH.unlink(missing_ok=True)

    shutil.rmtree(OUTBOUND_CONFIG_DIR, ignore_errors=True)
    if (root / "outbounds").exists():
        shutil.copytree(root / "outbounds", OUTBOUND_CONFIG_DIR)
    shutil.rmtree(TRAFFIC_STATE_DIR, ignore_errors=True)
    if (root / "traffic").exists():
        shutil.copytree(root / "traffic", TRAFFIC_STATE_DIR)
    DNSMASQ_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if (root / "dnsmasq.conf").exists():
        shutil.copy2(root / "dnsmasq.conf", DNSMASQ_CONFIG_PATH)
        os.chmod(DNSMASQ_CONFIG_PATH, 0o644)
    else:
        DNSMASQ_CONFIG_PATH.unlink(missing_ok=True)

    if previous_awg_state == "active" and AWG_CONFIG_PATH.exists():
        subprocess.run(["systemctl", "restart", AWG_SERVICE], check=False, capture_output=True, text=True)
    try:
        apply_egress_runtime()
    except Exception:
        pass


def apply_panel_config_document(text: str, *, current_ip: str | None = None) -> dict[str, object]:
    if os.geteuid() != 0:
        raise PermissionError("Для применения полного Config нужны права root")
    parsed = parse_panel_config_document(text)
    snapshot, previous_awg_state = _snapshot()
    try:
        current_settings = get_awg_settings()
        if (
            str(current_settings["server_network"]) != str(parsed["server_values"]["server_network"])
            and not parsed["confirm_network"]
        ):
            raise ValueError(
                "Для изменения сети установите server._sgAwgPanel.confirmNetworkChange=true"
            )
        configure_and_start_awg(**parsed["server_values"])
        for client in parsed["clients"]:
            update_awg_client_document(int(client["id"]), client)
        for outbound in parsed["outbounds"]:
            replace_outbound(
                int(outbound["id"]),
                name=str(outbound["name"]),
                config_text=str(outbound["config_text"]),
                enabled=bool(outbound["enabled"]),
            )
        update_traffic_document(
            parsed["traffic_settings"], parsed["traffic_clients"]
        )
        replace_rules_document(parsed["traffic_policy_rules"])
        apply_egress_runtime()
        configure_access_links(
            enabled=bool(parsed["access_enabled"]),
            profile_title=str(parsed["access_profile_title"]),
        )
        current_panel = get_panel_settings()
        if (
            str(current_panel["backup_schedule"]) != str(parsed["backup_schedule"])
            or int(current_panel["backup_keep"]) != int(parsed["backup_keep"])
        ):
            configure_backup_policy(str(parsed["backup_schedule"]), int(parsed["backup_keep"]))
        if str(current_panel["ip_allowlist"] or "") != str(parsed["ip_allowlist"] or ""):
            if not current_ip:
                raise ValueError("Для изменения IP allowlist через полный Config нужен текущий IP")
            update_ip_allowlist(parsed["ip_allowlist"], current_ip=current_ip)
        current_panel = get_panel_settings()
        target = parsed["panel_access"]
        access_changed = (
            str(current_panel["public_scheme"]) != str(target["scheme"])
            or str(current_panel["public_host"] or "") != str(target["public_host"])
            or int(current_panel["public_port"]) != int(target["public_port"])
            or bool(current_panel["manage_placeholder"]) != bool(target["manage_placeholder"])
        )
        if access_changed:
            configure_panel_access(**target)
        return parsed
    except Exception:
        _restore_snapshot(snapshot, previous_awg_state)
        raise
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


def _redact_ini(text: str) -> str:
    output: list[str] = []
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip().lower() if "=" in line else ""
        if key in {"privatekey", "presharedkey"}:
            prefix = line.split("=", 1)[0].rstrip()
            output.append(f"{prefix} = $REDACTED")
        else:
            output.append(line)
    return "\n".join(output).rstrip() + ("\n" if text else "")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        return f"Не удалось прочитать {path}: {exc}\n"


def _command_text(args: list[str]) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Команда недоступна: {exc}\n"
    value = result.stdout if result.returncode == 0 else (result.stderr or result.stdout)
    return value.strip() + ("\n" if value.strip() else "")


def generated_configs() -> dict[str, dict[str, str]]:
    awg_text = _redact_ini(_read_text(AWG_CONFIG_PATH))
    nginx_parts: list[str] = []
    for path in (NGINX_PANEL_PATH, NGINX_PLACEHOLDER_PATH):
        text = _read_text(path)
        if text:
            nginx_parts.append(f"# {path}\n{text.rstrip()}\n")
    nginx_text = "\n".join(nginx_parts)

    traffic_parts: list[str] = []
    nft_file = TRAFFIC_STATE_DIR / "traffic.nft"
    nft_text = _read_text(nft_file)
    if nft_text:
        traffic_parts.append(f"# {nft_file}\n{nft_text.rstrip()}\n")
    dnsmasq_text = _read_text(DNSMASQ_CONFIG_PATH)
    if dnsmasq_text:
        traffic_parts.append(f"# {DNSMASQ_CONFIG_PATH}\n{dnsmasq_text.rstrip()}\n")
    traffic_parts.append("# ip -4 rule show\n" + _command_text(["ip", "-4", "rule", "show"]).rstrip())
    traffic_parts.append(
        "# nft list table inet sg_awg_traffic\n"
        + _command_text(["nft", "list", "table", "inet", "sg_awg_traffic"]).rstrip()
    )
    traffic_text = "\n\n".join(part for part in traffic_parts if part.strip()).rstrip() + "\n"

    return {
        "awg": {"title": "AWG Server", "filename": "awg0.redacted.conf", "content": awg_text},
        "nginx": {"title": "Nginx", "filename": "nginx-panel.conf", "content": nginx_text},
        "traffic": {"title": "Traffic Rules", "filename": "traffic-current.txt", "content": traffic_text},
    }
