from __future__ import annotations

import os
import secrets
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("AWGPANEL_DB", "/var/lib/sg-awg-panel/panel.db"))

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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS panel_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    public_scheme TEXT NOT NULL DEFAULT 'http' CHECK (public_scheme IN ('http', 'https')),
    public_host TEXT NOT NULL DEFAULT '',
    public_port INTEGER NOT NULL DEFAULT 8080 CHECK (public_port BETWEEN 1 AND 65535),
    backend_address TEXT NOT NULL DEFAULT '127.0.0.1',
    backend_port INTEGER NOT NULL DEFAULT 18080 CHECK (backend_port BETWEEN 1 AND 65535),
    https_email TEXT NOT NULL DEFAULT '',
    https_enabled INTEGER NOT NULL DEFAULT 0 CHECK (https_enabled IN (0, 1)),
    ip_allowlist TEXT NOT NULL DEFAULT '',
    backup_schedule TEXT NOT NULL DEFAULT 'daily',
    backup_keep INTEGER NOT NULL DEFAULT 20 CHECK (backup_keep BETWEEN 1 AND 365),
    update_channel TEXT NOT NULL DEFAULT 'prerelease' CHECK (update_channel IN ('prerelease', 'stable')),
    latest_version TEXT NOT NULL DEFAULT '',
    latest_checked_at TEXT,
    latest_error TEXT NOT NULL DEFAULT '',
    auth_epoch INTEGER NOT NULL DEFAULT 1,
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


def _migrate(con: sqlite3.Connection) -> None:
    settings_columns = _columns(con, "awg_settings")
    if "isolate_clients" not in settings_columns:
        con.execute(
            "ALTER TABLE awg_settings ADD COLUMN isolate_clients INTEGER NOT NULL DEFAULT 1"
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
    }
    for name, definition in migrations.items():
        if name not in client_columns:
            con.execute(f"ALTER TABLE awg_clients ADD COLUMN {name} {definition}")

    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_awg_clients_access_token "
        "ON awg_clients(access_token) WHERE access_token <> ''"
    )

    rows = con.execute(
        "SELECT id FROM awg_clients WHERE access_token='' OR access_token IS NULL"
    ).fetchall()
    for row in rows:
        con.execute(
            "UPDATE awg_clients SET access_token=? WHERE id=?",
            (secrets.token_urlsafe(24), int(row["id"])),
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
            INSERT OR IGNORE INTO panel_settings (
                id, public_scheme, public_host, public_port,
                backend_address, backend_port, https_enabled,
                backup_schedule, backup_keep, update_channel, auth_epoch
            ) VALUES (1, 'http', '', 8080, '127.0.0.1', 18080,
                      0, 'daily', 20, 'prerelease', 1)
            """
        )
