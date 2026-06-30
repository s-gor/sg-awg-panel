from __future__ import annotations

import os
import secrets
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("AWGPANEL_DB", "/var/lib/sg-awg-panel/panel.db"))

ACTIVE_CLIENT_SQL = "enabled=1 AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS awg_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    configured INTEGER NOT NULL DEFAULT 0 CHECK (configured IN (0, 1)),
    interface_name TEXT NOT NULL DEFAULT 'awg0',
    endpoint_host TEXT NOT NULL DEFAULT '',
    listen_port INTEGER NOT NULL DEFAULT 585 CHECK (listen_port BETWEEN 1 AND 65535),
    server_network TEXT NOT NULL DEFAULT '10.77.0.0/24',
    dns_servers TEXT NOT NULL DEFAULT '1.1.1.1, 1.0.0.1',
    mtu INTEGER NOT NULL DEFAULT 1280 CHECK (mtu BETWEEN 576 AND 1500),
    external_interface TEXT NOT NULL DEFAULT '',
    private_key TEXT NOT NULL DEFAULT '',
    public_key TEXT NOT NULL DEFAULT '',
    jc INTEGER NOT NULL DEFAULT 6 CHECK (jc BETWEEN 0 AND 10),
    jmin INTEGER NOT NULL DEFAULT 64 CHECK (jmin BETWEEN 64 AND 1024),
    jmax INTEGER NOT NULL DEFAULT 128 CHECK (jmax BETWEEN 64 AND 1024),
    s1 INTEGER NOT NULL DEFAULT 48 CHECK (s1 BETWEEN 0 AND 64),
    s2 INTEGER NOT NULL DEFAULT 48 CHECK (s2 BETWEEN 0 AND 64),
    s3 INTEGER NOT NULL DEFAULT 32 CHECK (s3 BETWEEN 0 AND 64),
    s4 INTEGER NOT NULL DEFAULT 16 CHECK (s4 BETWEEN 0 AND 32),
    h1 TEXT NOT NULL DEFAULT '',
    h2 TEXT NOT NULL DEFAULT '',
    h3 TEXT NOT NULL DEFAULT '',
    h4 TEXT NOT NULL DEFAULT '',
    i1 TEXT NOT NULL DEFAULT '',
    i2 TEXT NOT NULL DEFAULT '',
    i3 TEXT NOT NULL DEFAULT '',
    i4 TEXT NOT NULL DEFAULT '',
    i5 TEXT NOT NULL DEFAULT '',
    isolate_clients INTEGER NOT NULL DEFAULT 1 CHECK (isolate_clients IN (0, 1)),
    nat_enabled INTEGER NOT NULL DEFAULT 1 CHECK (nat_enabled IN (0, 1)),
    server_lan_networks TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS awg_clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    address TEXT NOT NULL UNIQUE,
    private_key TEXT NOT NULL,
    public_key TEXT NOT NULL UNIQUE,
    preshared_key TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    allowed_ips TEXT NOT NULL DEFAULT '0.0.0.0/0',
    dns_servers TEXT NOT NULL DEFAULT '',
    mtu INTEGER,
    access_token TEXT NOT NULL DEFAULT '',
    access_enabled INTEGER NOT NULL DEFAULT 1 CHECK (access_enabled IN (0, 1)),
    access_downloads INTEGER NOT NULL DEFAULT 0,
    access_last_at TEXT,
    excluded_ips TEXT NOT NULL DEFAULT '',
    advertised_networks TEXT NOT NULL DEFAULT '',
    include_server_lan INTEGER NOT NULL DEFAULT 0 CHECK (include_server_lan IN (0, 1)),
    egress_mode TEXT NOT NULL DEFAULT 'awg_gateway',
    outbound_id INTEGER,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS outbounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    kind TEXT NOT NULL DEFAULT 'amneziawg',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    config_text TEXT NOT NULL,
    endpoint TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_outbounds_enabled
ON outbounds(enabled, id);



