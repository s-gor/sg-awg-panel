from __future__ import annotations

import ipaddress
import socket
from collections.abc import Mapping
from dataclasses import dataclass


STANDARD_PROFILE = {
    "jc": 6,
    "jmin": 64,
    "jmax": 128,
    "s1": 48,
    "s2": 48,
    "s3": 32,
    "s4": 16,
}

HARDENED_PROFILE = {
    "jc": 8,
    "jmin": 96,
    "jmax": 256,
    "s1": 64,
    "s2": 64,
    "s3": 48,
    "s4": 24,
}

PROFILE_FIELDS = tuple(STANDARD_PROFILE)


def _value(mapping: Mapping[str, object], key: str, default: object = None) -> object:
    getter = getattr(mapping, "get", None)
    if callable(getter):
        return getter(key, default)
    try:
        return mapping[key]
    except (KeyError, IndexError, TypeError):
        return default


def detect_masking_profile(settings: Mapping[str, object]) -> str:
    values = {key: int(_value(settings, key, 0) or 0) for key in PROFILE_FIELDS}
    if values == STANDARD_PROFILE:
        return "standard"
    if values == HARDENED_PROFILE:
        return "hardened"
    return "custom"


def profile_values(name: str) -> dict[str, int]:
    if name == "standard":
        return dict(STANDARD_PROFILE)
    if name == "hardened":
        return dict(HARDENED_PROFILE)
    if name == "custom":
        return {}
    raise ValueError("Неизвестный профиль маскировки")


@dataclass(frozen=True)
class ServerChangeSummary:
    changed: tuple[str, ...]
    clients_need_new_config: bool
    client_addresses_change: bool
    service_restart_required: bool


DISPLAY_NAMES = {
    "endpoint_host": "Endpoint",
    "listen_port": "UDP-порт",
    "server_network": "сеть клиентов",
    "dns_servers": "DNS",
    "mtu": "MTU",
    "external_interface": "внешний интерфейс",
    **{key: key.upper() for key in (*PROFILE_FIELDS, "h1", "h2", "h3", "h4", "i1", "i2", "i3", "i4", "i5")},
}


def server_change_summary(
    current: Mapping[str, object], submitted: Mapping[str, object]
) -> ServerChangeSummary:
    changed: list[str] = []
    keys = tuple(DISPLAY_NAMES)
    for key in keys:
        old = str(_value(current, key, "")).strip()
        new = str(_value(submitted, key, old)).strip()
        if old != new:
            changed.append(key)
    changed_set = set(changed)
    client_config_fields = {
        "endpoint_host", "listen_port", "server_network", "dns_servers", "mtu",
        *PROFILE_FIELDS, "h1", "h2", "h3", "h4", "i1", "i2", "i3", "i4", "i5",
    }
    return ServerChangeSummary(
        changed=tuple(changed),
        clients_need_new_config=bool(changed_set & client_config_fields),
        client_addresses_change="server_network" in changed_set,
        service_restart_required=bool(changed_set),
    )


def readable_changes(summary: ServerChangeSummary) -> list[str]:
    return [DISPLAY_NAMES.get(key, key) for key in summary.changed]


def network_client_addresses(network_value: str, count: int) -> list[str]:
    network = ipaddress.ip_network(network_value, strict=True)
    hosts = network.hosts()
    next(hosts, None)
    result: list[str] = []
    for _ in range(count):
        host = next(hosts, None)
        if host is None:
            raise ValueError("В новой сети недостаточно адресов для существующих клиентов")
        result.append(f"{host}/32")
    return result


def ensure_udp_port_available(port: int, *, current_port: int | None = None) -> None:
    if current_port is not None and int(port) == int(current_port):
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind(("0.0.0.0", int(port)))
    except OSError as exc:
        raise ValueError(f"UDP-порт {port} уже занят другой службой") from exc
    finally:
        sock.close()
