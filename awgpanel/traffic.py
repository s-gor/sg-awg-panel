from __future__ import annotations

import ipaddress
from collections.abc import Iterable


MAX_EFFECTIVE_ROUTES = 256
AMNEZIA_FULL_TUNNEL_ALLOWED_IPS = "0.0.0.0/0, ::/0"


def normalize_networks(
    value: object,
    *,
    allow_empty: bool = True,
    max_items: int = 32,
    field: str = "Сети",
) -> str:
    raw = str(value or "").strip()
    if not raw:
        if allow_empty:
            return ""
        raise ValueError(f"{field}: значение не может быть пустым")
    parts = [part.strip() for part in raw.split(",")]
    if any(not part for part in parts) or len(parts) > max_items:
        raise ValueError(f"{field}: укажите не более {max_items} CIDR-сетей через запятую")
    networks: list[ipaddress.IPv4Network] = []
    for part in parts:
        try:
            network = ipaddress.ip_network(part, strict=False)
        except ValueError as exc:
            raise ValueError(f"{field}: некорректная сеть {part}") from exc
        if network.version != 4:
            raise ValueError(f"{field}: IPv6 пока не поддерживается")
        networks.append(network)
    collapsed = list(ipaddress.collapse_addresses(networks))
    return ", ".join(str(network) for network in collapsed)


def parse_networks(value: object) -> list[ipaddress.IPv4Network]:
    normalized = normalize_networks(value, allow_empty=True)
    if not normalized:
        return []
    return [ipaddress.ip_network(part.strip(), strict=False) for part in normalized.split(",")]


def _subtract_one(
    source: ipaddress.IPv4Network,
    excluded: ipaddress.IPv4Network,
) -> list[ipaddress.IPv4Network]:
    if not source.overlaps(excluded):
        return [source]
    if source.subnet_of(excluded):
        return []
    if excluded.subnet_of(source):
        return list(source.address_exclude(excluded))
    return [source]


def effective_allowed_ips(
    base_value: object,
    excluded_value: object = "",
    additional: Iterable[str] = (),
) -> str:
    base = parse_networks(base_value)
    for value in additional:
        base.extend(parse_networks(value))
    base = list(ipaddress.collapse_addresses(base))
    excluded = parse_networks(excluded_value)
    result = base
    for item in excluded:
        next_result: list[ipaddress.IPv4Network] = []
        for network in result:
            next_result.extend(_subtract_one(network, item))
        result = list(ipaddress.collapse_addresses(next_result))
        if len(result) > MAX_EFFECTIVE_ROUTES:
            raise ValueError(
                "Слишком много маршрутов после применения исключений. Уменьшите число или размер исключений."
            )
    return ", ".join(str(network) for network in result)


def exported_allowed_ips(
    base_value: object,
    excluded_value: object = "",
    additional: Iterable[str] = (),
) -> str:
    """Return the exact full-tunnel pair recognised by AmneziaVPN.

    Custom panel-managed routes remain IPv4-only and are rendered exactly as
    calculated. Only the ordinary full-tunnel profile receives ::/0.
    """
    extra = [str(value or "").strip() for value in additional]
    effective = effective_allowed_ips(base_value, excluded_value, extra)
    normalized_base = normalize_networks(base_value, allow_empty=True)
    normalized_excluded = normalize_networks(excluded_value, allow_empty=True)
    if normalized_base == "0.0.0.0/0" and not normalized_excluded and not any(extra):
        return AMNEZIA_FULL_TUNNEL_ALLOWED_IPS
    return effective


def validate_advertised_networks(
    advertised_value: object,
    *,
    server_network: str,
    existing_values: Iterable[tuple[int, object]],
    client_id: int,
) -> str:
    normalized = normalize_networks(
        advertised_value,
        allow_empty=True,
        field="Сети за клиентом",
    )
    advertised = parse_networks(normalized)
    server = ipaddress.ip_network(server_network, strict=True)
    for network in advertised:
        if network.overlaps(server):
            raise ValueError("Сеть за клиентом не должна пересекаться с сетью AWG")
    for other_id, value in existing_values:
        if int(other_id) == int(client_id):
            continue
        for other in parse_networks(value):
            for network in advertised:
                if network.overlaps(other):
                    raise ValueError(
                        f"Сеть {network} пересекается с сетью, уже назначенной другому клиенту"
                    )
    return normalized
