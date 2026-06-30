from __future__ import annotations

import configparser
import ipaddress
import json
from io import StringIO
from typing import Any

from .errors import AWGPanelError
from .outbounds import parse_amneziawg_outbound_config
from .traffic import normalize_networks
from .traffic_modes import AWG_GATEWAY, normalize_egress_mode

KEEP_SECRET = "$KEEP"
META_KEY = "_sgAwgPanel"


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _object(text: str, label: str) -> dict[str, Any]:
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
        path = field or "$"
        raise ValueError(f"{path}: неизвестные поля: {', '.join(unknown)}")


def _array(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} должен быть массивом")
    return value


def _string(value: object, field: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} должен быть строкой")
    result = value.strip()
    if not result and not allow_empty:
        raise ValueError(f"{field} не может быть пустым")
    return result


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} должен быть true или false")
    return value


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} должен быть целым числом")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} должен быть от {minimum} до {maximum}")
    return value


def _networks_to_list(value: object) -> list[str]:
    raw = str(value or "").strip()
    return [part.strip() for part in raw.split(",") if part.strip()]


def _list_to_networks(value: object, field: str, *, allow_empty: bool = True) -> str:
    items = _array(value, field)
    if any(not isinstance(item, str) for item in items):
        raise ValueError(f"{field} должен содержать только строки CIDR")
    return normalize_networks(
        ", ".join(item.strip() for item in items),
        allow_empty=allow_empty,
        field=field,
    )


def _dns_to_list(value: object) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def server_json_document(settings: object) -> str:
    s = dict(settings)
    document = {
        META_KEY: {
            "format": "server-v1",
            "privateKey": KEEP_SECRET,
            "publicKey": str(s.get("public_key") or ""),
            "note": "Закрытый ключ не отображается и сохраняется без изменений.",
            "confirmNetworkChange": False,
        },
        "server": {
            "interfaceName": str(s.get("interface_name") or "awg0"),
            "endpointHost": str(s.get("endpoint_host") or ""),
            "listenPort": int(s.get("listen_port") or 585),
            "network": str(s.get("server_network") or "10.77.0.0/24"),
            "dnsServers": _dns_to_list(s.get("dns_servers")),
            "mtu": int(s.get("mtu") or 1280),
            "externalInterface": str(s.get("external_interface") or ""),
        },
        "masking": {
            "jc": int(s.get("jc") or 0),
            "jmin": int(s.get("jmin") or 0),
            "jmax": int(s.get("jmax") or 0),
            "s1": int(s.get("s1") or 0),
            "s2": int(s.get("s2") or 0),
            "s3": int(s.get("s3") or 0),
            "s4": int(s.get("s4") or 0),
            "h1": str(s.get("h1") or ""),
            "h2": str(s.get("h2") or ""),
            "h3": str(s.get("h3") or ""),
            "h4": str(s.get("h4") or ""),
            "i1": str(s.get("i1") or ""),
            "i2": str(s.get("i2") or ""),
            "i3": str(s.get("i3") or ""),
            "i4": str(s.get("i4") or ""),
            "i5": str(s.get("i5") or ""),
        },
    }
    return _dump(document)


