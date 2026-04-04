"""Database connection and parameterized query helpers.

Supports MariaDB (primary) with SQLite fallback.
All queries use parameterized placeholders to prevent SQL injection.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Generator

from flask import Flask, g

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MariaDB optional import
# ---------------------------------------------------------------------------
try:
    import mariadb

    _HAS_MARIADB = True
except ImportError:
    _HAS_MARIADB = False

# ---------------------------------------------------------------------------
# Schema DDL (compatible with both SQLite and MariaDB)
# ---------------------------------------------------------------------------
_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seal_id     TEXT    NOT NULL UNIQUE,
    case_number TEXT    NOT NULL,
    investigator TEXT   NOT NULL,
    suspect_name TEXT   NOT NULL,
    suspect_email TEXT  NOT NULL DEFAULT '',
    suspect_birth TEXT  NOT NULL DEFAULT '',
    suspect_phone TEXT  NOT NULL DEFAULT '',
    auth_level  TEXT    NOT NULL DEFAULT 'basic',
    password_hash TEXT  NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seal_id     TEXT    NOT NULL,
    role        TEXT    NOT NULL CHECK(role IN ('suspect','investigator','admin')),
    name        TEXT    NOT NULL,
    email       TEXT    NOT NULL DEFAULT '',
    birth_date  TEXT    NOT NULL DEFAULT '',
    phone       TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (seal_id) REFERENCES cases(seal_id)
);

CREATE TABLE IF NOT EXISTS key_shares (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seal_id     TEXT    NOT NULL,
    share_index INTEGER NOT NULL CHECK(share_index BETWEEN 1 AND 4),
    share_data  TEXT    NOT NULL,
    uploaded_by TEXT    NOT NULL,
    uploaded_at TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (seal_id) REFERENCES cases(seal_id),
    UNIQUE(seal_id, share_index)
);

CREATE TABLE IF NOT EXISTS seal_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seal_id     TEXT    NOT NULL,
    event_id    INTEGER NOT NULL,
    event_type  TEXT    NOT NULL CHECK(event_type IN ('Sealing','Unsealing','Resealing')),
    record_json TEXT    NOT NULL,
    record_pdf  BLOB,
    synced_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (seal_id) REFERENCES cases(seal_id),
    UNIQUE(seal_id, event_id)
);

CREATE TABLE IF NOT EXISTS auth_failures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seal_id     TEXT    NOT NULL,
    ip_address  TEXT    NOT NULL DEFAULT '',
    failed_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_MARIADB_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    seal_id      VARCHAR(64)  NOT NULL UNIQUE,
    case_number  VARCHAR(128) NOT NULL,
    investigator VARCHAR(128) NOT NULL,
    suspect_name VARCHAR(128) NOT NULL,
    suspect_email VARCHAR(256) NOT NULL DEFAULT '',
    suspect_birth VARCHAR(16)  NOT NULL DEFAULT '',
    suspect_phone VARCHAR(32)  NOT NULL DEFAULT '',
    auth_level   VARCHAR(32)  NOT NULL DEFAULT 'basic',
    password_hash VARCHAR(256) NOT NULL DEFAULT '',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS users (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    seal_id    VARCHAR(64)  NOT NULL,
    role       ENUM('suspect','investigator','admin') NOT NULL,
    name       VARCHAR(128) NOT NULL,
    email      VARCHAR(256) NOT NULL DEFAULT '',
    birth_date VARCHAR(16)  NOT NULL DEFAULT '',
    phone      VARCHAR(32)  NOT NULL DEFAULT '',
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (seal_id) REFERENCES cases(seal_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS key_shares (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    seal_id      VARCHAR(64) NOT NULL,
    share_index  TINYINT     NOT NULL,
    share_data   TEXT        NOT NULL,
    uploaded_by  VARCHAR(128) NOT NULL,
    uploaded_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (seal_id) REFERENCES cases(seal_id),
    UNIQUE KEY uq_seal_share (seal_id, share_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS seal_records (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    seal_id      VARCHAR(64) NOT NULL,
    event_id     INT         NOT NULL,
    event_type   ENUM('Sealing','Unsealing','Resealing') NOT NULL,
    record_json  LONGTEXT    NOT NULL,
    record_pdf   LONGBLOB,
    synced_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (seal_id) REFERENCES cases(seal_id),
    UNIQUE KEY uq_seal_event (seal_id, event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS auth_failures (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    seal_id    VARCHAR(64) NOT NULL,
    ip_address VARCHAR(45) NOT NULL DEFAULT '',
    failed_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _connect_mariadb(app: Flask) -> mariadb.Connection:
    """Create a MariaDB connection from app config."""
    if not _HAS_MARIADB:
        raise RuntimeError("mariadb package is not installed")

    conn = mariadb.connect(
        host=app.config["DB_HOST"],
        port=app.config["DB_PORT"],
        user=app.config["DB_USER"],
        password=app.config["DB_PASSWORD"],
        database=app.config["DB_NAME"],
        pool_size=app.config.get("DB_POOL_SIZE", 5),
    )
    return conn


def _connect_sqlite(app: Flask) -> sqlite3.Connection:
    """Create a SQLite connection from app config."""
    import os

    db_path = app.config["SQLITE_PATH"]
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db() -> Any:
    """Get or create a database connection for the current request.

    Returns:
        A database connection (MariaDB or SQLite).
    """
    from flask import current_app

    if "db" not in g:
        use_sqlite = current_app.config.get("USE_SQLITE", False)
        if use_sqlite or not _HAS_MARIADB:
            if not use_sqlite:
                logger.warning(
                    "MariaDB driver not installed, falling back to SQLite"
                )
            g.db = _connect_sqlite(current_app)
            g.db_type = "sqlite"
        else:
            try:
                g.db = _connect_mariadb(current_app)
                g.db_type = "mariadb"
            except Exception:
                logger.warning(
                    "MariaDB connection failed, falling back to SQLite"
                )
                g.db = _connect_sqlite(current_app)
                g.db_type = "sqlite"
    return g.db


def close_db(exc: BaseException | None = None) -> None:
    """Close the database connection at the end of the request."""
    db = g.pop("db", None)
    g.pop("db_type", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


def init_db(app: Flask) -> None:
    """Initialize database tables.

    Args:
        app: The Flask application instance.
    """
    with app.app_context():
        db = get_db()
        db_type = g.get("db_type", "sqlite")

        if db_type == "sqlite":
            db.executescript(_SQLITE_SCHEMA)
        else:
            cursor = db.cursor()
            for statement in _MARIADB_SCHEMA.strip().split(";"):
                stmt = statement.strip()
                if stmt:
                    cursor.execute(stmt)
            db.commit()
            cursor.close()

        close_db()


# ---------------------------------------------------------------------------
# Query helpers (parameterized queries only)
# ---------------------------------------------------------------------------

def execute_query(
    sql: str,
    params: tuple[Any, ...] = (),
    *,
    fetch_one: bool = False,
    fetch_all: bool = False,
) -> Any:
    """Execute a parameterized SQL query.

    Args:
        sql: SQL statement with ? placeholders (SQLite) or %s (MariaDB).
        params: Query parameters.
        fetch_one: Return a single row.
        fetch_all: Return all rows.

    Returns:
        Query result or None.
    """
    db = get_db()
    db_type = g.get("db_type", "sqlite")

    # Normalize placeholders: internal code uses ? (SQLite style)
    # MariaDB uses %s
    if db_type == "mariadb":
        sql = sql.replace("?", "%s")

    cursor = db.cursor()
    try:
        cursor.execute(sql, params)

        if fetch_one:
            return cursor.fetchone()
        if fetch_all:
            return cursor.fetchall()

        db.commit()
        return cursor.lastrowid
    finally:
        cursor.close()


def insert_case(
    seal_id: str,
    case_number: str,
    investigator: str,
    suspect_name: str,
    suspect_email: str = "",
    suspect_birth: str = "",
    suspect_phone: str = "",
    auth_level: str = "basic",
    password_hash: str = "",
) -> int | None:
    """Insert a new case record.

    Returns:
        The inserted row ID, or None on failure.
    """
    return execute_query(
        """INSERT INTO cases
           (seal_id, case_number, investigator, suspect_name,
            suspect_email, suspect_birth, suspect_phone,
            auth_level, password_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            seal_id, case_number, investigator, suspect_name,
            suspect_email, suspect_birth, suspect_phone,
            auth_level, password_hash,
        ),
    )


