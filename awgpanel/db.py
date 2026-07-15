from __future__ import annotations

import ipaddress
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
    address TEXT NOT NULL,
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
    system_role TEXT NOT NULL DEFAULT '',
    node_id INTEGER REFERENCES cluster_nodes(id) ON DELETE RESTRICT,
    deployment_state TEXT NOT NULL DEFAULT 'active' CHECK (deployment_state IN ('queued', 'active', 'error', 'deleting')),
    deployment_job_id INTEGER,
    deployment_error TEXT NOT NULL DEFAULT '',
    deployed_enabled INTEGER NOT NULL DEFAULT 1 CHECK (deployed_enabled IN (0, 1)),
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
    instance_name TEXT NOT NULL DEFAULT 'SG-AWG-Panel',
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


CREATE TABLE IF NOT EXISTS cluster_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'node' CHECK (role IN ('controller', 'node')),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'online', 'offline', 'error', 'disabled')),
    is_local INTEGER NOT NULL DEFAULT 0 CHECK (is_local IN (0, 1)),
    node_slot INTEGER CHECK (node_slot BETWEEN 0 AND 12),
    vpn_network TEXT NOT NULL DEFAULT '',
    public_host TEXT NOT NULL DEFAULT '',
    public_port INTEGER NOT NULL DEFAULT 585 CHECK (public_port BETWEEN 1 AND 65535),
    enrollment_token_hash TEXT NOT NULL DEFAULT '',
    enrollment_expires_at TEXT,
    agent_token_hash TEXT NOT NULL DEFAULT '',
    registered_at TEXT,
    last_seen_at TEXT,
    agent_version TEXT NOT NULL DEFAULT '',
    os_name TEXT NOT NULL DEFAULT '',
    os_version TEXT NOT NULL DEFAULT '',
    kernel TEXT NOT NULL DEFAULT '',
    machine_id TEXT NOT NULL DEFAULT '',
    public_ipv4 TEXT NOT NULL DEFAULT '',
    private_ipv4 TEXT NOT NULL DEFAULT '',
    country_code TEXT NOT NULL DEFAULT '',
    country_mode TEXT NOT NULL DEFAULT 'auto' CHECK (country_mode IN ('auto', 'manual')),
    country_updated_at TEXT,
    awg_version TEXT NOT NULL DEFAULT '',
    panel_version TEXT NOT NULL DEFAULT '',
    cpu_percent REAL NOT NULL DEFAULT 0,
    memory_percent REAL NOT NULL DEFAULT 0,
    disk_percent REAL NOT NULL DEFAULT 0,
    load1 REAL NOT NULL DEFAULT 0,
    service_awg TEXT NOT NULL DEFAULT 'unknown',
    service_traffic TEXT NOT NULL DEFAULT 'unknown',
    service_nginx TEXT NOT NULL DEFAULT 'unknown',
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    awg_runtime_json TEXT NOT NULL DEFAULT '{}',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cluster_nodes_local
ON cluster_nodes(is_local) WHERE is_local=1;
CREATE INDEX IF NOT EXISTS idx_cluster_nodes_state
ON cluster_nodes(state, last_seen_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cluster_nodes_slot
ON cluster_nodes(node_slot) WHERE node_slot IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_cluster_nodes_vpn_network
ON cluster_nodes(vpn_network) WHERE vpn_network <> '';

CREATE TABLE IF NOT EXISTS cluster_pool_slots (
    slot INTEGER PRIMARY KEY CHECK (slot BETWEEN 0 AND 12),
    vpn_network TEXT NOT NULL UNIQUE,
    node_id INTEGER UNIQUE REFERENCES cluster_nodes(id) ON DELETE SET NULL,
    allocated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retired_at TEXT
);

CREATE TABLE IF NOT EXISTS node_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES cluster_nodes(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('refresh', 'diagnostics', 'restart_awg', 'restart_traffic', 'restart_nginx', 'apply_awg_config')),
    payload_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'claimed', 'success', 'error', 'cancelled')),
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    claimed_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_node_jobs_queue
ON node_jobs(node_id, state, id);