def parse_server_json_document(text: str) -> tuple[dict[str, object], bool]:
    document = _object(text, "AWG Server")
    _reject_unknown_keys(document, {META_KEY, "server", "masking"}, "$")
    meta = _mapping(document.get(META_KEY, {}), META_KEY)
    _reject_unknown_keys(meta, {"format", "privateKey", "publicKey", "note", "confirmNetworkChange"}, META_KEY)
    if meta.get("format") not in (None, "server-v1"):
        raise ValueError(f"{META_KEY}.format должен быть server-v1")
    if meta.get("privateKey", KEEP_SECRET) != KEEP_SECRET:
        raise ValueError("Закрытый ключ AWG Server нельзя менять через JSON")
    confirm = _boolean(meta.get("confirmNetworkChange", False), f"{META_KEY}.confirmNetworkChange")
    server = _mapping(document.get("server"), "server")
    _reject_unknown_keys(server, {"interfaceName", "endpointHost", "listenPort", "network", "dnsServers", "mtu", "externalInterface"}, "server")
    masking = _mapping(document.get("masking"), "masking")
    _reject_unknown_keys(masking, {"jc", "jmin", "jmax", "s1", "s2", "s3", "s4", "h1", "h2", "h3", "h4", "i1", "i2", "i3", "i4", "i5"}, "masking")
    dns = _array(server.get("dnsServers"), "server.dnsServers")
    if any(not isinstance(item, str) for item in dns):
        raise ValueError("server.dnsServers должен содержать IP-адреса строками")
    values: dict[str, object] = {
        "interface_name": _string(server.get("interfaceName", "awg0"), "server.interfaceName", allow_empty=False),
        "endpoint_host": _string(server.get("endpointHost", ""), "server.endpointHost", allow_empty=False),
        "listen_port": _integer(server.get("listenPort"), "server.listenPort", 1, 65535),
        "server_network": _string(server.get("network", ""), "server.network", allow_empty=False),
        "dns_servers": ", ".join(_string(item, "server.dnsServers[]", allow_empty=False) for item in dns),
        "mtu": _integer(server.get("mtu"), "server.mtu", 576, 1500),
        "external_interface": _string(server.get("externalInterface", ""), "server.externalInterface", allow_empty=False),
    }
    ranges = {
        "jc": (0, 10), "jmin": (64, 1024), "jmax": (64, 1024),
        "s1": (0, 64), "s2": (0, 64), "s3": (0, 64), "s4": (0, 32),
    }
    for name, (minimum, maximum) in ranges.items():
        values[name] = _integer(masking.get(name), f"masking.{name}", minimum, maximum)
    for name in ("h1", "h2", "h3", "h4", "i1", "i2", "i3", "i4", "i5"):
        values[name] = _string(masking.get(name, ""), f"masking.{name}")
    return values, confirm


def client_json_document(client: object, settings: object) -> str:
    c = dict(client)
    s = dict(settings)
    document = {
        META_KEY: {
            "format": "client-v4",
            "privateKey": KEEP_SECRET,
            "presharedKey": KEEP_SECRET,
            "publicKey": str(c.get("public_key") or ""),
            "note": "Ключи нельзя менять через JSON. Для замены используйте пересоздание ключей.",
        },
        "client": {
            "id": int(c["id"]),
            "name": str(c["name"]),
            "enabled": bool(c["enabled"]),
            "comment": str(c.get("comment") or ""),
            "expiresAt": (
                str(c.get("expires_at")).replace(" ", "T") + "Z"
                if c.get("expires_at") else None
            ),
            "address": str(c["address"]),
            "dnsMode": "automatic",
            "mtu": int(c["mtu"]) if c.get("mtu") is not None else None,
            "inheritServerMtu": c.get("mtu") is None,
            "accessEnabled": bool(c.get("access_enabled", 1)),
        },
        "traffic": {
            "allowedIPs": _networks_to_list(c.get("allowed_ips")),
            "excludedIPs": _networks_to_list(c.get("excluded_ips")),
            "advertisedNetworks": _networks_to_list(c.get("advertised_networks")),
            "includeServerLAN": bool(c.get("include_server_lan", 0)),
            "egressMode": normalize_egress_mode(c.get("egress_mode") or AWG_GATEWAY),
            "outboundId": int(c["outbound_id"]) if c.get("outbound_id") is not None else None,
        },
    }
    return _dump(document)


