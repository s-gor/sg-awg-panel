from __future__ import annotations

import socket
from collections.abc import Mapping

MASKING_PROFILES: dict[str, dict[str, int]] = {
    "standard": {
        "jc": 6,
        "jmin": 64,
        "jmax": 128,
        "s1": 48,
        "s2": 48,
        "s3": 32,
        "s4": 16,
    },
    "enhanced": {
        "jc": 8,
        "jmin": 64,
        "jmax": 256,
        "s1": 48,
        "s2": 48,
        "s3": 32,
        "s4": 16,
    },
}

MASKING_KEYS = (
    "jc", "jmin", "jmax", "s1", "s2", "s3", "s4",
    "h1", "h2", "h3", "h4", "i1", "i2", "i3", "i4", "i5",
)

CLIENT_CONFIG_KEYS = (
    "endpoint_host", "listen_port", "server_network", "dns_servers", "mtu",
    *MASKING_KEYS,
)


def _text(value: object) -> str:
    return str(value if value is not None else "").strip()


def detect_masking_profile(settings: Mapping[str, object]) -> str:
    """Return the matching built-in profile or ``custom``.

    H1-H4 are deliberately random and therefore do not identify a profile.
    Any configured I1-I5 signature makes the result custom.
    """
    if any(_text(settings.get(f"i{i}", "")) for i in range(1, 6)):
        return "custom"

    for name, values in MASKING_PROFILES.items():
        if all(_text(settings.get(key)) == str(value) for key, value in values.items()):
            return name
    return "custom"


def server_change_summary(
    current: Mapping[str, object], submitted: Mapping[str, object]
) -> dict[str, object]:
    changed = tuple(
        key for key in CLIENT_CONFIG_KEYS + ("external_interface",)
        if _text(current.get(key)) != _text(submitted.get(key))
    )
    return {
        "changed": changed,
        "network_changed": "server_network" in changed,
        "udp_changed": "listen_port" in changed,
        "masking_changed": any(key in changed for key in MASKING_KEYS),
        "client_configs_changed": any(key in changed for key in CLIENT_CONFIG_KEYS),
    }


def ensure_udp_port_available(port: int, current_port: int | None = None) -> None:
    """Best-effort check that a newly selected UDP port can be bound.

    The currently configured port is allowed because it can already be owned by
    the running AWG service. The real apply step remains authoritative and is
    protected by the existing backup and rollback transaction.
    """
    if current_port is not None and port == current_port:
        return
    if not 1 <= port <= 65535:
        raise ValueError("UDP-порт должен быть от 1 до 65535")

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("0.0.0.0", port))
    except OSError as exc:
        raise ValueError(
            f"UDP-порт {port} уже занят другой службой. Выберите свободный порт."
        ) from exc
    finally:
        probe.close()