def find_case_by_seal_id(seal_id: str) -> Any:
    """Find a case by seal_id.

    Returns:
        Row dict/tuple or None.
    """
    return execute_query(
        "SELECT * FROM cases WHERE seal_id = ?",
        (seal_id,),
        fetch_one=True,
    )


def insert_key_share(
    seal_id: str,
    share_index: int,
    share_data: str,
    uploaded_by: str,
) -> int | None:
    """Insert or ignore a key share.

    Returns:
        The inserted row ID, or None if duplicate.
    """
    db = get_db()
    db_type = g.get("db_type", "sqlite")

    if db_type == "sqlite":
        sql = """INSERT OR IGNORE INTO key_shares
                 (seal_id, share_index, share_data, uploaded_by)
                 VALUES (?, ?, ?, ?)"""
    else:
        sql = """INSERT IGNORE INTO key_shares
                 (seal_id, share_index, share_data, uploaded_by)
                 VALUES (%s, %s, %s, %s)"""

    cursor = db.cursor()
    try:
        cursor.execute(sql, (seal_id, share_index, share_data, uploaded_by))
        db.commit()
        return cursor.lastrowid
    finally:
        cursor.close()


def find_key_shares_by_seal_id(seal_id: str) -> list[Any]:
    """Find all key shares for a given seal_id.

    Returns:
        List of row dicts/tuples.
    """
    return execute_query(
        "SELECT * FROM key_shares WHERE seal_id = ? ORDER BY share_index",
        (seal_id,),
        fetch_all=True,
    ) or []


