from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOCAL_PROFILE_ID = "local"
SCHEMA_VERSION = 1


class RevisionConflict(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"unsupported database schema version: {version}")
            connection.execute("BEGIN IMMEDIATE")
            for statement in (
                """CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS settings (
                    profile_id TEXT PRIMARY KEY REFERENCES profiles(id),
                    revision INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL REFERENCES profiles(id),
                    state TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    end_reason TEXT
                )""",
                """CREATE TABLE IF NOT EXISTS session_summaries (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(id),
                    frames_received INTEGER NOT NULL DEFAULT 0,
                    frames_processed INTEGER NOT NULL DEFAULT 0,
                    frames_dropped INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )""",
            ):
                connection.execute(statement)
            connection.execute(
                "INSERT OR IGNORE INTO profiles (id, created_at) VALUES (?, ?)",
                (LOCAL_PROFILE_ID, utc_now()),
            )
            if version < SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.execute(
                "UPDATE sessions SET state = 'interrupted', ended_at = ?, end_reason = 'process_restart' "
                "WHERE state IN ('active', 'stopping')",
                (utc_now(),),
            )

    def read_settings(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT revision, payload FROM settings WHERE profile_id = ?", (LOCAL_PROFILE_ID,)
            ).fetchone()
        if row is None:
            return {"revision": 0, "payload": {}}
        return {"revision": row["revision"], "payload": json.loads(row["payload"])}

    def save_settings(self, expected_revision: int, payload: dict[str, Any]) -> dict[str, Any]:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM settings WHERE profile_id = ?", (LOCAL_PROFILE_ID,)
            ).fetchone()
            current_revision = 0 if row is None else row["revision"]
            if current_revision != expected_revision:
                raise RevisionConflict(f"expected {expected_revision}, found {current_revision}")
            next_revision = current_revision + 1
            connection.execute(
                "INSERT INTO settings (profile_id, revision, payload, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(profile_id) DO UPDATE SET revision = excluded.revision, "
                "payload = excluded.payload, updated_at = excluded.updated_at",
                (LOCAL_PROFILE_ID, next_revision, serialized, utc_now()),
            )
        return {"revision": next_revision, "payload": payload}

    def start_session(self, session_id: str) -> None:
        now = utc_now()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO sessions (id, profile_id, state, started_at) VALUES (?, ?, 'active', ?)",
                (session_id, LOCAL_PROFILE_ID, now),
            )
            connection.execute(
                "INSERT INTO session_summaries (session_id, updated_at) VALUES (?, ?)",
                (session_id, now),
            )

    def finish_session(
        self,
        session_id: str,
        frames_received: int,
        frames_processed: int,
        frames_dropped: int,
        reason: str,
    ) -> None:
        now = utc_now()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE sessions SET state = 'ended', ended_at = ?, end_reason = ? "
                "WHERE id = ? AND state IN ('active', 'stopping')",
                (now, reason, session_id),
            ).rowcount
            if changed:
                connection.execute(
                    "UPDATE session_summaries SET frames_received = ?, frames_processed = ?, "
                    "frames_dropped = ?, updated_at = ? WHERE session_id = ?",
                    (frames_received, frames_processed, frames_dropped, now, session_id),
                )

    def list_sessions(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute("""
                SELECT s.id, s.state, s.started_at, s.ended_at, s.end_reason,
                       m.frames_received, m.frames_processed, m.frames_dropped
                FROM sessions s JOIN session_summaries m ON m.session_id = s.id
                WHERE s.profile_id = ? ORDER BY s.started_at DESC, s.id DESC
            """, (LOCAL_PROFILE_ID,)).fetchall()
        return [dict(row) for row in rows]