def parse_client_json_document(text: str, *, expected_id: int) -> dict[str, object]:
    document = _object(text, "клиента")
    _reject_unknown_keys(document, {META_KEY, "client", "traffic"}, "$")
    meta = _mapping(document.get(META_KEY, {}), META_KEY)
    client_format = meta.get("format")
    if client_format not in (None, "client-v1", "client-v2", "client-v3", "client-v4"):
        raise ValueError(f"{META_KEY}.format должен быть client-v1, client-v2, client-v3 или client-v4")
    if meta.get("privateKey", KEEP_SECRET) != KEEP_SECRET or meta.get("presharedKey", KEEP_SECRET) != KEEP_SECRET:
        raise ValueError("Ключи клиента нельзя менять через JSON")
    client = _mapping(document.get("client"), "client")
    if client_format == "client-v4":
        _reject_unknown_keys(client, {"id", "name", "enabled", "comment", "expiresAt", "address", "dnsMode", "mtu", "inheritServerMtu", "accessEnabled"}, "client")
    traffic = _mapping(document.get("traffic"), "traffic")
    _reject_unknown_keys(traffic, {"allowedIPs", "excludedIPs", "advertisedNetworks", "includeServerLAN", "egressMode", "outboundId"}, "traffic")
    client_id = _integer(client.get("id"), "client.id", 1, 2_147_483_647)
    if client_id != int(expected_id):
        raise ValueError("client.id не совпадает с редактируемым клиентом")
    inherit_mtu = _boolean(client.get("inheritServerMtu", False), "client.inheritServerMtu")
    # Beta 1 always advertises the текущий сервер DNS address to clients so
    # domain traffic works without additional client settings. Older JSON
    # formats are accepted, but their per-client DNS values are normalized.
    if client_format == "client-v4":
        if client.get("dnsMode", "automatic") != "automatic":
            raise ValueError("client.dnsMode должен быть automatic")
    else:
        dns_values = _array(client.get("dnsServers", []), "client.dnsServers")
        if any(not isinstance(item, str) for item in dns_values):
            raise ValueError("client.dnsServers должен содержать строки")
    expires_at = client.get("expiresAt")
    if expires_at is not None and not isinstance(expires_at, str):
        raise ValueError("client.expiresAt должен быть строкой ISO 8601 или null")
    mtu_value = client.get("mtu")
    if inherit_mtu:
        mtu: int | None = None
    elif mtu_value is None:
        raise ValueError("client.mtu обязателен, если inheritServerMtu=false")
    else:
        mtu = _integer(mtu_value, "client.mtu", 576, 1500)
    try:
        mode = normalize_egress_mode(
            _string(traffic.get("egressMode", AWG_GATEWAY), "traffic.egressMode", allow_empty=False)
        )
    except ValueError as exc:
        raise ValueError("traffic.egressMode должен быть awg_gateway, block или outbound") from exc
    outbound_id = traffic.get("outboundId")
    if mode == "outbound":
        outbound_id = _integer(outbound_id, "traffic.outboundId", 1, 32)
    elif outbound_id is not None:
        raise ValueError("traffic.outboundId должен быть null для текущий сервер и Block")
    return {
        "id": client_id,
        "name": _string(client.get("name", ""), "client.name", allow_empty=False),
        "enabled": _boolean(client.get("enabled"), "client.enabled"),
        "comment": _string(client.get("comment", ""), "client.comment"),
        "expires_at": expires_at,
        "address": _string(client.get("address", ""), "client.address", allow_empty=False),
        "dns_servers": "",
        "mtu": mtu,
        "access_enabled": _boolean(client.get("accessEnabled", True), "client.accessEnabled"),
        "allowed_ips": _list_to_networks(traffic.get("allowedIPs", []), "traffic.allowedIPs", allow_empty=False),
        "excluded_ips": _list_to_networks(traffic.get("excludedIPs", []), "traffic.excludedIPs"),
        "advertised_networks": _list_to_networks(traffic.get("advertisedNetworks", []), "traffic.advertisedNetworks"),
        "include_server_lan": _boolean(traffic.get("includeServerLAN", False), "traffic.includeServerLAN"),
        "egress_mode": mode,
        "outbound_id": outbound_id,
    }