CREATE TABLE IF NOT EXISTS cascade_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_node_id INTEGER NOT NULL REFERENCES cluster_nodes(id) ON DELETE RESTRICT,
    exit_node_id INTEGER NOT NULL REFERENCES cluster_nodes(id) ON DELETE RESTRICT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    state TEXT NOT NULL DEFAULT 'preparing_exit' CHECK (state IN (
        'preparing_exit', 'preparing_entry', 'active', 'disabling_entry',
        'disabling_exit', 'disabled', 'error'
    )),
    service_client_id INTEGER REFERENCES awg_clients(id) ON DELETE SET NULL,
    outbound_id INTEGER REFERENCES outbounds(id) ON DELETE SET NULL,
    entry_job_id INTEGER REFERENCES node_jobs(id) ON DELETE SET NULL,
    exit_job_id INTEGER REFERENCES node_jobs(id) ON DELETE SET NULL,
    last_test_at TEXT,
    last_exit_ip TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (entry_node_id <> exit_node_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cascade_links_entry_active
ON cascade_links(entry_node_id) WHERE enabled=1 AND state<>'disabled';
CREATE INDEX IF NOT EXISTS idx_cascade_links_exit
ON cascade_links(exit_node_id, enabled, state);

CREATE TABLE IF NOT EXISTS cascade_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    outbound_id INTEGER REFERENCES outbounds(id) ON DELETE SET NULL,
    exit_name TEXT NOT NULL DEFAULT '',
    exit_host TEXT NOT NULL DEFAULT '',
    exit_country_code TEXT NOT NULL DEFAULT '',
    last_state TEXT NOT NULL DEFAULT 'not_configured',
    last_test_at TEXT,
    last_exit_ip TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    last_client_test_at TEXT,
    last_client_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
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


def _migrate_awg_clients_v202(con: sqlite3.Connection) -> None:
    """Add cluster ownership and remove the obsolete global address uniqueness.

    Different SG-Node servers can legitimately use the same private client
    address because each server has its own awg0 interface and public IP.
    """
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='awg_clients'"
    ).fetchone()
    table_sql = str(row[0] or "") if row else ""
    columns = _columns(con, "awg_clients")
    needs_rebuild = "address TEXT NOT NULL UNIQUE" in table_sql
    required = {"node_id", "deployment_state", "deployment_job_id", "deployment_error", "deployed_enabled"}
    if not needs_rebuild and required <= columns:
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_awg_clients_node "
            "ON awg_clients(node_id, deployment_state, id)"
        )
        return

    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("ALTER TABLE awg_clients RENAME TO awg_clients_legacy_v202")
    con.execute(
        """
        CREATE TABLE awg_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            address TEXT NOT NULL,
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
            system_role TEXT NOT NULL DEFAULT '',
            node_id INTEGER REFERENCES cluster_nodes(id) ON DELETE RESTRICT,
            deployment_state TEXT NOT NULL DEFAULT 'active'
                CHECK (deployment_state IN ('queued', 'active', 'error', 'deleting')),
            deployment_job_id INTEGER,
            deployment_error TEXT NOT NULL DEFAULT '',
            deployed_enabled INTEGER NOT NULL DEFAULT 1 CHECK (deployed_enabled IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    legacy_columns = _columns(con, "awg_clients_legacy_v202")
    target_columns = [
        "id", "name", "enabled", "address", "private_key", "public_key",
        "preshared_key", "comment", "allowed_ips", "dns_servers", "mtu",
        "access_token", "access_enabled", "access_downloads", "access_last_at",
        "excluded_ips", "advertised_networks", "include_server_lan", "egress_mode",
        "outbound_id", "expires_at", "system_role", "node_id", "deployment_state",
        "deployment_job_id", "deployment_error", "deployed_enabled", "created_at", "updated_at",
    ]
    select_parts = []
    defaults = {
        "enabled": "1",
        "comment": "''",
        "allowed_ips": "'0.0.0.0/0'",
        "dns_servers": "''",
        "mtu": "NULL",
        "access_token": "''",
        "access_enabled": "1",
        "access_downloads": "0",
        "access_last_at": "NULL",
        "excluded_ips": "''",
        "advertised_networks": "''",
        "include_server_lan": "0",
        "egress_mode": "'awg_gateway'",
        "outbound_id": "NULL",
        "expires_at": "NULL",
        "system_role": "''",
        "node_id": "NULL",
        "deployment_state": "'active'",
        "deployment_job_id": "NULL",
        "deployment_error": "''",
        "deployed_enabled": "1",
        "created_at": "CURRENT_TIMESTAMP",
        "updated_at": "CURRENT_TIMESTAMP",
    }
    for name in target_columns:
        select_parts.append(name if name in legacy_columns else defaults.get(name, "NULL"))
    con.execute(
        f"INSERT INTO awg_clients ({', '.join(target_columns)}) "
        f"SELECT {', '.join(select_parts)} FROM awg_clients_legacy_v202"
    )
    con.execute("DROP TABLE awg_clients_legacy_v202")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_awg_clients_node "
        "ON awg_clients(node_id, deployment_state, id)"
    )
    con.execute("PRAGMA foreign_keys=ON")


def _migrate(con: sqlite3.Connection) -> None:
    _migrate_awg_clients_v202(con)

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
    if "instance_name" not in panel_columns:
        con.execute(
            "ALTER TABLE panel_settings ADD COLUMN instance_name TEXT NOT NULL DEFAULT 'SG-AWG-Panel'"
        )
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

    cascade_columns = _columns(con, "cascade_settings")
    if "exit_country_code" not in cascade_columns:
        con.execute(
            "ALTER TABLE cascade_settings ADD COLUMN exit_country_code TEXT NOT NULL DEFAULT ''"
        )
    if "last_client_test_at" not in cascade_columns:
        con.execute(
            "ALTER TABLE cascade_settings ADD COLUMN last_client_test_at TEXT"
        )
    if "last_client_error" not in cascade_columns:
        con.execute(
            "ALTER TABLE cascade_settings ADD COLUMN last_client_error TEXT NOT NULL DEFAULT ''"
        )

    node_columns = _columns(con, "cluster_nodes")
    if "node_slot" not in node_columns:
        con.execute("ALTER TABLE cluster_nodes ADD COLUMN node_slot INTEGER")
    if "vpn_network" not in node_columns:
        con.execute("ALTER TABLE cluster_nodes ADD COLUMN vpn_network TEXT NOT NULL DEFAULT ''")
    if "awg_runtime_json" not in node_columns:
        con.execute("ALTER TABLE cluster_nodes ADD COLUMN awg_runtime_json TEXT NOT NULL DEFAULT '{}'")
    if "country_code" not in node_columns:
        con.execute("ALTER TABLE cluster_nodes ADD COLUMN country_code TEXT NOT NULL DEFAULT ''")
    if "country_mode" not in node_columns:
        con.execute("ALTER TABLE cluster_nodes ADD COLUMN country_mode TEXT NOT NULL DEFAULT 'auto'")
    if "country_updated_at" not in node_columns:
        con.execute("ALTER TABLE cluster_nodes ADD COLUMN country_updated_at TEXT")
    if "machine_id" not in node_columns:
        con.execute("ALTER TABLE cluster_nodes ADD COLUMN machine_id TEXT NOT NULL DEFAULT ''")
    con.execute("UPDATE cluster_nodes SET public_port=585 WHERE is_local=0 AND public_port<>585")

    # RC5: Controller and up to twelve SG-Node servers receive permanent,
    # non-overlapping /24 pools. A retired slot remains reserved so a future
    # node never silently inherits addresses from a removed server.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS cluster_pool_slots (
            slot INTEGER PRIMARY KEY CHECK (slot BETWEEN 0 AND 12),
            vpn_network TEXT NOT NULL UNIQUE,
            node_id INTEGER UNIQUE REFERENCES cluster_nodes(id) ON DELETE SET NULL,
            allocated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            retired_at TEXT
        )
        """
    )
    local = con.execute(
        "SELECT id FROM cluster_nodes WHERE is_local=1 ORDER BY id LIMIT 1"
    ).fetchone()
    local_slot = 0
    local_network = "10.77.0.0/24"
    agent_env = Path(os.environ.get("SG_AWG_NODE_ENV", "/etc/sg-awg-node/agent.env"))
    if agent_env.is_file():
        configured_network_row = con.execute(
            "SELECT server_network FROM awg_settings WHERE id=1"
        ).fetchone()
        configured_network = str(configured_network_row["server_network"] or "") if configured_network_row else ""
        try:
            parsed_network = ipaddress.ip_network(configured_network, strict=True)
            candidate = int(str(parsed_network.network_address).split(".")[2])
            if parsed_network.prefixlen == 24 and 1 <= candidate <= 12 and configured_network == f"10.77.{candidate}.0/24":
                local_slot = candidate
                local_network = configured_network
        except ValueError:
            pass
    if local is not None:
        local_id = int(local["id"])
        con.execute("DELETE FROM cluster_pool_slots WHERE node_id=?", (local_id,))
        con.execute(
            "UPDATE cluster_nodes SET node_slot=?, vpn_network=? WHERE id=?",
            (local_slot, local_network, local_id),
        )
        con.execute(
            "INSERT INTO cluster_pool_slots(slot,vpn_network,node_id,retired_at) "
            "VALUES(?,?,?,NULL) "
            "ON CONFLICT(slot) DO UPDATE SET vpn_network=excluded.vpn_network, "
            "node_id=excluded.node_id, retired_at=NULL",
            (local_slot, local_network, local_id),
        )

    # Keep valid existing assignments; allocate only slots that have never
    # appeared in cluster_pool_slots. This intentionally does not reuse retired
    # slots without a future explicit administrator action.
    for row in con.execute(
        "SELECT id,node_slot,vpn_network FROM cluster_nodes WHERE is_local=0 ORDER BY id"
    ).fetchall():
        node_id = int(row["id"])
        try:
            slot = int(row["node_slot"] or 0)
        except (TypeError, ValueError):
            slot = 0
        expected = f"10.77.{slot}.0/24" if 1 <= slot <= 12 else ""
        conflict = None
        if expected:
            conflict = con.execute(
                "SELECT node_id FROM cluster_pool_slots WHERE slot=? AND node_id<>?",
                (slot, node_id),
            ).fetchone()
        if not expected or conflict is not None:
            slot = 0
            expected = ""
            for candidate in range(1, 13):
                if con.execute(
                    "SELECT 1 FROM cluster_pool_slots WHERE slot=?", (candidate,)
                ).fetchone() is None:
                    slot = candidate
                    expected = f"10.77.{candidate}.0/24"
                    break
        if slot:
            con.execute(
                "UPDATE cluster_nodes SET node_slot=?, vpn_network=? WHERE id=?",
                (slot, expected, node_id),
            )
            con.execute(
                "INSERT INTO cluster_pool_slots(slot,vpn_network,node_id,retired_at) "
                "VALUES(?,?,?,NULL) "
                "ON CONFLICT(slot) DO UPDATE SET node_id=excluded.node_id, "
                "vpn_network=excluded.vpn_network, retired_at=NULL",
                (slot, expected, node_id),
            )

    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cluster_nodes_slot "
        "ON cluster_nodes(node_slot) WHERE node_slot IS NOT NULL"
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cluster_nodes_vpn_network "
        "ON cluster_nodes(vpn_network) WHERE vpn_network<>''"
    )
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='node_profiles'").fetchone():
        con.execute("DROP TABLE node_profiles")
    con.execute(
        "UPDATE node_jobs SET state='cancelled', result_json=? "
        "WHERE kind='apply_awg_config' AND state IN ('queued','claimed') "
        "AND COALESCE(json_extract(payload_json, '$.mode'), '') <> 'sync_clients'",
        ('{"message":"Устаревшее задание отменено при переходе на единый раздел Clients"}',),
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
        "system_role": "TEXT NOT NULL DEFAULT ''",
        "node_id": "INTEGER",
        "deployment_state": "TEXT NOT NULL DEFAULT 'active'",
        "deployment_job_id": "INTEGER",
        "deployment_error": "TEXT NOT NULL DEFAULT ''",
        "deployed_enabled": "INTEGER NOT NULL DEFAULT 1",
    }
    for name, definition in migrations.items():
        if name not in client_columns:
            con.execute(f"ALTER TABLE awg_clients ADD COLUMN {name} {definition}")

    # Normalize every ordinary client into the pool permanently assigned to its
    # server before enforcing uniqueness. Service peers used by Cascade keep
    # their explicit tunnel addresses. Preserve a valid host number when
    # possible; otherwise allocate the first free .2-.254 address.
    servers = [(None, local_network)]
    servers.extend(
        (int(row["id"]), str(row["vpn_network"]))
        for row in con.execute(
            "SELECT id,vpn_network FROM cluster_nodes "
            "WHERE is_local=0 AND vpn_network<>'' ORDER BY node_slot,id"
        ).fetchall()
    )
    for server_id, network_text in servers:
        network = ipaddress.ip_network(network_text, strict=True)
        if server_id is None:
            rows = con.execute(
                "SELECT id,address FROM awg_clients "
                "WHERE node_id IS NULL AND COALESCE(system_role,'')='' "
                "AND deployment_state<>'deleting' ORDER BY id"
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id,address FROM awg_clients "
                "WHERE node_id=? AND COALESCE(system_role,'')='' "
                "AND deployment_state<>'deleting' ORDER BY id",
                (server_id,),
            ).fetchall()
        used: set[int] = set()
        for row in rows:
            preferred = 0
            try:
                preferred = int(ipaddress.ip_interface(str(row["address"])).ip) & 0xFF
            except ValueError:
                pass
            host = preferred if 2 <= preferred <= 254 and preferred not in used else 0
            if not host:
                host = next((candidate for candidate in range(2, 255) if candidate not in used), 0)
            if not host:
                raise sqlite3.IntegrityError(f"VPN-пул {network_text} исчерпан")
            used.add(host)
            desired = f"{network.network_address + host}/32"
            if str(row["address"]) != desired:
                con.execute(
                    "UPDATE awg_clients SET address=?, deployment_state=CASE "
                    "WHEN node_id IS NULL THEN deployment_state ELSE 'queued' END, "
                    "deployment_error='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (desired, int(row["id"])),
                )

    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_awg_clients_server_address "
        "ON awg_clients(COALESCE(node_id,0), address) WHERE deployment_state<>'deleting'"
    )

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
            INSERT OR IGNORE INTO cascade_settings (
                id, enabled, outbound_id, exit_name, exit_host, last_state
            ) VALUES (1, 0, NULL, '', '', 'not_configured')
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO panel_settings (
                id, instance_name, public_scheme, public_host, public_port,
                backend_address, backend_port, https_enabled, manage_placeholder,
                backup_schedule, backup_keep, update_channel, auth_epoch,
                access_enabled, access_profile_title
            ) VALUES (1, 'SG-AWG-Panel', 'http', '', 62443, '127.0.0.1', 18080,
                      0, 1, 'daily', 20, 'prerelease', 1, 1, 'SG-AWG')
            """
        )