CREATE TABLE IF NOT EXISTS traffic_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('domains', 'cidrs')),
    source_type TEXT NOT NULL DEFAULT 'manual' CHECK (source_type IN ('manual', 'url')),
    source_url TEXT NOT NULL DEFAULT '',
    source_format TEXT NOT NULL DEFAULT 'plain' CHECK (source_format IN ('plain', 'antifilter', 'v2fly')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    auto_update INTEGER NOT NULL DEFAULT 0 CHECK (auto_update IN (0, 1)),
    builtin INTEGER NOT NULL DEFAULT 0 CHECK (builtin IN (0, 1)),
    content_text TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    item_count INTEGER NOT NULL DEFAULT 0,
    last_updated_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_traffic_lists_enabled
ON traffic_lists(enabled, kind, name);

CREATE TABLE IF NOT EXISTS traffic_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    priority INTEGER NOT NULL DEFAULT 100 CHECK (priority BETWEEN 1 AND 9999),
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    client_ids TEXT NOT NULL DEFAULT '',
    list_id INTEGER REFERENCES traffic_lists(id) ON DELETE RESTRICT,
    inline_domains TEXT NOT NULL DEFAULT '',
    inline_cidrs TEXT NOT NULL DEFAULT '',
    protocol TEXT NOT NULL DEFAULT 'any' CHECK (protocol IN ('any', 'tcp', 'udp')),
    ports TEXT NOT NULL DEFAULT '',
    invert_match INTEGER NOT NULL DEFAULT 0 CHECK (invert_match IN (0, 1)),
    schedule TEXT NOT NULL DEFAULT '',
    system_key TEXT NOT NULL DEFAULT '',
    action_mode TEXT NOT NULL DEFAULT 'awg_gateway' CHECK (action_mode IN ('awg_gateway', 'block', 'outbound')),
    outbound_id INTEGER REFERENCES outbounds(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_traffic_rules_order
ON traffic_rules(enabled, priority, id);


CREATE TABLE IF NOT EXISTS dns_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode TEXT NOT NULL DEFAULT 'redirect' CHECK (mode IN ('off', 'observe', 'redirect')),
    upstreams TEXT NOT NULL DEFAULT '1.1.1.1, 1.0.0.1',
    advertise_to_clients INTEGER NOT NULL DEFAULT 1 CHECK (advertise_to_clients IN (0, 1)),
    block_dot INTEGER NOT NULL DEFAULT 1 CHECK (block_dot IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS traffic_controls_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    allow_smtp25 INTEGER NOT NULL DEFAULT 0 CHECK (allow_smtp25 IN (0, 1)),
    allow_private_networks INTEGER NOT NULL DEFAULT 0 CHECK (allow_private_networks IN (0, 1)),
    allow_client_communication INTEGER NOT NULL DEFAULT 0 CHECK (allow_client_communication IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS panel_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    public_scheme TEXT NOT NULL DEFAULT 'http' CHECK (public_scheme IN ('http', 'https')),
    public_host TEXT NOT NULL DEFAULT '',
    public_port INTEGER NOT NULL DEFAULT 62443 CHECK (public_port BETWEEN 1 AND 65535),
    backend_address TEXT NOT NULL DEFAULT '127.0.0.1',
    backend_port INTEGER NOT NULL DEFAULT 18080 CHECK (backend_port BETWEEN 1 AND 65535),
    https_email TEXT NOT NULL DEFAULT '',
    https_enabled INTEGER NOT NULL DEFAULT 0 CHECK (https_enabled IN (0, 1)),
    manage_placeholder INTEGER NOT NULL DEFAULT 1 CHECK (manage_placeholder IN (0, 1)),
    ip_allowlist TEXT NOT NULL DEFAULT '',
    backup_schedule TEXT NOT NULL DEFAULT 'daily',
    backup_keep INTEGER NOT NULL DEFAULT 20 CHECK (backup_keep BETWEEN 1 AND 365),
    update_channel TEXT NOT NULL DEFAULT 'prerelease' CHECK (update_channel IN ('prerelease', 'stable')),
    latest_version TEXT NOT NULL DEFAULT '',
    latest_checked_at TEXT,
    latest_error TEXT NOT NULL DEFAULT '',
    auth_epoch INTEGER NOT NULL DEFAULT 1,
    access_enabled INTEGER NOT NULL DEFAULT 1 CHECK (access_enabled IN (0, 1)),
    access_profile_title TEXT NOT NULL DEFAULT 'SG-AWG',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash TEXT PRIMARY KEY,
    auth_epoch INTEGER NOT NULL DEFAULT 1,
    ip_address TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS auth_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    ip_address TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_web_sessions_active
ON web_sessions(revoked_at, last_seen_at);

CREATE INDEX IF NOT EXISTS idx_auth_events_created
ON auth_events(created_at DESC);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}


def _migrate_traffic_rules_gateway_mode(con: sqlite3.Connection) -> None:
    """Replace the legacy `direct` action with canonical `awg_gateway`."""
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='traffic_rules'"
    ).fetchone()
    table_sql = str(row[0] or "") if row else ""
    if "'direct'" not in table_sql and '"direct"' not in table_sql:
        return

    con.execute("ALTER TABLE traffic_rules RENAME TO traffic_rules_legacy")
    con.execute(
        """
        CREATE TABLE traffic_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            priority INTEGER NOT NULL DEFAULT 100 CHECK (priority BETWEEN 1 AND 9999),
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            client_ids TEXT NOT NULL DEFAULT '',
            list_id INTEGER REFERENCES traffic_lists(id) ON DELETE RESTRICT,
            inline_domains TEXT NOT NULL DEFAULT '',
            inline_cidrs TEXT NOT NULL DEFAULT '',
            protocol TEXT NOT NULL DEFAULT 'any' CHECK (protocol IN ('any', 'tcp', 'udp')),
            ports TEXT NOT NULL DEFAULT '',
            invert_match INTEGER NOT NULL DEFAULT 0 CHECK (invert_match IN (0, 1)),
            schedule TEXT NOT NULL DEFAULT '',
            system_key TEXT NOT NULL DEFAULT '',
            action_mode TEXT NOT NULL DEFAULT 'awg_gateway'
                CHECK (action_mode IN ('awg_gateway', 'block', 'outbound')),
            outbound_id INTEGER REFERENCES outbounds(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    con.execute(
        """
        INSERT INTO traffic_rules (
            id, priority, name, enabled, client_ids, list_id, inline_domains,
            inline_cidrs, protocol, ports, invert_match, schedule, system_key,
            action_mode, outbound_id, created_at, updated_at
        )
        SELECT
            id, priority, name, enabled, client_ids, list_id, inline_domains,
            inline_cidrs, protocol, ports, invert_match, schedule, '',
            CASE WHEN action_mode='direct' THEN 'awg_gateway' ELSE action_mode END,
            outbound_id, created_at, updated_at
        FROM traffic_rules_legacy
        """
    )
    con.execute("DROP TABLE traffic_rules_legacy")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_traffic_rules_order "
        "ON traffic_rules(enabled, priority, id)"
    )


def _migrate(con: sqlite3.Connection) -> None:
    settings_columns = _columns(con, "awg_settings")
    if "isolate_clients" not in settings_columns:
        con.execute(
            "ALTER TABLE awg_settings ADD COLUMN isolate_clients INTEGER NOT NULL DEFAULT 1"
        )
    if "nat_enabled" not in settings_columns:
        con.execute(
            "ALTER TABLE awg_settings ADD COLUMN nat_enabled INTEGER NOT NULL DEFAULT 1"
        )
    if "server_lan_networks" not in settings_columns:
        con.execute(
            "ALTER TABLE awg_settings ADD COLUMN server_lan_networks TEXT NOT NULL DEFAULT ''"
        )

    # Beta 8 removes the broad "server protection" feature set. Preserve only
    # the three explicit firewall exceptions and discard hidden lists/presets.
    old_protection = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='server_protection_settings'"
    ).fetchone()
    if old_protection:
        columns = _columns(con, "server_protection_settings")
        row = con.execute("SELECT * FROM server_protection_settings WHERE id=1").fetchone()
        if row is not None:
            block_smtp = bool(row["block_smtp"]) if "block_smtp" in columns else True
            block_private = bool(row["block_private_networks"]) if "block_private_networks" in columns else True
            isolate = bool(row["isolate_clients"]) if "isolate_clients" in columns else True
            con.execute(
                """
                INSERT INTO traffic_controls_settings (
                    id, allow_smtp25, allow_private_networks, allow_client_communication
                ) VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    allow_smtp25=excluded.allow_smtp25,
                    allow_private_networks=excluded.allow_private_networks,
                    allow_client_communication=excluded.allow_client_communication,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (0 if block_smtp else 1, 0 if block_private else 1, 0 if isolate else 1),
            )
        con.execute("DROP TABLE server_protection_settings")

    panel_columns = _columns(con, "panel_settings")
    if "manage_placeholder" not in panel_columns:
        con.execute(
            "ALTER TABLE panel_settings ADD COLUMN manage_placeholder INTEGER NOT NULL DEFAULT 1"
        )
    if "access_enabled" not in panel_columns:
        con.execute(
            "ALTER TABLE panel_settings ADD COLUMN access_enabled INTEGER NOT NULL DEFAULT 1"
        )
    if "access_profile_title" not in panel_columns:
        con.execute(
            "ALTER TABLE panel_settings ADD COLUMN access_profile_title TEXT NOT NULL DEFAULT 'SG-AWG'"
        )

    client_columns = _columns(con, "awg_clients")
    migrations = {
        "allowed_ips": "TEXT NOT NULL DEFAULT '0.0.0.0/0'",
        "dns_servers": "TEXT NOT NULL DEFAULT ''",
        "mtu": "INTEGER",
        "access_token": "TEXT NOT NULL DEFAULT ''",
        "access_enabled": "INTEGER NOT NULL DEFAULT 1",
        "access_downloads": "INTEGER NOT NULL DEFAULT 0",
        "access_last_at": "TEXT",
        "excluded_ips": "TEXT NOT NULL DEFAULT ''",
        "advertised_networks": "TEXT NOT NULL DEFAULT ''",
        "include_server_lan": "INTEGER NOT NULL DEFAULT 0",
        "egress_mode": "TEXT NOT NULL DEFAULT 'awg_gateway'",
        "outbound_id": "INTEGER",
        "expires_at": "TEXT",
    }
    for name, definition in migrations.items():
        if name not in client_columns:
            con.execute(f"ALTER TABLE awg_clients ADD COLUMN {name} {definition}")

    _migrate_traffic_rules_gateway_mode(con)

    rule_columns = _columns(con, "traffic_rules")
    if "system_key" not in rule_columns:
        con.execute(
            "ALTER TABLE traffic_rules ADD COLUMN system_key TEXT NOT NULL DEFAULT ''"
        )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_traffic_rules_system_key "
        "ON traffic_rules(system_key) WHERE system_key <> ''"
    )
    con.execute("DELETE FROM traffic_rules WHERE system_key <> ''")
    con.execute("DELETE FROM traffic_rules WHERE action_mode='awg_gateway'")
    con.execute("DELETE FROM traffic_lists WHERE slug IN ('security-threats','ads-trackers','p2p-trackers','doh-bypass')")

    # Domain traffic is a core feature, not an optional switch. Existing
    # installations are moved to the safe automatic DNS path during upgrade.
    con.execute(
        "UPDATE dns_settings SET mode='redirect', advertise_to_clients=1 "
        "WHERE id=1"
    )

    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_awg_clients_access_token "
        "ON awg_clients(access_token) WHERE access_token <> ''"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_awg_clients_lifecycle "
        "ON awg_clients(enabled, expires_at)"
    )

    rows = con.execute(
        "SELECT id FROM awg_clients WHERE access_token='' OR access_token IS NULL"
    ).fetchall()
    for row in rows:
        con.execute(
            "UPDATE awg_clients SET access_token=? WHERE id=?",
            (secrets.token_urlsafe(24), int(row["id"])),
        )

    con.execute(
        "UPDATE awg_clients SET egress_mode='awg_gateway' WHERE egress_mode='direct'"
    )
    con.execute(
        "UPDATE awg_clients SET egress_mode='awg_gateway', outbound_id=NULL "
        "WHERE egress_mode NOT IN ('awg_gateway', 'block', 'outbound') OR egress_mode IS NULL"
    )
    con.execute(
        "UPDATE awg_clients SET outbound_id=NULL WHERE egress_mode<>'outbound'"
    )
    con.execute(
        "UPDATE awg_clients SET egress_mode='awg_gateway', outbound_id=NULL "
        "WHERE egress_mode='outbound' AND outbound_id IS NULL"
    )


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)
        _migrate(con)
        con.execute(
            """
            INSERT OR IGNORE INTO awg_settings (
                id, configured, interface_name, endpoint_host, listen_port,
                server_network, dns_servers, mtu, external_interface,
                isolate_clients
            ) VALUES (1, 0, 'awg0', '', 585, '10.77.0.0/24',
                      '1.1.1.1, 1.0.0.1', 1280, '', 1)
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO dns_settings (
                id, mode, upstreams, advertise_to_clients, block_dot
            ) VALUES (1, 'redirect', '1.1.1.1, 1.0.0.1', 1, 1)
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO traffic_controls_settings (
                id, allow_smtp25, allow_private_networks, allow_client_communication
            ) VALUES (1, 0, 0, 0)
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO panel_settings (
                id, public_scheme, public_host, public_port,
                backend_address, backend_port, https_enabled, manage_placeholder,
                backup_schedule, backup_keep, update_channel, auth_epoch,
                access_enabled, access_profile_title
            ) VALUES (1, 'http', '', 62443, '127.0.0.1', 18080,
                      0, 1, 'daily', 20, 'prerelease', 1, 1, 'SG-AWG')
            """
        )