def insert_seal_record(
    seal_id: str,
    event_id: int,
    event_type: str,
    record_json: str,
    record_pdf: bytes | None = None,
) -> int | None:
    """Insert a seal record (idempotent: ignores duplicates).

    Returns:
        The inserted row ID, or None if duplicate.
    """
    db = get_db()
    db_type = g.get("db_type", "sqlite")

    if db_type == "sqlite":
        sql = """INSERT OR IGNORE INTO seal_records
                 (seal_id, event_id, event_type, record_json, record_pdf)
                 VALUES (?, ?, ?, ?, ?)"""
    else:
        sql = """INSERT IGNORE INTO seal_records
                 (seal_id, event_id, event_type, record_json, record_pdf)
                 VALUES (%s, %s, %s, %s, %s)"""

    cursor = db.cursor()
    try:
        cursor.execute(sql, (seal_id, event_id, event_type, record_json, record_pdf))
        db.commit()
        return cursor.lastrowid
    finally:
        cursor.close()


def find_seal_records_by_seal_id(seal_id: str) -> list[Any]:
    """Find all seal records for a given seal_id ordered by event_id.

    Returns:
        List of row dicts/tuples.
    """
    return execute_query(
        "SELECT * FROM seal_records WHERE seal_id = ? ORDER BY event_id",
        (seal_id,),
        fetch_all=True,
    ) or []


def record_auth_failure(seal_id: str, ip_address: str) -> None:
    """Record an authentication failure."""
    execute_query(
        "INSERT INTO auth_failures (seal_id, ip_address) VALUES (?, ?)",
        (seal_id, ip_address),
    )


def count_recent_auth_failures(
    seal_id: str,
    ip_address: str,
    window_seconds: int = 600,
) -> int:
    """Count authentication failures within the given time window.

    Args:
        seal_id: The seal identifier.
        ip_address: Client IP address.
        window_seconds: Lookback window in seconds.

    Returns:
        Number of recent failures.
    """
    db = get_db()
    db_type = g.get("db_type", "sqlite")

    if db_type == "sqlite":
        sql = """SELECT COUNT(*) FROM auth_failures
                 WHERE seal_id = ? AND ip_address = ?
                 AND failed_at > datetime('now', ?)"""
        params = (seal_id, ip_address, f"-{window_seconds} seconds")
    else:
        sql = """SELECT COUNT(*) FROM auth_failures
                 WHERE seal_id = %s AND ip_address = %s
                 AND failed_at > DATE_SUB(NOW(), INTERVAL %s SECOND)"""
        params = (seal_id, ip_address, window_seconds)

    cursor = db.cursor()
    try:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            return 0
        return row[0] if isinstance(row, (tuple, list)) else row["COUNT(*)"]
    finally:
        cursor.close()