def network_json_document(settings: object, clients: list[object]) -> str:
    s = dict(settings)
    client_rows = [dict(item) for item in clients]
    document = {
        META_KEY: {
            "format": "traffic-v2",
            "note": "Список clients должен содержать все текущие клиенты ровно по одному разу.",
        },
        "settings": {
            "externalInterface": str(s.get("external_interface") or ""),
            "natEnabled": bool(s.get("nat_enabled", 1)),
            "isolateClients": bool(s.get("isolate_clients", 1)),
            "serverLanNetworks": _networks_to_list(s.get("server_lan_networks")),
        },
        "clients": [
            {
                "id": int(c["id"]),
                "name": str(c["name"]),
                "egress": {
                    "mode": normalize_egress_mode(c.get("egress_mode") or AWG_GATEWAY),
                    "outboundId": int(c["outbound_id"]) if c.get("outbound_id") is not None else None,
                },
                "routes": {
                    "allowedIPs": _networks_to_list(c.get("allowed_ips")),
                    "excludedIPs": _networks_to_list(c.get("excluded_ips")),
                    "advertisedNetworks": _networks_to_list(c.get("advertised_networks")),
                    "includeServerLAN": bool(c.get("include_server_lan", 0)),
                },
            }
            for c in client_rows
        ],
    }
    return _dump(document)


def parse_network_json_document(text: str) -> tuple[dict[str, object], list[dict[str, object]]]:
    document = _object(text, "Network")
    _reject_unknown_keys(document, {META_KEY, "settings", "clients"}, "$")
    meta = _mapping(document.get(META_KEY, {}), META_KEY)
    if meta.get("format") not in (None, "traffic-v1", "traffic-v2"):
        raise ValueError(f"{META_KEY}.format должен быть traffic-v1 или traffic-v2")
    settings = _mapping(document.get("settings"), "settings")
    _reject_unknown_keys(settings, {"externalInterface", "natEnabled", "isolateClients", "serverLanNetworks"}, "settings")
    clients = _array(document.get("clients"), "clients")
    parsed_settings = {
        "external_interface": _string(settings.get("externalInterface", ""), "settings.externalInterface", allow_empty=False),
        "nat_enabled": _boolean(settings.get("natEnabled"), "settings.natEnabled"),
        "isolate_clients": _boolean(settings.get("isolateClients"), "settings.isolateClients"),
        "server_lan_networks": _list_to_networks(settings.get("serverLanNetworks", []), "settings.serverLanNetworks"),
    }
    parsed_clients: list[dict[str, object]] = []
    seen: set[int] = set()
    for index, item in enumerate(clients):
        value = _mapping(item, f"clients[{index}]")
        _reject_unknown_keys(value, {"id", "name", "egress", "routes"}, f"clients[{index}]")
        client_id = _integer(value.get("id"), f"clients[{index}].id", 1, 2_147_483_647)
        if client_id in seen:
            raise ValueError(f"clients[{index}].id повторяется")
        seen.add(client_id)
        egress = _mapping(value.get("egress"), f"clients[{index}].egress")
        _reject_unknown_keys(egress, {"mode", "outboundId"}, f"clients[{index}].egress")
        routes = _mapping(value.get("routes"), f"clients[{index}].routes")
        _reject_unknown_keys(routes, {"allowedIPs", "excludedIPs", "advertisedNetworks", "includeServerLAN"}, f"clients[{index}].routes")
        try:
            mode = normalize_egress_mode(
                _string(egress.get("mode", AWG_GATEWAY), f"clients[{index}].egress.mode", allow_empty=False)
            )
        except ValueError as exc:
            raise ValueError(
                f"clients[{index}].egress.mode должен быть awg_gateway, block или outbound"
            ) from exc
        outbound_id = egress.get("outboundId")
        if mode == "outbound":
            outbound_id = _integer(outbound_id, f"clients[{index}].egress.outboundId", 1, 32)
        elif outbound_id is not None:
            raise ValueError(f"clients[{index}].egress.outboundId должен быть null для текущий сервер и Block")
        parsed_clients.append({
            "id": client_id,
            "egress_mode": mode,
            "outbound_id": outbound_id,
            "allowed_ips": _list_to_networks(routes.get("allowedIPs", []), f"clients[{index}].routes.allowedIPs", allow_empty=False),
            "excluded_ips": _list_to_networks(routes.get("excludedIPs", []), f"clients[{index}].routes.excludedIPs"),
            "advertised_networks": _list_to_networks(routes.get("advertisedNetworks", []), f"clients[{index}].routes.advertisedNetworks"),
            "include_server_lan": _boolean(routes.get("includeServerLAN", False), f"clients[{index}].routes.includeServerLAN"),
        })
    return parsed_settings, parsed_clients


