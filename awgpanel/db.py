from __future__ import annotations

import os
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)
        con.execute(
            """
            INSERT OR IGNORE INTO awg_settings (
                id, configured, interface_name, endpoint_host, listen_port,
                server_network, dns_servers, mtu, external_interface
            ) VALUES (1, 0, 'awg0', '', 585, '10.77.0.0/24',
                      '1.1.1.1, 1.0.0.1', 1280, '')
            """
        )
