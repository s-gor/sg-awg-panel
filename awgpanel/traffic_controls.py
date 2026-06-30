from __future__ import annotations

from typing import Any

from .db import connect, init_db
from .errors import AWGPanelError


def get_traffic_controls_settings() -> dict[str, Any]:
    """Return the small set of explicit AWG-client firewall exceptions.

    Safe defaults are represented as disabled allowances:
    - SMTP TCP/25 is blocked unless allow_smtp25 is true;
    - private/special IPv4 networks are blocked unless allow_private_networks is true;
    - AWG clients are isolated unless allow_client_communication is true.
    Cloud metadata remains blocked whenever private networks are allowed.
    """
    init_db()
    with connect() as con:
        row = con.execute(
            "SELECT * FROM traffic_controls_settings WHERE id=1"
        ).fetchone()
    if row is None:
        raise AWGPanelError("Настройки ограничений трафика не найдены")
    return dict(row)


def save_traffic_controls_settings(
    *,
    allow_smtp25: object,
    allow_private_networks: object,
    allow_client_communication: object,
) -> dict[str, Any]:
    values = {
        "allow_smtp25": bool(allow_smtp25),
        "allow_private_networks": bool(allow_private_networks),
        "allow_client_communication": bool(allow_client_communication),
    }
    init_db()
    with connect() as con:
        con.execute(
            """
            UPDATE traffic_controls_settings SET
                allow_smtp25=?, allow_private_networks=?,
                allow_client_communication=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=1
            """,
            (
                1 if values["allow_smtp25"] else 0,
                1 if values["allow_private_networks"] else 0,
                1 if values["allow_client_communication"] else 0,
            ),
        )
        con.execute(
            "UPDATE awg_settings SET isolate_clients=?, updated_at=CURRENT_TIMESTAMP WHERE id=1",
            (0 if values["allow_client_communication"] else 1,),
        )
    return get_traffic_controls_settings()


def nft_options(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    values = settings or get_traffic_controls_settings()
    allow_private = bool(values["allow_private_networks"])
    return {
        "block_smtp": not bool(values["allow_smtp25"]),
        "block_private_networks": not allow_private,
        # Metadata is never exposed to AWG clients, even when private networks
        # are deliberately allowed for a site-to-site or home-LAN use case.
        "block_metadata": True,
        "isolate_clients": not bool(values["allow_client_communication"]),
    }