def _read_ini(config_text: str) -> tuple[dict[str, str], dict[str, str]]:
    parser = configparser.RawConfigParser(interpolation=None, delimiters=("=",), strict=True)
    parser.optionxform = str
    parser.read_file(StringIO(config_text))
    interface = {key: value for key, value in parser.items("Interface")}
    peer = {key: value for key, value in parser.items("Peer")}
    return interface, peer


def _ci_get(values: dict[str, str], key: str, default: str = "") -> str:
    for name, value in values.items():
        if name.lower() == key.lower():
            return value
    return default


def outbound_json_document(outbound: object | None = None) -> str:
    if outbound is None:
        document = {
            META_KEY: {"format": "outbound-v1", "name": "Europe", "enabled": True},
            "interface": {
                "address": "10.50.0.2/32",
                "privateKey": "REPLACE_PRIVATE_KEY",
                "mtu": 1280,
                "jc": 6, "jmin": 64, "jmax": 128,
                "s1": 48, "s2": 48, "s3": 32, "s4": 16,
                "h1": "", "h2": "", "h3": "", "h4": "",
                "i1": "", "i2": "", "i3": "", "i4": "", "i5": "",
            },
            "peer": {
                "publicKey": "REPLACE_PUBLIC_KEY",
                "presharedKey": "",
                "allowedIPs": ["0.0.0.0/0"],
                "endpoint": "vpn.example.com:51820",
                "persistentKeepalive": 25,
            },
        }
    else:
        row = dict(outbound)
        interface, peer = _read_ini(str(row["config_text"]))
        document = {
            META_KEY: {
                "format": "outbound-v1",
                "id": int(row["id"]),
                "name": str(row["name"]),
                "enabled": bool(row["enabled"]),
                "note": "PrivateKey и PresharedKey со значением $KEEP сохраняются без изменений.",
            },
            "interface": {
                "address": _ci_get(interface, "Address"),
                "privateKey": KEEP_SECRET,
                "mtu": int(_ci_get(interface, "MTU", "1280") or 1280),
                "jc": int(_ci_get(interface, "Jc", "0") or 0),
                "jmin": int(_ci_get(interface, "Jmin", "64") or 64),
                "jmax": int(_ci_get(interface, "Jmax", "128") or 128),
                "s1": int(_ci_get(interface, "S1", "0") or 0),
                "s2": int(_ci_get(interface, "S2", "0") or 0),
                "s3": int(_ci_get(interface, "S3", "0") or 0),
                "s4": int(_ci_get(interface, "S4", "0") or 0),
                **{f"h{i}": _ci_get(interface, f"H{i}") for i in range(1, 5)},
                **{f"i{i}": _ci_get(interface, f"I{i}") for i in range(1, 6)},
            },
            "peer": {
                "publicKey": _ci_get(peer, "PublicKey"),
                "presharedKey": KEEP_SECRET if _ci_get(peer, "PresharedKey") else "",
                "allowedIPs": _networks_to_list(_ci_get(peer, "AllowedIPs")),
                "endpoint": _ci_get(peer, "Endpoint"),
                "persistentKeepalive": int(_ci_get(peer, "PersistentKeepalive", "25") or 25),
            },
        }
    return _dump(document)


