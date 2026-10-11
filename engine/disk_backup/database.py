from __future__ import annotations

import sqlite3
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS volumes (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    root TEXT NOT NULL,
    kind TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id TEXT NOT NULL REFERENCES volumes(id),
    mode TEXT NOT NULL CHECK(mode IN ('incremental','full')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    file_count INTEGER NOT NULL DEFAULT 0,
    byte_count INTEGER NOT NULL DEFAULT 0,
    hashed_bytes INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds REAL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS files (
    volume_id TEXT NOT NULL REFERENCES volumes(id),
    relative_path TEXT NOT NULL,
    name TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT,
    last_seen_scan INTEGER NOT NULL REFERENCES scans(id),
    last_verified_at TEXT,
    PRIMARY KEY(volume_id, relative_path)
);
CREATE INDEX IF NOT EXISTS files_size_idx ON files(volume_id, size);
CREATE INDEX IF NOT EXISTS files_hash_idx ON files(volume_id, sha256);

CREATE TABLE IF NOT EXISTS backup_jobs (
    id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    target_volume_id TEXT NOT NULL REFERENCES volumes(id),
    target_root TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    total_files INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    completed_files INTEGER NOT NULL DEFAULT 0,
    completed_bytes INTEGER NOT NULL DEFAULT 0,
    copied_files INTEGER NOT NULL DEFAULT 0,
    skipped_files INTEGER NOT NULL DEFAULT 0,
    failed_files INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS backup_items (
    job_id TEXT NOT NULL REFERENCES backup_jobs(id),
    source_path TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT,
    target_path TEXT,
    duplicate_of TEXT,
    status TEXT NOT NULL,
    error TEXT,
    cleanup_status TEXT NOT NULL DEFAULT 'not_requested',
    PRIMARY KEY(job_id, source_path)
);
CREATE INDEX IF NOT EXISTS backup_items_status_idx ON backup_items(job_id, status);

CREATE TABLE IF NOT EXISTS metrics (
    operation TEXT PRIMARY KEY,
    units_per_second REAL NOT NULL,
    updated_at TEXT NOT NULL
);
"""

CURRENT_SCHEMA_VERSION = 4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Catalog:
    def __init__(self, path: Path):
        self.path = path
        existed = path.is_file() and path.stat().st_size > 0
        self._protection_handle: int | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, timeout=60)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        previous_version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if existed and previous_version < CURRENT_SCHEMA_VERSION:
            self.create_backup(f"before-v{CURRENT_SCHEMA_VERSION}")
        self._migrate()
        self.verify_integrity()
        if existed:
            self.create_automatic_backup_if_due()
        self._protect_active_database()

    def close(self) -> None:
        self._connection.close()
        if self._protection_handle is not None:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._protection_handle)
            self._protection_handle = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._connection
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def upsert_volume(self, volume_id: str, label: str, root: str, kind: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO volumes(id,label,root,kind,last_seen_at) VALUES(?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET label=excluded.label,root=excluded.root,
                kind=excluded.kind,last_seen_at=excluded.last_seen_at""",
                (volume_id, label, root, kind, utc_now()),
            )

    def estimate_rate(self, operation: str, fallback: float) -> float:
        row = self.connection.execute(
            "SELECT units_per_second FROM metrics WHERE operation=?", (operation,)
        ).fetchone()
        return float(row[0]) if row else fallback

    def record_rate(self, operation: str, rate: float) -> None:
        if rate <= 0:
            return
        previous = self.connection.execute(
            "SELECT units_per_second FROM metrics WHERE operation=?", (operation,)
        ).fetchone()
        smoothed = rate if not previous else (float(previous[0]) * 0.7 + rate * 0.3)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO metrics(operation,units_per_second,updated_at) VALUES(?,?,?)
                ON CONFLICT(operation) DO UPDATE SET units_per_second=excluded.units_per_second,
                updated_at=excluded.updated_at""",
                (operation, smoothed, utc_now()),
            )

    @property
    def backup_directory(self) -> Path:
        return self.path.parent / "catalog_backups"

    def create_backup(self, label: str = "manual") -> Path:
        """Create a transactionally consistent SQLite backup beside the live state folder."""
        safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-") or "backup"
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = self.backup_directory / f"catalog-{safe_label}-{timestamp}.sqlite3"
        backup = sqlite3.connect(destination)
        try:
            self.connection.backup(backup)
            result = str(backup.execute("PRAGMA quick_check").fetchone()[0])
            if result.lower() != "ok":
                raise RuntimeError(f"Catalog backup integrity check failed: {result}")
        finally:
            backup.close()
        return destination

    def create_automatic_backup_if_due(self) -> Path | None:
        date_stamp = datetime.now().strftime("%Y%m%d")
        if self.backup_directory.is_dir() and any(
            self.backup_directory.glob(f"catalog-auto-{date_stamp}-*.sqlite3")
        ):
            return None
        return self.create_backup("auto")

    def verify_integrity(self) -> None:
        result = str(self.connection.execute("PRAGMA quick_check").fetchone()[0])
        if result.lower() != "ok":
            raise RuntimeError(f"Catalog integrity check failed: {result}")

    def catalog_backups(self) -> list[Path]:
        if not self.backup_directory.is_dir():
            return []
        return sorted(
            self.backup_directory.glob("catalog-*.sqlite3"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )

    def _protect_active_database(self) -> None:
        """On Windows, deny delete/rename sharing while retaining normal SQLite read/write access."""
        import os

        if os.name != "nt":
            return
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateFileW.restype = ctypes.c_void_p
        handle = kernel32.CreateFileW(
            str(self.path.resolve()),
            0x80000000,  # GENERIC_READ
            0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE (not DELETE)
            None,
            3,  # OPEN_EXISTING
            0x80,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in (None, invalid):
            raise OSError("Windows could not protect the active catalog from deletion.")
        self._protection_handle = int(handle)

    def _migrate(self) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version >= CURRENT_SCHEMA_VERSION:
            return
        with self.transaction() as connection:
            if version < 1:
                self._add_column(connection, "files", "extension", "TEXT NOT NULL DEFAULT ''")
                self._add_column(connection, "files", "category", "TEXT NOT NULL DEFAULT 'other'")
                self._add_column(connection, "backup_jobs", "selection_json", "TEXT")
                self._add_column(connection, "backup_items", "source_name", "TEXT")
                self._add_column(connection, "backup_items", "extension", "TEXT NOT NULL DEFAULT ''")
                self._add_column(connection, "backup_items", "category", "TEXT NOT NULL DEFAULT 'other'")
            if version < 2:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS cleanup_sessions (
                        id TEXT PRIMARY KEY,
                        source_device_id TEXT NOT NULL,
                        source_kind TEXT NOT NULL,
                        source_ref TEXT NOT NULL,
                        queue_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        threshold_bytes INTEGER NOT NULL,
                        limit_count INTEGER NOT NULL,
                        target_volume_id TEXT,
                        target_root TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS cleanup_items (
                        session_id TEXT NOT NULL REFERENCES cleanup_sessions(id),
                        source_path TEXT NOT NULL,
                        source_name TEXT NOT NULL,
                        size INTEGER NOT NULL,
                        mtime_ns INTEGER NOT NULL,
                        extension TEXT NOT NULL,
                        category TEXT NOT NULL,
                        sha256 TEXT,
                        proof_path TEXT,
                        rank INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        action TEXT,
                        error TEXT,
                        freed_bytes INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(session_id, source_path)
                    );
                    CREATE INDEX IF NOT EXISTS cleanup_items_status_idx
                    ON cleanup_items(session_id,status,rank);
                    """
                )
            if version < 3:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS restore_jobs (
                        id TEXT PRIMARY KEY,
                        backup_job_id TEXT NOT NULL REFERENCES backup_jobs(id),
                        target_device_id TEXT NOT NULL,
                        target_kind TEXT NOT NULL,
                        target_root TEXT NOT NULL,
                        status TEXT NOT NULL,
                        selection_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        total_files INTEGER NOT NULL DEFAULT 0,
                        total_bytes INTEGER NOT NULL DEFAULT 0,
                        restored_files INTEGER NOT NULL DEFAULT 0,
                        skipped_files INTEGER NOT NULL DEFAULT 0,
                        conflict_files INTEGER NOT NULL DEFAULT 0,
                        failed_files INTEGER NOT NULL DEFAULT 0,
                        completed_bytes INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS restore_items (
                        restore_id TEXT NOT NULL REFERENCES restore_jobs(id),
                        original_source_path TEXT NOT NULL,
                        proof_path TEXT NOT NULL,
                        restore_path TEXT NOT NULL,
                        source_name TEXT NOT NULL,
                        size INTEGER NOT NULL,
                        mtime_ns INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        extension TEXT NOT NULL,
                        category TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        error TEXT,
                        restored_at TEXT,
                        PRIMARY KEY(restore_id, original_source_path)
                    );
                    CREATE INDEX IF NOT EXISTS restore_jobs_status_idx
                    ON restore_jobs(status,updated_at);
                    CREATE INDEX IF NOT EXISTS restore_items_status_idx
                    ON restore_items(restore_id,status,category);
                    """
                )
            if version < 4:
                self._add_column(connection, "scans", "origin", "TEXT NOT NULL DEFAULT 'index'")
                connection.execute(
                    """UPDATE scans SET origin='backup' WHERE elapsed_seconds=0
                    AND mode='incremental' AND file_count=1"""
                )
            connection.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
        self._backfill_file_metadata()

    @staticmethod
    def _add_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _backfill_file_metadata(self) -> None:
        from .media import classify_name

        rows = self.connection.execute(
            "SELECT volume_id,relative_path,name FROM files WHERE extension=''"
        ).fetchall()
        if not rows:
            return
        values = []
        for row in rows:
            extension, category = classify_name(str(row["name"]))
            values.append((extension, category, row["volume_id"], row["relative_path"]))
        with self.transaction() as connection:
            connection.executemany(
                "UPDATE files SET extension=?,category=? WHERE volume_id=? AND relative_path=?",
                values,
            )
