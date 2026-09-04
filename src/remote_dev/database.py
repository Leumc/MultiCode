"""SQLite persistence for users, sessions, jobs, grants and audit events."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_WORKSPACE_QUOTA_MIB,
    MAX_USER_INFLIGHT,
    MAX_USER_RUNNING_MEMORY_MIB,
)
from .security import hash_password, token_hash

DEFAULT_HEADERS = {
    "algorithm", "array", "bitset", "cassert", "cctype", "cerrno", "cfenv",
    "cfloat", "chrono", "cinttypes", "climits", "cmath", "complex", "concepts",
    "condition_variable", "csetjmp", "csignal", "cstdarg", "cstddef", "cstdint",
    "cstdio", "cstdlib", "cstring", "ctime", "deque", "exception", "filesystem",
    "forward_list", "fstream", "functional", "future", "initializer_list", "iomanip",
    "ios", "iosfwd", "iostream", "istream", "iterator", "limits", "list", "map",
    "memory", "mutex", "new", "numeric", "optional", "ostream", "queue", "random",
    "ranges", "ratio", "regex", "set", "shared_mutex", "span", "sstream", "stack",
    "stdexcept", "streambuf", "string", "string_view", "tuple", "type_traits",
    "typeindex", "typeinfo", "unordered_map", "unordered_set", "utility", "valarray",
    "variant", "vector", "bits/stdc++.h",
}

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
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
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    csrf_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS header_policy (
    name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    builtin INTEGER NOT NULL DEFAULT 1 CHECK(builtin IN (0,1)),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    filename TEXT NOT NULL,
    source TEXT NOT NULL,
    compiler TEXT NOT NULL,
    time_limit_ms INTEGER NOT NULL,
    memory_limit_mib INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
    compile_status TEXT,
    compile_stdout TEXT NOT NULL DEFAULT '',
    compile_stderr TEXT NOT NULL DEFAULT '',
    compile_exit_code INTEGER,
    compile_wall_time_ms INTEGER,
    grant_id INTEGER REFERENCES quota_grants(id)
);
CREATE TABLE IF NOT EXISTS job_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    input_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    wall_time_ms INTEGER,
    peak_memory_kib INTEGER,
    exit_code INTEGER,
    term_signal INTEGER,
    output_truncated INTEGER NOT NULL DEFAULT 0,
    UNIQUE(job_id, position)
);
CREATE TABLE IF NOT EXISTS quota_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_limit_mib INTEGER,
    time_limit_ms INTEGER,
    max_inflight INTEGER,
    remaining_uses INTEGER,
    expires_at TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS extension_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    extension_id TEXT NOT NULL,
    version TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK(length(sha256)=64),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    created_at TEXT NOT NULL,
    UNIQUE(extension_id,version)
);
CREATE TABLE IF NOT EXISTS extension_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    catalog_id INTEGER NOT NULL REFERENCES extension_catalog(id),
    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','revoked')),
    scope TEXT CHECK(scope IN ('user','global')),
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by INTEGER REFERENCES users(id),
    UNIQUE(user_id,catalog_id,status)
);
CREATE TABLE IF NOT EXISTS login_failures (
    scope TEXT NOT NULL,
    username TEXT NOT NULL,
    source TEXT NOT NULL,
    failures INTEGER NOT NULL,
    first_failed_at TEXT NOT NULL,
    blocked_until TEXT,
    PRIMARY KEY(scope,username,source)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_users_api_token ON users(api_token_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_user_status ON jobs(user_id,status);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
"""


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=8000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_user_public_ids(connection)
            stamp = iso()
            connection.executemany(
                "INSERT OR IGNORE INTO header_policy(name,enabled,builtin,updated_at) VALUES(?,1,1,?)",
                [(name, stamp) for name in sorted(DEFAULT_HEADERS)],
            )

    @staticmethod
    def _migrate_user_public_ids(connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "public_id" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN public_id TEXT")
        rows = connection.execute(
            "SELECT id FROM users WHERE public_id IS NULL OR public_id=''"
        ).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE users SET public_id=? WHERE id=?", (str(uuid.uuid4()), row["id"])
            )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_public_id ON users(public_id)"
        )

    def create_admin(self, username: str, password: str) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO users(public_id,username,role,password_hash,created_at) VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), username, "admin", hash_password(password), iso()),
            )
        return self.get_user_by_username(username)

    def create_developer(
        self, username: str, password: str, api_token: str, workspace_path: str,
        public_id: str | None = None,
    ) -> dict[str, Any]:
        public_id = public_id or str(uuid.uuid4())
        try:
            if str(uuid.UUID(public_id)) != public_id:
                raise ValueError
        except (ValueError, AttributeError, TypeError):
            raise ValueError("public_id must be a canonical lowercase UUID") from None
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO users
                   (public_id,username,role,password_hash,api_token_hash,workspace_path,
                    workspace_quota_mib,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    public_id, username, "developer", hash_password(password),
                    token_hash(api_token), workspace_path, DEFAULT_WORKSPACE_QUOTA_MIB, iso(),
                ),
            )
            user_id = cursor.lastrowid
        return self.get_user(user_id)

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            return row_dict(connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def get_user_by_public_id(self, public_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return row_dict(connection.execute(
                "SELECT * FROM users WHERE public_id=?", (public_id,)
            ).fetchone())

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return row_dict(connection.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())

    def get_user_by_api_token(self, encoded: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return row_dict(connection.execute(
                "SELECT * FROM users WHERE api_token_hash=? AND status='active'", (token_hash(encoded),)
            ).fetchone())

    def list_developers(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT id,public_id,username,role,status,workspace_path,workspace_quota_mib,
                          created_at,last_login_at FROM users WHERE role='developer' ORDER BY id"""
            )]

    def update_developer(
        self, user_id: int, *, username: str | None = None,
        status: str | None = None, password: str | None = None,
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            user = connection.execute(
                "SELECT * FROM users WHERE id=? AND role='developer'", (user_id,)
            ).fetchone()
            if not user:
                return None
            if username is not None:
                connection.execute("UPDATE users SET username=? WHERE id=?", (username, user_id))
            if status is not None:
                connection.execute("UPDATE users SET status=? WHERE id=?", (status, user_id))
            if password is not None:
                connection.execute(
                    "UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), user_id)
                )
            if status == "disabled" or password is not None:
                connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return self.get_user(user_id)

    def rotate_developer_token(self, user_id: int, encoded: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            updated = connection.execute(
                "UPDATE users SET api_token_hash=? WHERE id=? AND role='developer'",
                (token_hash(encoded), user_id),
            ).rowcount
            if updated != 1:
                return None
        return self.get_user(user_id)

    def delete_unconfigured_developer(self, user_id: int, public_id: str) -> None:
        """Compensate a failed offline bootstrap before the instance is activated."""
        with self.connect() as connection:
            deleted = connection.execute(
                "DELETE FROM users WHERE id=? AND public_id=? AND role='developer'",
                (user_id, public_id),
            ).rowcount
            if deleted != 1:
                raise RuntimeError("unable to roll back failed developer bootstrap")

    def revoke_session_token(self, encoded: str) -> bool:
        with self.connect() as connection:
            return connection.execute(
                "DELETE FROM sessions WHERE token_hash=?", (token_hash(encoded),)
            ).rowcount == 1

    def revoke_user_sessions(self, user_id: int) -> int | None:
        with self.connect() as connection:
            user = connection.execute(
                "SELECT id FROM users WHERE id=? AND role='developer'", (user_id,)
            ).fetchone()
            if not user:
                return None
            return connection.execute(
                "DELETE FROM sessions WHERE user_id=?", (user_id,)
            ).rowcount

    def login_retry_after(self, scope: str, username: str, source: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT blocked_until FROM login_failures
                   WHERE scope=? AND username=? AND source=?""",
                (scope, username, source),
            ).fetchone()
        if not row or not row["blocked_until"]:
            return 0
        remaining = (datetime.fromisoformat(row["blocked_until"]) - utcnow()).total_seconds()
        return max(0, int(remaining) + (1 if remaining % 1 else 0))

    def record_login_failure(self, scope: str, username: str, source: str) -> None:
        now = utcnow()
        with self.connect() as connection:
            row = connection.execute(
                """SELECT failures,first_failed_at FROM login_failures
                   WHERE scope=? AND username=? AND source=?""",
                (scope, username, source),
            ).fetchone()
            if not row or now - datetime.fromisoformat(row["first_failed_at"]) >= timedelta(minutes=15):
                failures = 1
                first_failed_at = iso(now)
            else:
                failures = row["failures"] + 1
                first_failed_at = row["first_failed_at"]
            blocked_until = iso(now + timedelta(minutes=15)) if failures >= 5 else None
            connection.execute(
                """INSERT INTO login_failures
                   (scope,username,source,failures,first_failed_at,blocked_until)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(scope,username,source) DO UPDATE SET
                     failures=excluded.failures,
                     first_failed_at=excluded.first_failed_at,
                     blocked_until=excluded.blocked_until""",
                (scope, username, source, failures, first_failed_at, blocked_until),
            )

    def clear_login_failures(self, scope: str, username: str, source: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM login_failures WHERE scope=? AND username=? AND source=?",
                (scope, username, source),
            )

    def create_session(self, user_id: int, token: str, csrf: str, ttl_hours: int = 12) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO sessions(user_id,token_hash,csrf_token,expires_at,created_at) VALUES(?,?,?,?,?)",
                (user_id, token_hash(token), csrf, iso(utcnow() + timedelta(hours=ttl_hours)), iso()),
            )
            connection.execute("UPDATE users SET last_login_at=? WHERE id=?", (iso(), user_id))

    def get_session_user(self, token: str) -> tuple[dict[str, Any], str] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT u.*,s.csrf_token FROM sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.token_hash=? AND s.expires_at>? AND u.status='active'""",
                (token_hash(token), iso()),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        csrf = data.pop("csrf_token")
        return data, csrf

    def enabled_headers(self) -> set[str]:
        with self.connect() as connection:
            return {row[0] for row in connection.execute(
                "SELECT name FROM header_policy WHERE enabled=1"
            )}

    def list_headers(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [
                {"name": row["name"], "enabled": bool(row["enabled"]), "builtin": bool(row["builtin"])}
                for row in connection.execute("SELECT * FROM header_policy ORDER BY name")
            ]

    def set_header(self, name: str, enabled: bool) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO header_policy(name,enabled,builtin,updated_at) VALUES(?,?,0,?)
                   ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (name, int(enabled), iso()),
            )
        return {"name": name, "enabled": enabled}

    def create_job(self, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            inflight = connection.execute(
                """SELECT COUNT(*) FROM jobs WHERE user_id=? AND status IN
                   ('queued','compiling','running')""",
                (user_id,),
            ).fetchone()[0]
            max_inflight = self.effective_limits(user_id, connection)["max_inflight"]
            if inflight >= max_inflight:
                raise OverflowError("inflight_limit")
            grant_id = None
            if payload["memory_limit_mib"] > 256 or payload["time_limit_ms"] > 60_000:
                grant = connection.execute(
                    """SELECT * FROM quota_grants WHERE user_id=? AND active=1
                       AND (expires_at IS NULL OR expires_at>?)
                       AND (remaining_uses IS NULL OR remaining_uses>0)
                       AND (?<=256 OR memory_limit_mib>=?)
                       AND (?<=60000 OR time_limit_ms>=?)
                       ORDER BY (expires_at IS NULL),expires_at,id LIMIT 1""",
                    (
                        user_id, iso(), payload["memory_limit_mib"], payload["memory_limit_mib"],
                        payload["time_limit_ms"], payload["time_limit_ms"],
                    ),
                ).fetchone()
                if not grant:
                    raise ValueError("quota_exceeded")
                grant_id = grant["id"]
            cursor = connection.execute(
                """INSERT INTO jobs(user_id,filename,source,compiler,time_limit_ms,
                   memory_limit_mib,status,created_at,grant_id) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    user_id, payload["filename"], payload["source"], payload["compiler"],
                    payload["time_limit_ms"], payload["memory_limit_mib"], "queued", iso(), grant_id,
                ),
            )
            job_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO job_cases(job_id,position,input_text) VALUES(?,?,?)",
                [(job_id, index, value) for index, value in enumerate(payload["inputs"], 1)],
            )
        return self.get_job(job_id, user_id)

    def claim_next_job(self) -> dict[str, Any] | None:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """WITH eligible AS (
                       SELECT j.*,u.public_id AS owner_public_id,
                              (SELECT MAX(previous.started_at) FROM jobs previous
                               WHERE previous.user_id=j.user_id
                                 AND previous.started_at IS NOT NULL) AS last_claimed_at,
                              ROW_NUMBER() OVER (
                                  PARTITION BY j.user_id ORDER BY j.created_at,j.id
                              ) AS user_queue_position
                       FROM jobs j JOIN users u ON u.id=j.user_id
                       WHERE j.status='queued' AND j.cancel_requested=0
                         AND COALESCE((
                             SELECT SUM(active.memory_limit_mib) FROM jobs active
                             WHERE active.user_id=j.user_id
                               AND active.status IN ('compiling','running')
                         ),0) + j.memory_limit_mib <= COALESCE((
                             SELECT grant.memory_limit_mib FROM quota_grants grant
                             WHERE grant.id=j.grant_id AND grant.active=1
                               AND (grant.expires_at IS NULL OR grant.expires_at>?)
                               AND (grant.remaining_uses IS NULL OR grant.remaining_uses>0)
                         ),?)
                   )
                   SELECT * FROM eligible WHERE user_queue_position=1
                   ORDER BY (last_claimed_at IS NOT NULL),last_claimed_at,user_id
                   LIMIT 1""",
                (iso(), MAX_USER_RUNNING_MEMORY_MIB),
            ).fetchone()
            if not row:
                connection.rollback()
                return None
            if row["grant_id"] is not None:
                charged = connection.execute(
                    """UPDATE quota_grants SET
                         remaining_uses=CASE WHEN remaining_uses IS NULL THEN NULL ELSE remaining_uses-1 END
                       WHERE id=? AND active=1 AND (expires_at IS NULL OR expires_at>?)
                         AND (remaining_uses IS NULL OR remaining_uses>0)""",
                    (row["grant_id"], iso()),
                ).rowcount
                if charged != 1:
                    connection.execute(
                        """UPDATE jobs SET status='cancelled',finished_at=? WHERE id=?""",
                        (iso(), row["id"]),
                    )
                    connection.execute(
                        """UPDATE job_cases SET status='cancelled',stderr='temporary grant expired before execution'
                           WHERE job_id=?""",
                        (row["id"],),
                    )
                    connection.commit()
                    return None
            updated = connection.execute(
                "UPDATE jobs SET status='compiling',started_at=? WHERE id=? AND status='queued'",
                (iso(), row["id"]),
            ).rowcount
            if updated != 1:
                connection.rollback()
                return None
            cases = [dict(case) for case in connection.execute(
                "SELECT * FROM job_cases WHERE job_id=? ORDER BY position", (row["id"],)
            )]
            connection.commit()
            result = dict(row)
            result["status"] = "compiling"
            result["cases"] = cases
            return result
        finally:
            connection.close()

    def finish_job(self, job_id: int, execution: Any) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE jobs SET status=?,finished_at=?,compile_status=?,compile_stdout=?,
                   compile_stderr=?,compile_exit_code=?,compile_wall_time_ms=? WHERE id=?""",
                (
                    execution.status, iso(), execution.compile.status,
                    execution.compile.stdout, execution.compile.stderr,
                    execution.compile.exit_code, execution.compile.wall_time_ms, job_id,
                ),
            )
            completed_positions = set()
            for case in execution.cases:
                completed_positions.add(case.position)
                connection.execute(
                    """UPDATE job_cases SET status=?,stdout=?,stderr=?,wall_time_ms=?,
                       peak_memory_kib=?,exit_code=?,term_signal=?,output_truncated=?
                       WHERE job_id=? AND position=?""",
                    (
                        case.status, case.stdout, case.stderr, case.wall_time_ms,
                        case.peak_memory_kib, case.exit_code, case.term_signal,
                        int(case.output_truncated), job_id, case.position,
                    ),
                )
            connection.execute(
                """UPDATE job_cases SET status='cancelled',stderr=?
                   WHERE job_id=? AND status='queued'""",
                (
                    "not run because compilation did not complete"
                    if execution.compile.status != "completed"
                    else "submission ended before this case started",
                    job_id,
                ),
            )

    def mark_job_system_error(self, job_id: int, message: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE jobs SET status='system_error',finished_at=?,compile_status='system_error',
                   compile_stderr=? WHERE id=?""",
                (iso(), message[:16_384], job_id),
            )
            connection.execute(
                "UPDATE job_cases SET status='cancelled',stderr=? WHERE job_id=? AND status='queued'",
                ("runner internal error", job_id),
            )

    def list_jobs_admin(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT j.id,j.user_id,u.username,j.filename,j.compiler,j.time_limit_ms,
                          j.memory_limit_mib,j.status,j.created_at,j.started_at,j.finished_at,
                          j.cancel_requested
                   FROM jobs j JOIN users u ON u.id=j.user_id
                   ORDER BY j.id DESC LIMIT ?""",
                (limit,),
            )]

    def cancel_job_admin(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return None
            if row["status"] == "queued":
                connection.execute(
                    "UPDATE jobs SET status='cancelled',cancel_requested=1,finished_at=? WHERE id=?",
                    (iso(), job_id),
                )
                connection.execute(
                    "UPDATE job_cases SET status='cancelled' WHERE job_id=?", (job_id,)
                )
            elif row["status"] in {"compiling", "running"}:
                connection.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
        return self.get_job(job_id)

    def is_cancel_requested(self, job_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested,status FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            return bool(row and (row["cancel_requested"] or row["status"] == "cancelled"))

    def cancel_job(self, job_id: int, user_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)
            ).fetchone()
            if not row:
                return None
            if row["status"] == "queued":
                connection.execute(
                    """UPDATE jobs SET status='cancelled',cancel_requested=1,finished_at=?
                       WHERE id=?""",
                    (iso(), job_id),
                )
                connection.execute(
                    "UPDATE job_cases SET status='cancelled',stderr='cancelled by user' WHERE job_id=?",
                    (job_id,),
                )
            elif row["status"] in {"compiling", "running"}:
                connection.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
        return self.get_job(job_id, user_id)

    def get_job(self, job_id: int, user_id: int | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM jobs WHERE id=?"
        params: list[Any] = [job_id]
        if user_id is not None:
            query += " AND user_id=?"
            params.append(user_id)
        with self.connect() as connection:
            job = connection.execute(query, params).fetchone()
            if not job:
                return None
            result = dict(job)
            result["cases"] = [dict(row) for row in connection.execute(
                "SELECT * FROM job_cases WHERE job_id=? ORDER BY position", (job_id,)
            )]
        result["case_count"] = len(result["cases"])
        return result

    def create_extension(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO extension_catalog(extension_id,version,sha256,created_at)
                   VALUES(?,?,?,?)""",
                (payload["extension_id"], payload["version"], payload["sha256"], iso()),
            )
            row = connection.execute(
                "SELECT * FROM extension_catalog WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
            return dict(row)

    def list_extensions(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM extension_catalog WHERE enabled=1 ORDER BY extension_id,version"
            )]

    def request_extension(self, user_id: int, catalog_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT id FROM extension_catalog WHERE id=? AND enabled=1", (catalog_id,)
            ).fetchone()
            if not exists:
                raise LookupError("extension not in approved catalog")
            cursor = connection.execute(
                """INSERT INTO extension_requests(user_id,catalog_id,status,created_at)
                   VALUES(?,?,'pending',?)""",
                (user_id, catalog_id, iso()),
            )
            row = connection.execute(
                "SELECT * FROM extension_requests WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
            return dict(row)

    def decide_extension_request(
        self, request_id: int, admin_id: int, scope: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            updated = connection.execute(
                """UPDATE extension_requests SET status='approved',scope=?,decided_at=?,decided_by=?
                   WHERE id=? AND status='pending'""",
                (scope, iso(), admin_id, request_id),
            ).rowcount
            if updated != 1:
                return None
            row = connection.execute(
                "SELECT * FROM extension_requests WHERE id=?", (request_id,)
            ).fetchone()
            return dict(row)

    def list_extension_requests(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT r.*,u.username,c.extension_id,c.version FROM extension_requests r
                   JOIN users u ON u.id=r.user_id JOIN extension_catalog c ON c.id=r.catalog_id
                   ORDER BY r.created_at DESC"""
            )]

    def create_grant(self, user_id: int, actor_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        expires_at = None
        if payload.get("expires_in_seconds"):
            expires_at = iso(utcnow() + timedelta(seconds=payload["expires_in_seconds"]))
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO quota_grants
                   (user_id,memory_limit_mib,time_limit_ms,max_inflight,remaining_uses,
                    expires_at,created_at,created_by) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    user_id, payload.get("memory_limit_mib"), payload.get("time_limit_ms"),
                    payload.get("max_inflight"), payload.get("remaining_uses"),
                    expires_at, iso(), actor_id,
                ),
            )
            grant_id = cursor.lastrowid
            row = connection.execute("SELECT * FROM quota_grants WHERE id=?", (grant_id,)).fetchone()
        return dict(row)

    def get_grant(self, grant_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM quota_grants WHERE id=?", (grant_id,)
            ).fetchone()
            return dict(row) if row else None

    def effective_limits(
        self, user_id: int, connection: sqlite3.Connection | None = None
    ) -> dict[str, int]:
        own = connection is None
        connection = connection or self.connect()
        try:
            rows = connection.execute(
                """SELECT * FROM quota_grants WHERE user_id=? AND active=1
                   AND (expires_at IS NULL OR expires_at>?)
                   AND (remaining_uses IS NULL OR remaining_uses>0)""",
                (user_id, iso()),
            ).fetchall()
            return {
                "memory_limit_mib": max([256] + [r["memory_limit_mib"] for r in rows if r["memory_limit_mib"]]),
                "time_limit_ms": max([60_000] + [r["time_limit_ms"] for r in rows if r["time_limit_ms"]]),
                "max_inflight": max([MAX_USER_INFLIGHT] + [r["max_inflight"] for r in rows if r["max_inflight"]]),
            }
        finally:
            if own:
                connection.close()

    def audit(
        self, actor_id: int | None, action: str, target_type: str | None = None,
        target_id: str | None = None, details: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO audit_log(actor_user_id,action,target_type,target_id,
                   details_json,created_at) VALUES(?,?,?,?,?,?)""",
                (actor_id, action, target_type, target_id, json.dumps(details or {}), iso()),
            )

    def list_audit(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT 500"
            )]