def parse_outbound_json_document(text: str, *, current: object | None = None) -> tuple[str, str, bool]:
    document = _object(text, "Outbound")
    _reject_unknown_keys(document, {META_KEY, "interface", "peer"}, "$")
    meta = _mapping(document.get(META_KEY, {}), META_KEY)
    _reject_unknown_keys(meta, {"format", "id", "name", "enabled", "note"}, META_KEY)
    if meta.get("format") not in (None, "outbound-v1"):
        raise ValueError(f"{META_KEY}.format должен быть outbound-v1")
    name = _string(meta.get("name", ""), f"{META_KEY}.name", allow_empty=False)
    enabled = _boolean(meta.get("enabled", True), f"{META_KEY}.enabled")
    interface = _mapping(document.get("interface"), "interface")
    _reject_unknown_keys(interface, {"address", "privateKey", "mtu", "jc", "jmin", "jmax", "s1", "s2", "s3", "s4", "h1", "h2", "h3", "h4", "i1", "i2", "i3", "i4", "i5"}, "interface")
    peer = _mapping(document.get("peer"), "peer")
    _reject_unknown_keys(peer, {"publicKey", "presharedKey", "allowedIPs", "endpoint", "persistentKeepalive"}, "peer")
    current_interface: dict[str, str] = {}
    current_peer: dict[str, str] = {}
    if current is not None:
        current_interface, current_peer = _read_ini(str(dict(current)["config_text"]))
    private_key = _string(interface.get("privateKey", ""), "interface.privateKey", allow_empty=False)
    if private_key == KEEP_SECRET:
        private_key = _ci_get(current_interface, "PrivateKey")
        if not private_key:
            raise ValueError("$KEEP нельзя использовать при создании Outbound")
    preshared_key = _string(peer.get("presharedKey", ""), "peer.presharedKey")
    if preshared_key == KEEP_SECRET:
        preshared_key = _ci_get(current_peer, "PresharedKey")
        if not preshared_key:
            raise ValueError("В текущем Outbound нет PresharedKey для $KEEP")
    address = _string(interface.get("address", ""), "interface.address", allow_empty=False)
    try:
        parsed_address = ipaddress.ip_interface(address)
    except ValueError as exc:
        raise ValueError("interface.address должен быть IPv4-адресом с префиксом") from exc
    if parsed_address.version != 4:
        raise ValueError("IPv6 Outbound пока не поддерживается")
    lines = ["[Interface]", f"Address = {parsed_address}", f"PrivateKey = {private_key}", "Table = off"]
    integer_fields = {
        "mtu": (576, 1500, "MTU"), "jc": (0, 10, "Jc"),
        "jmin": (64, 1024, "Jmin"), "jmax": (64, 1024, "Jmax"),
        "s1": (0, 64, "S1"), "s2": (0, 64, "S2"),
        "s3": (0, 64, "S3"), "s4": (0, 32, "S4"),
    }
    for key, (minimum, maximum, output_name) in integer_fields.items():
        if key in interface and interface[key] is not None:
            lines.append(f"{output_name} = {_integer(interface[key], f'interface.{key}', minimum, maximum)}")
    for prefix, count in (("h", 4), ("i", 5)):
        for index in range(1, count + 1):
            key = f"{prefix}{index}"
            value = _string(interface.get(key, ""), f"interface.{key}")
            if value:
                lines.append(f"{key.upper()} = {value}")
    allowed = _list_to_networks(peer.get("allowedIPs", []), "peer.allowedIPs", allow_empty=False)
    public_key = _string(peer.get("publicKey", ""), "peer.publicKey", allow_empty=False)
    endpoint = _string(peer.get("endpoint", ""), "peer.endpoint", allow_empty=False)
    keepalive = _integer(peer.get("persistentKeepalive", 25), "peer.persistentKeepalive", 0, 65535)
    lines.extend(["", "[Peer]", f"PublicKey = {public_key}"])
    if preshared_key:
        lines.append(f"PresharedKey = {preshared_key}")
    lines.extend([f"AllowedIPs = {allowed}", f"Endpoint = {endpoint}", f"PersistentKeepalive = {keepalive}"])
    parsed = parse_amneziawg_outbound_config("\n".join(lines) + "\n")
    return name, parsed.config_text, enabled


