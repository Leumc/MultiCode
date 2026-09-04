import sqlite3
import uuid

from remote_dev.database import Database


LEGACY_USERS = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK(role IN ('admin','developer')),
    password_hash TEXT NOT NULL,
    api_token_hash TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
    workspace_path TEXT,
    workspace_quota_mib INTEGER NOT NULL DEFAULT 1024,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
"""


def test_initialize_backfills_stable_public_id_for_legacy_users(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(LEGACY_USERS)
        connection.execute(
            """INSERT INTO users
               (username,role,password_hash,status,workspace_quota_mib,created_at)
               VALUES('admin','admin','legacy-hash','active',1024,'2026-01-01T00:00:00Z')"""
        )

    database = Database(path)
    database.initialize()
    first = database.get_user_by_username("admin")["public_id"]
    assert uuid.UUID(first).version == 4

    database.initialize()
    assert database.get_user_by_username("admin")["public_id"] == first