def dns_json_document(settings: object) -> str:
    row = dict(settings)
    return _dump({
        META_KEY: {
            "format": "dns-v1",
            "note": "Эти серверы используются текущий сервер как внешние DNS. Клиенты получают DNS текущий сервер автоматически.",
        },
        "dns": {
            "servers": _dns_to_list(row.get("dns_servers")),
        },
    })


def parse_dns_json_document(text: str) -> str:
    document = _object(text, "DNS")
    meta = _mapping(document.get(META_KEY, {}), META_KEY)
    if meta.get("format") not in (None, "dns-v1"):
        raise ValueError(f"{META_KEY}.format должен быть dns-v1")
    dns = _mapping(document.get("dns"), "dns")
    servers = _array(dns.get("servers"), "dns.servers")
    if not 1 <= len(servers) <= 4:
        raise ValueError("dns.servers должен содержать от одного до четырёх IP-адресов")
    normalized: list[str] = []
    for index, item in enumerate(servers):
        raw = _string(item, f"dns.servers[{index}]", allow_empty=False)
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise ValueError(f"Некорректный DNS IP-адрес: {raw}") from exc
        normalized.append(str(address))
    return ", ".join(dict.fromkeys(normalized))


def backup_json_document(panel: object) -> str:
    row = dict(panel)
    return _dump({
        META_KEY: {
            "format": "backups-v1",
            "note": "JSON управляет расписанием и количеством хранимых копий.",
        },
        "backups": {
            "schedule": str(row.get("backup_schedule") or "daily"),
            "keep": int(row.get("backup_keep") or 20),
        },
    })


def parse_backup_json_document(text: str) -> tuple[str, int]:
    document = _object(text, "резервных копий")
    meta = _mapping(document.get(META_KEY, {}), META_KEY)
    if meta.get("format") not in (None, "backups-v1"):
        raise ValueError(f"{META_KEY}.format должен быть backups-v1")
    backups = _mapping(document.get("backups"), "backups")
    schedule = _string(backups.get("schedule", ""), "backups.schedule", allow_empty=False)
    allowed = {"hourly", "every_6_hours", "daily", "weekly", "disabled"}
    if schedule not in allowed:
        raise ValueError(
            "backups.schedule должен быть hourly, every_6_hours, daily, weekly или disabled"
        )
    keep = _integer(backups.get("keep"), "backups.keep", 1, 365)
    return schedule, keep


def access_json_document(panel: object, clients: list[object]) -> str:
    settings = dict(panel)
    rows = [dict(item) for item in clients]
    return _dump({
        META_KEY: {
            "format": "access-v1",
            "tokens": KEEP_SECRET,
            "note": "Секретные токены ссылок не отображаются и сохраняются без изменений.",
        },
        "publicLinks": {
            "enabled": bool(settings.get("access_enabled")),
            "profileTitle": str(settings.get("access_profile_title") or "SG-AWG"),
        },
        "clients": [
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "linkEnabled": bool(row.get("access_enabled", 1)),
            }
            for row in rows
        ],
    })


def parse_access_json_document(
    text: str, *, expected_client_ids: set[int]
) -> tuple[bool, str, dict[int, bool]]:
    document = _object(text, "доступа к конфигурациям")
    meta = _mapping(document.get(META_KEY, {}), META_KEY)
    if meta.get("format") not in (None, "access-v1"):
        raise ValueError(f"{META_KEY}.format должен быть access-v1")
    if meta.get("tokens", KEEP_SECRET) != KEEP_SECRET:
        raise ValueError(f"{META_KEY}.tokens должен оставаться {KEEP_SECRET}")
    links = _mapping(document.get("publicLinks"), "publicLinks")
    enabled = _boolean(links.get("enabled"), "publicLinks.enabled")
    title = _string(
        links.get("profileTitle", ""), "publicLinks.profileTitle", allow_empty=False
    )
    if len(title) > 80:
        raise ValueError("publicLinks.profileTitle должен содержать не более 80 символов")
    if any(ord(ch) < 32 for ch in title):
        raise ValueError("publicLinks.profileTitle содержит недопустимые символы")
    client_items = _array(document.get("clients"), "clients")
    states: dict[int, bool] = {}
    for index, item in enumerate(client_items):
        row = _mapping(item, f"clients[{index}]")
        client_id = _integer(row.get("id"), f"clients[{index}].id", 1, 2_147_483_647)
        if client_id in states:
            raise ValueError(f"clients[{index}].id повторяется")
        states[client_id] = _boolean(
            row.get("linkEnabled"), f"clients[{index}].linkEnabled"
        )
    if set(states) != set(expected_client_ids):
        missing = sorted(set(expected_client_ids) - set(states))
        extra = sorted(set(states) - set(expected_client_ids))
        parts: list[str] = []
        if missing:
            parts.append("отсутствуют клиенты: " + ", ".join(map(str, missing)))
        if extra:
            parts.append("неизвестные клиенты: " + ", ".join(map(str, extra)))
        raise ValueError("clients должен содержать полный текущий список; " + "; ".join(parts))
    return enabled, title, states


def security_json_document(panel: object) -> str:
    row = dict(panel)
    allowlist = [part.strip() for part in str(row.get("ip_allowlist") or "").split(",") if part.strip()]
    return _dump({
        META_KEY: {
            "format": "security-v1",
            "backend": "127.0.0.1:18080",
            "note": "Пароль и активные сессии являются отдельными защищёнными действиями и в JSON не выводятся.",
        },
        "panelAccess": {
            "scheme": str(row.get("public_scheme") or "http"),
            "host": str(row.get("public_host") or ""),
            "port": int(row.get("public_port") or 62443),
            "managePlaceholder": bool(row.get("manage_placeholder", 1)),
        },
        "ipAllowlist": allowlist,
    })


def parse_security_json_document(text: str) -> tuple[dict[str, object], str]:
    document = _object(text, "безопасности")
    meta = _mapping(document.get(META_KEY, {}), META_KEY)
    if meta.get("format") not in (None, "security-v1"):
        raise ValueError(f"{META_KEY}.format должен быть security-v1")
    access = _mapping(document.get("panelAccess"), "panelAccess")
    scheme = _string(access.get("scheme", ""), "panelAccess.scheme", allow_empty=False).lower()
    if scheme not in {"http", "https"}:
        raise ValueError("panelAccess.scheme должен быть http или https")
    host = _string(access.get("host", ""), "panelAccess.host").rstrip(".")
    if scheme == "https" and not host:
        raise ValueError("Для HTTPS требуется panelAccess.host")
    if host:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("Некорректный домен панели") from exc
        labels = host.split(".")
        if len(labels) < 2 or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or any(not (ch.isalnum() or ch == "-") for ch in label)
            for label in labels
        ):
            raise ValueError("Укажите полное доменное имя, например awg.example.com")
    port = _integer(access.get("port"), "panelAccess.port", 49152, 65535)
    placeholder = _boolean(
        access.get("managePlaceholder"), "panelAccess.managePlaceholder"
    )
    allowlist_items = _array(document.get("ipAllowlist"), "ipAllowlist")
    normalized: list[str] = []
    for index, item in enumerate(allowlist_items):
        raw = _string(item, f"ipAllowlist[{index}]", allow_empty=False)
        try:
            if "/" not in raw:
                address = ipaddress.ip_address(raw)
                raw = f"{address}/{32 if address.version == 4 else 128}"
            network = ipaddress.ip_network(raw, strict=False)
        except ValueError as exc:
            raise ValueError(f"Некорректный IP или CIDR: {raw}") from exc
        normalized.append(str(network))
    if len(normalized) > 64:
        raise ValueError("ipAllowlist должен содержать не более 64 сетей")
    values = {
        "scheme": scheme,
        "public_host": host,
        "public_port": port,
        "manage_placeholder": placeholder,
    }
    return values, ", ".join(dict.fromkeys(normalized))
